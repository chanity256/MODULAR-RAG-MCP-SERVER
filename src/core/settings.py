"""配置加载与校验（阶段 A3）。

集中放置 ``Settings`` 数据结构与加载/校验逻辑：
- ``load_settings(path) -> Settings``：读取 YAML -> 解析为 Settings -> 校验必填字段。
- ``validate_settings(settings) -> None``：把"必填字段检查"集中化，错误信息包含字段路径。
- ``Settings`` 仅做结构与最小校验，不在这里做任何网络/IO 的"业务初始化"。

设计原则：
- fail-fast：缺失关键字段时立即抛出可读错误（明确指出缺的是哪个字段路径）。
- 配置驱动：后续 Libs/Core 各工厂读取 ``Settings`` 决定具体实现。
- 环境变量引用：形如 ``"${ENV_VAR}"`` 的值在加载时解析为对应环境变量值（缺失时保留原值并记录）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "ConfigError",
    "LLMConfig",
    "EmbeddingConfig",
    "VisionLLMConfig",
    "VectorStoreConfig",
    "SplitterConfig",
    "RetrievalConfig",
    "RerankConfig",
    "EvaluationConfig",
    "ObservabilityConfig",
    "DashboardConfig",
    "Settings",
    "load_settings",
    "validate_settings",
]

# 形如 ${VAR_NAME} 的环境变量引用。
_ENV_REF_PATTERN = re.compile(r"^\$\{(?P<name>[A-Z_][A-Z0-9_]*)\}$")


class ConfigError(ValueError):
    """配置错误，``message`` 中包含出错字段的路径（例如 ``embedding.provider``）。"""


# ---------------------------------------------------------------------------
# 嵌套配置数据类
# ---------------------------------------------------------------------------


@dataclass
class LLMConfig:
    provider: str
    model: str
    # provider-specific 可选字段（阶段 B 各 provider 实现会按 provider 读取）
    azure_endpoint: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    deployment_name: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EmbeddingConfig:
    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    azure_endpoint: str | None = None
    api_version: str | None = None
    dimensions: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class VisionLLMConfig:
    provider: str
    model: str
    azure_endpoint: str | None = None
    api_key: str | None = None
    api_version: str | None = None
    deployment_name: str | None = None
    max_image_size: int = 2048
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class VectorStoreConfig:
    backend: str
    persist_path: str = "./data/db/chroma"
    collection: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SplitterConfig:
    """文档切分配置（阶段 B3 引入）。

    默认 provider=recursive（对应 B7.5 的 RecursiveCharacterTextSplitter）。
    若配置缺失该节，``load_settings`` 会以默认值兜底，保证向后兼容。
    """

    provider: str = "recursive"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    separators: list[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalConfig:
    sparse_backend: str
    fusion_algorithm: str
    top_k_dense: int = 20
    top_k_sparse: int = 20
    top_k_final: int = 10
    rrf_k: int = 60
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RerankConfig:
    backend: str
    model: str | None = None
    top_m: int = 30
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationConfig:
    backends: list[str]
    golden_test_set: str = "./tests/fixtures/golden_test_set.json"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ObservabilityConfig:
    enabled: bool = True
    log_file: str = "./logs/traces.jsonl"
    log_level: str = "INFO"
    detail_level: str = "standard"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DashboardConfig:
    enabled: bool = True
    port: int = 8501
    traces_dir: str = "./logs"
    auto_refresh: bool = True
    refresh_interval: int = 5
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Settings:
    """全局配置聚合，供各工厂/模块读取。"""

    llm: LLMConfig
    embedding: EmbeddingConfig
    vision_llm: VisionLLMConfig
    vector_store: VectorStoreConfig
    splitter: SplitterConfig
    retrieval: RetrievalConfig
    rerank: RerankConfig
    evaluation: EvaluationConfig
    observability: ObservabilityConfig
    dashboard: DashboardConfig
    # 原始配置（便于后续模块读取未显式建模的字段）
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# 加载与校验
# ---------------------------------------------------------------------------

# 每个顶层节内必填的字段（缺失即报错）。值为 None 表示该节本身可选。
_REQUIRED_FIELDS: dict[str, list[str]] = {
    "llm": ["provider", "model"],
    "embedding": ["provider", "model"],
    "vision_llm": ["provider", "model"],
    "vector_store": ["backend"],
    "retrieval": ["sparse_backend", "fusion_algorithm"],
    "rerank": ["backend"],
    "evaluation": ["backends"],
    # observability / dashboard 均有合理默认值，不强制必填
}


def _resolve_env(value: Any) -> Any:
    """解析 ``${ENV_VAR}`` 形式的环境变量引用；未匹配则原样返回。"""
    if isinstance(value, str):
        match = _ENV_REF_PATTERN.match(value)
        if match:
            env_name = match.group("name")
            return os.environ.get(env_name, value)
    return value


def _require_section(data: dict[str, Any], section: str) -> dict[str, Any]:
    sec = data.get(section)
    if sec is None:
        raise ConfigError(f"missing required config section: {section}")
    if not isinstance(sec, dict):
        raise ConfigError(f"config section '{section}' must be a mapping, got {type(sec).__name__}")
    return sec


def _require_field(section: str, sec: dict[str, Any], field_name: str) -> Any:
    if field_name not in sec:
        raise ConfigError(f"missing required field: {section}.{field_name}")
    value = sec[field_name]
    if value is None or (isinstance(value, str) and value.strip() == ""):
        raise ConfigError(f"empty required field: {section}.{field_name}")
    return _resolve_env(value)


def _opt(section: dict[str, Any], key: str, default: Any = None) -> Any:
    if key in section and section[key] is not None:
        return _resolve_env(section[key])
    return default


def _extra(section: dict[str, Any], known: set[str]) -> dict[str, Any]:
    return {k: _resolve_env(v) for k, v in section.items() if k not in known}


def _build_llm(sec: dict[str, Any]) -> LLMConfig:
    known = {
        "provider", "model", "azure_endpoint", "api_key", "base_url",
        "deployment_name", "temperature", "max_tokens",
    }
    return LLMConfig(
        provider=_require_field("llm", sec, "provider"),
        model=_require_field("llm", sec, "model"),
        azure_endpoint=_opt(sec, "azure_endpoint"),
        api_key=_opt(sec, "api_key"),
        base_url=_opt(sec, "base_url"),
        deployment_name=_opt(sec, "deployment_name"),
        temperature=float(_opt(sec, "temperature", 0.0)),
        max_tokens=_opt(sec, "max_tokens"),
        extra=_extra(sec, known),
    )


def _build_embedding(sec: dict[str, Any]) -> EmbeddingConfig:
    known = {
        "provider", "model", "api_key", "base_url",
        "azure_endpoint", "api_version", "dimensions",
    }
    return EmbeddingConfig(
        provider=_require_field("embedding", sec, "provider"),
        model=_require_field("embedding", sec, "model"),
        api_key=_opt(sec, "api_key"),
        base_url=_opt(sec, "base_url"),
        azure_endpoint=_opt(sec, "azure_endpoint"),
        api_version=_opt(sec, "api_version"),
        dimensions=_opt(sec, "dimensions"),
        extra=_extra(sec, known),
    )


def _build_vision_llm(sec: dict[str, Any]) -> VisionLLMConfig:
    known = {
        "provider", "model", "azure_endpoint", "api_key",
        "api_version", "deployment_name", "max_image_size",
    }
    return VisionLLMConfig(
        provider=_require_field("vision_llm", sec, "provider"),
        model=_require_field("vision_llm", sec, "model"),
        azure_endpoint=_opt(sec, "azure_endpoint"),
        api_key=_opt(sec, "api_key"),
        api_version=_opt(sec, "api_version"),
        deployment_name=_opt(sec, "deployment_name"),
        max_image_size=int(_opt(sec, "max_image_size", 2048)),
        extra=_extra(sec, known),
    )


def _build_vector_store(sec: dict[str, Any]) -> VectorStoreConfig:
    known = {"backend", "persist_path", "collection"}
    return VectorStoreConfig(
        backend=_require_field("vector_store", sec, "backend"),
        persist_path=_opt(sec, "persist_path", "./data/db/chroma"),
        collection=_opt(sec, "collection"),
        extra=_extra(sec, known),
    )


def _build_splitter(sec: dict[str, Any]) -> SplitterConfig:
    """splitter 为可选节，缺失时由调用方传空 dict 走默认值。"""
    known = {"provider", "chunk_size", "chunk_overlap", "separators"}
    return SplitterConfig(
        provider=_opt(sec, "provider", "recursive"),
        chunk_size=int(_opt(sec, "chunk_size", 1000)),
        chunk_overlap=int(_opt(sec, "chunk_overlap", 200)),
        separators=_opt(sec, "separators"),
        extra=_extra(sec, known),
    )


def _build_retrieval(sec: dict[str, Any]) -> RetrievalConfig:
    known = {
        "sparse_backend", "fusion_algorithm",
        "top_k_dense", "top_k_sparse", "top_k_final", "rrf_k",
    }
    return RetrievalConfig(
        sparse_backend=_require_field("retrieval", sec, "sparse_backend"),
        fusion_algorithm=_require_field("retrieval", sec, "fusion_algorithm"),
        top_k_dense=int(_opt(sec, "top_k_dense", 20)),
        top_k_sparse=int(_opt(sec, "top_k_sparse", 20)),
        top_k_final=int(_opt(sec, "top_k_final", 10)),
        rrf_k=int(_opt(sec, "rrf_k", 60)),
        extra=_extra(sec, known),
    )


def _build_rerank(sec: dict[str, Any]) -> RerankConfig:
    known = {"backend", "model", "top_m"}
    return RerankConfig(
        backend=_require_field("rerank", sec, "backend"),
        model=_opt(sec, "model"),
        top_m=int(_opt(sec, "top_m", 30)),
        extra=_extra(sec, known),
    )


def _build_evaluation(sec: dict[str, Any]) -> EvaluationConfig:
    backends = _require_field("evaluation", sec, "backends")
    if not isinstance(backends, list) or not backends:
        raise ConfigError("invalid field: evaluation.backends must be a non-empty list")
    known = {"backends", "golden_test_set"}
    return EvaluationConfig(
        backends=list(backends),
        golden_test_set=_opt(sec, "golden_test_set", "./tests/fixtures/golden_test_set.json"),
        extra=_extra(sec, known),
    )


def _build_observability(sec: dict[str, Any]) -> ObservabilityConfig:
    logging_sec = sec.get("logging", {}) if isinstance(sec.get("logging"), dict) else {}
    known = {"enabled", "log_file", "logging", "detail_level"}
    return ObservabilityConfig(
        enabled=bool(_opt(sec, "enabled", True)),
        log_file=_opt(sec, "log_file", "./logs/traces.jsonl"),
        log_level=_opt(logging_sec, "log_level", "INFO"),
        detail_level=_opt(sec, "detail_level", "standard"),
        extra=_extra(sec, known),
    )


def _build_dashboard(sec: dict[str, Any]) -> DashboardConfig:
    known = {"enabled", "port", "traces_dir", "auto_refresh", "refresh_interval"}
    return DashboardConfig(
        enabled=bool(_opt(sec, "enabled", True)),
        port=int(_opt(sec, "port", 8501)),
        traces_dir=_opt(sec, "traces_dir", "./logs"),
        auto_refresh=bool(_opt(sec, "auto_refresh", True)),
        refresh_interval=int(_opt(sec, "refresh_interval", 5)),
        extra=_extra(sec, known),
    )


def validate_settings(settings: Settings) -> None:
    """集中化的必填字段检查。

    ``load_settings`` 在构造时已做了一次必填校验；本函数对外暴露同样的检查能力，
    便于在配置被修改后或测试中独立调用。任何缺失/非法字段均抛出 ``ConfigError``，
    错误信息包含字段路径（例如 ``embedding.provider``）。
    """
    # 重新对每个已建模字段做非空/类型断言（构造时已校验，这里做防御性二次检查）。
    checks: list[tuple[str, Any]] = [
        ("llm.provider", settings.llm.provider),
        ("llm.model", settings.llm.model),
        ("embedding.provider", settings.embedding.provider),
        ("embedding.model", settings.embedding.model),
        ("vision_llm.provider", settings.vision_llm.provider),
        ("vision_llm.model", settings.vision_llm.model),
        ("vector_store.backend", settings.vector_store.backend),
        ("retrieval.sparse_backend", settings.retrieval.sparse_backend),
        ("retrieval.fusion_algorithm", settings.retrieval.fusion_algorithm),
        ("rerank.backend", settings.rerank.backend),
    ]
    for path, value in checks:
        if value is None or (isinstance(value, str) and value.strip() == ""):
            raise ConfigError(f"empty required field: {path}")
    if not settings.evaluation.backends:
        raise ConfigError("invalid field: evaluation.backends must be a non-empty list")


def load_settings(path: str | os.PathLike[str] = "config/settings.yaml") -> Settings:
    """读取 YAML -> 解析为 Settings -> 校验必填字段。

    Args:
        path: 配置文件路径，默认 ``config/settings.yaml``。

    Returns:
        ``Settings`` 对象。

    Raises:
        ConfigError: 配置文件缺失、不可解析或缺少必填字段时。
        FileNotFoundError: 配置文件不存在时（由调用方决定是否转成 ConfigError）。
    """
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse YAML config {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping, got {type(data).__name__}")

    # 逐节构建并做必填校验。
    llm = _build_llm(_require_section(data, "llm"))
    embedding = _build_embedding(_require_section(data, "embedding"))
    vision_llm = _build_vision_llm(_require_section(data, "vision_llm"))
    vector_store = _build_vector_store(_require_section(data, "vector_store"))
    retrieval = _build_retrieval(_require_section(data, "retrieval"))
    rerank = _build_rerank(_require_section(data, "rerank"))
    evaluation = _build_evaluation(_require_section(data, "evaluation"))

    # 可选节：缺失时使用默认值。
    splitter_sec = data.get("splitter") or {}
    obs_sec = data.get("observability") or {}
    dashboard_sec = data.get("dashboard") or {}
    if not isinstance(splitter_sec, dict):
        raise ConfigError("config section 'splitter' must be a mapping")
    if not isinstance(obs_sec, dict):
        raise ConfigError("config section 'observability' must be a mapping")
    if not isinstance(dashboard_sec, dict):
        raise ConfigError("config section 'dashboard' must be a mapping")
    splitter = _build_splitter(splitter_sec)
    observability = _build_observability(obs_sec)
    dashboard = _build_dashboard(dashboard_sec)

    settings = Settings(
        llm=llm,
        embedding=embedding,
        vision_llm=vision_llm,
        vector_store=vector_store,
        splitter=splitter,
        retrieval=retrieval,
        rerank=rerank,
        evaluation=evaluation,
        observability=observability,
        dashboard=dashboard,
        raw=data,
    )
    validate_settings(settings)
    return settings
