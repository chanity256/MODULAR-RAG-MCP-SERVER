"""冒烟测试：校验关键顶层包可被导入。

对应 DEV_SPEC 阶段 A2：建立 pytest 运行基座，至少 1 个冒烟测试做关键包 import 校验。
后续阶段的测试在此基础上增量补充。
"""

from __future__ import annotations


def test_import_top_level_packages() -> None:
    """五个顶层包（对应 src/ 下的目录）均可导入。"""
    import importlib

    for name in ["mcp_server", "core", "ingestion", "libs", "observability"]:
        importlib.import_module(name)


def test_observability_logger_get_logger() -> None:
    """占位 logger 可用且不抛异常。"""
    from observability.logger import get_logger

    logger = get_logger("smoke-test")
    assert logger is not None
    logger.info("smoke test logger works")


def test_config_settings_yaml_loadable() -> None:
    """config/settings.yaml 可被 PyYAML 解析。"""
    from pathlib import Path

    import yaml

    config_path = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
    assert config_path.exists(), f"配置文件缺失: {config_path}"

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    # 阶段 A 最小校验：关键顶层配置节存在。
    for key in ["llm", "embedding", "vector_store", "retrieval", "rerank"]:
        assert key in data, f"settings.yaml 缺少顶层字段: {key}"


def test_prompt_files_exist() -> None:
    """三个 prompt 占位文件存在且非空。"""
    from pathlib import Path

    prompts_dir = Path(__file__).resolve().parents[2] / "config" / "prompts"
    for name in ["image_captioning.txt", "chunk_refinement.txt", "rerank.txt"]:
        path = prompts_dir / name
        assert path.exists(), f"prompt 文件缺失: {path}"
        assert path.stat().st_size > 0, f"prompt 文件为空: {path}"
