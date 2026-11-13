"""F5：Pipeline 进度回调 on_progress 测试。"""

from __future__ import annotations

from unittest.mock import Mock

from core.trace.trace_context import TraceContext
from tests.unit.test_ingestion_pipeline import (
    FakeLoader,
    FakeVectorStore,
    _build_pipeline,
    _write_source,
)


def _run(tmp_path, on_progress):
    pipeline, _ = _build_pipeline(tmp_path, FakeLoader(tmp_path), FakeVectorStore())
    source = _write_source(tmp_path)
    result = pipeline.run(str(source), on_progress=on_progress)
    return result


def test_progress_callback_called_for_all_stages(tmp_path):
    callback = Mock()
    result = _run(tmp_path, callback)

    assert result.status == "success"
    assert callback.call_count > 0
    stage_names = [call.args[0] for call in callback.call_args_list]
    # 五个阶段（03-tech-stack 契约）均至少上报一次。
    for expected in ("load", "split", "transform", "embed", "upsert"):
        assert expected in stage_names, f"stage {expected} not reported"


def test_progress_callback_arguments_shape(tmp_path):
    callback = Mock()
    _run(tmp_path, callback)

    for call in callback.call_args_list:
        stage, current, total = call.args
        assert isinstance(stage, str) and stage
        assert isinstance(current, int) and isinstance(total, int)
        assert 1 <= current <= total


def test_progress_embed_reports_batch_granularity(tmp_path):
    callback = Mock()
    _run(tmp_path, callback)

    embed_calls = [c.args for c in callback.call_args_list if c.args[0] == "embed"]
    assert embed_calls
    last_current, last_total = embed_calls[-1][1], embed_calls[-1][2]
    assert last_current == last_total  # 收尾时 embed 全部批次完成


def test_progress_callback_none_behaves_identically(tmp_path):
    """on_progress=None 不影响摄取结果（与不传完全一致）。"""
    with_none = _run(tmp_path, None)
    assert with_none.status == "success" and with_none.chunks > 0


def test_progress_callback_exception_isolated(tmp_path):
    """回调抛错不应中断摄取（_emit_progress 内部消化）。"""
    result = _run(tmp_path, Mock(side_effect=RuntimeError("cb boom")))
    assert result.status == "success"
