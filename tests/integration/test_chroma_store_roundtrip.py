"""ChromaStore 集成测试（阶段 B7.6）。

使用真实 ChromaDB（嵌入式 PersistentClient）完成完整的 upsert → query roundtrip。
每个测试用独立临时目录持久化，测试结束后自动清理。

覆盖：
- provider=chroma 时 VectorStoreFactory 可创建。
- 基本 upsert + query roundtrip。
- 向量查询返回结果按 score 降序。
- top_k 参数限制返回数量。
- metadata filters（where 子句）过滤。
- 幂等 upsert（相同 id 覆盖，count 不增长）。
- 空输入安全处理。
- 持久化：重开实例可读到已有数据。
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from core.settings import load_settings
from libs.vector_store.base_vector_store import QueryResult, VectorRecord
from libs.vector_store.chroma_store import ChromaStore
from libs.vector_store.vector_store_factory import VectorStoreFactory

_VALID_YAML = dedent(
    """\
    llm:
      provider: azure
      model: gpt-4o
    embedding:
      provider: openai
      model: text-embedding-3-small
    vision_llm:
      provider: azure
      model: gpt-4o
    vector_store:
      backend: chroma
      persist_path: ./data/db/chroma
    retrieval:
      sparse_backend: bm25
      fusion_algorithm: rrf
    rerank:
      backend: none
    evaluation:
      backends: [custom]
    """
)


def _settings(tmp_path: Path, **vs_overrides):
    path = tmp_path / "settings.yaml"
    path.write_text(_VALID_YAML, encoding="utf-8")
    settings = load_settings(path)
    if vs_overrides:
        settings = replace(settings, vector_store=replace(settings.vector_store, **vs_overrides))
    return settings


@pytest.fixture
def store(tmp_path: Path):
    """每个测试用独立临时 Chroma 目录。"""
    persist = tmp_path / "chroma_db"
    settings = _settings(tmp_path, persist_path=str(persist))
    s = ChromaStore(settings.vector_store, collection="test_col")
    yield s
    # 清理：删除临时目录
    shutil.rmtree(persist, ignore_errors=True)


@pytest.fixture
def factory_store(tmp_path: Path):
    """通过工厂创建的实例。"""
    persist = tmp_path / "chroma_db"
    settings = _settings(tmp_path, persist_path=str(persist))
    import libs.vector_store  # noqa: F401 — 触发注册
    s = VectorStoreFactory.create(settings, collection="factory_col")
    yield s
    shutil.rmtree(persist, ignore_errors=True)


# ---------------------------------------------------------------------------
# 工厂创建
# ---------------------------------------------------------------------------


def test_factory_creates_chroma(tmp_path: Path, factory_store):
    assert isinstance(factory_store, ChromaStore)


def test_factory_registered_backends():
    import libs.vector_store  # noqa: F401

    assert "chroma" in VectorStoreFactory.available_backends()


# ---------------------------------------------------------------------------
# 基本 upsert + query roundtrip
# ---------------------------------------------------------------------------


def test_basic_upsert_and_query(store):
    records = [
        VectorRecord(id="r1", vector=[1.0, 0.0, 0.0], text="apple", metadata={"category": "fruit"}),
        VectorRecord(id="r2", vector=[0.0, 1.0, 0.0], text="carrot", metadata={"category": "vegetable"}),
        VectorRecord(id="r3", vector=[0.0, 0.0, 1.0], text="banana", metadata={"category": "fruit"}),
    ]
    count = store.upsert(records)
    assert count == 3
    assert store.count() == 3

    results = store.query([1.0, 0.0, 0.0], top_k=3)
    assert len(results) == 3
    assert results[0].id == "r1"
    assert results[0].text == "apple"
    assert results[0].metadata["category"] == "fruit"
    # score 应为正且越高越相关
    assert results[0].score >= results[1].score


def test_query_returns_sorted_by_score_desc(store):
    records = [
        VectorRecord(id="a", vector=[1.0, 0.0], text="a"),
        VectorRecord(id="b", vector=[0.9, 0.1], text="b"),
        VectorRecord(id="c", vector=[0.0, 1.0], text="c"),
    ]
    store.upsert(records)
    results = store.query([1.0, 0.0], top_k=3)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_query_results_are_queryresult_instances(store):
    store.upsert([VectorRecord(id="x", vector=[1.0, 0.0], text="hello")])
    results = store.query([1.0, 0.0], top_k=1)
    assert len(results) == 1
    assert isinstance(results[0], QueryResult)
    assert results[0].id == "x"
    assert results[0].text == "hello"


# ---------------------------------------------------------------------------
# top_k 参数
# ---------------------------------------------------------------------------


def test_top_k_limits_results(store):
    records = [VectorRecord(id=f"r{i}", vector=[float(i), 0.0], text=f"r{i}") for i in range(5)]
    store.upsert(records)
    results = store.query([0.0, 0.0], top_k=2)
    assert len(results) == 2


def test_top_k_zero_returns_empty(store):
    store.upsert([VectorRecord(id="x", vector=[1.0, 0.0], text="x")])
    results = store.query([1.0, 0.0], top_k=0)
    assert results == []


# ---------------------------------------------------------------------------
# metadata filters
# ---------------------------------------------------------------------------


def test_metadata_filter_basic(store):
    records = [
        VectorRecord(id="r1", vector=[1.0, 0.0], text="apple", metadata={"category": "fruit"}),
        VectorRecord(id="r2", vector=[0.0, 1.0], text="carrot", metadata={"category": "vegetable"}),
        VectorRecord(id="r3", vector=[1.0, 1.0], text="banana", metadata={"category": "fruit"}),
    ]
    store.upsert(records)
    results = store.query([1.0, 0.0], top_k=10, filters={"category": "fruit"})
    assert len(results) == 2
    assert all(r.metadata["category"] == "fruit" for r in results)


def test_metadata_filter_no_match(store):
    records = [
        VectorRecord(id="r1", vector=[1.0, 0.0], text="apple", metadata={"category": "fruit"}),
    ]
    store.upsert(records)
    results = store.query([1.0, 0.0], top_k=10, filters={"category": "nonexistent"})
    assert len(results) == 0


# ---------------------------------------------------------------------------
# 幂等 upsert
# ---------------------------------------------------------------------------


def test_upsert_idempotent_same_id_overwrites(store):
    r1 = VectorRecord(id="dup", vector=[1.0, 0.0], text="original", metadata={"v": 1})
    store.upsert([r1])
    assert store.count() == 1
    r2 = VectorRecord(id="dup", vector=[0.0, 1.0], text="updated", metadata={"v": 2})
    store.upsert([r2])
    assert store.count() == 1  # 不增长
    results = store.query([0.0, 1.0], top_k=1)
    assert results[0].id == "dup"
    assert results[0].text == "updated"
    assert results[0].metadata["v"] == 2


def test_upsert_empty_records_returns_zero(store):
    assert store.upsert([]) == 0
    assert store.count() == 0


# ---------------------------------------------------------------------------
# 持久化：重开实例可读已有数据
# ---------------------------------------------------------------------------


def test_persistence_across_instances(tmp_path: Path):
    persist = tmp_path / "chroma_persist"
    settings = _settings(tmp_path, persist_path=str(persist))
    s1 = ChromaStore(settings.vector_store, collection="persist_col")
    s1.upsert([VectorRecord(id="p1", vector=[1.0, 0.0], text="persisted")])
    assert s1.count() == 1

    # 重新创建实例，指向同一目录
    s2 = ChromaStore(settings.vector_store, collection="persist_col")
    assert s2.count() == 1
    results = s2.query([1.0, 0.0], top_k=1)
    assert results[0].id == "p1"
    assert results[0].text == "persisted"
    shutil.rmtree(persist, ignore_errors=True)


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------


def test_query_non_list_vector_raises(store):
    with pytest.raises(ValueError):
        store.query("not a list", top_k=1)  # type: ignore[arg-type]


def test_upsert_non_list_records_raises(store):
    with pytest.raises(ValueError):
        store.upsert("not a list")  # type: ignore[arg-type]
