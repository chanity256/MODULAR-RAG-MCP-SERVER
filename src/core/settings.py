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
    "ChunkRefinerConfig",
    "MetadataEnricherConfig",
    "ImageCaptionerConfig",
    "IngestionConfig",
    "IngestionRetryConfig",
    "IngestionVersionPolicyConfig",
    "IngestionSourceFiltersConfig",
    "resolve_ingestion_versions",
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
class ChunkRefinerConfig:
    """Chunk 精炼配置（阶段 C5 引入）。

    默认 ``use_llm=False``（仅规则去噪）；打开后经 LLMFactory 创建 LLM
    做智能重写，失败自动降级回规则结果。
    """

    use_llm: bool = False
    prompt_path: str = "config/prompts/chunk_refinement.txt"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetadataEnricherConfig:
    """元数据增强配置（阶段 C6 引入，可选节）。

    默认 ``use_llm=False``（规则兜底生成 title/summary/tags）；
    打开后经 LLM 生成语义元数据，失败降级回规则结果。
    """

    use_llm: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageCaptionerConfig:
    """图片描述生成配置（阶段 C7 引入，可选节）。

    默认 ``enabled=False``（保留 image_refs 并标记
    ``has_unprocessed_images``，不阻塞 ingestion）；打开后经 Vision LLM
    生成 caption 写入 ``metadata["image_captions"]``，单图失败降级。
    """

    enabled: bool = False
    prompt_path: str = "config/prompts/image_captioning.txt"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestionRetryConfig:
    """摄取失败重试配置（INGESTION_ROBUSTNESS_SPEC 第 6 章，可选节）。

    缺省 ``max_attempts=1`` 即不重试，行为与基线一致；``>1`` 时仅对可重试
    错误按 ``initial_backoff_s * backoff_multiplier**n``（±``backoff_jitter``
    比例抖动）退避。
    """

    max_attempts: int = 1
    initial_backoff_s: float = 2.0
    backoff_multiplier: float = 2.0
    backoff_jitter: float = 0.2
    extra: dict[str, Any] = field(default_factory=dict)


#: ``version_policy`` 三个版本字段取该值时由 ``resolve_ingestion_versions`` 推导。
VERSION_AUTO = "auto"


@dataclass
class IngestionVersionPolicyConfig:
    """版本元数据与不兼容检测配置（可选节）。

    三个版本字段为 ``auto`` 时按以下口径推导（见 ``resolve_ingestion_versions``），
    否则原样使用显式字符串：

    - ``parser_version``：``auto`` → 实际使用的 Loader 类名（未知时 ``unknown``）。
    - ``chunk_version``：``auto`` → ``{splitter.provider}/{chunk_size}/{chunk_overlap}/refine={llm|rule}``。
    - ``embedding_version``：``auto`` → ``{embedding.provider}/{embedding.model}/{dimensions|default}``。

    ``on_embedding_mismatch``：``stale`` = 告警并跳过；``rebuild`` = 提示需全量重建
    （均不自动重建）。
    """

    parser_version: str = VERSION_AUTO
    chunk_version: str = VERSION_AUTO
    embedding_version: str = VERSION_AUTO
    on_embedding_mismatch: str = "stale"
    extra: dict[str, Any] = field(default_factory=dict)


# Note: 过滤先于扩展名筛选等取舍 — 见
# .agents/notes/implemented/feature/2026-10-07-source-filters-before-extension.md
@dataclass
class IngestionSourceFiltersConfig:
    """目录扫描 include/exclude 过滤配置（INGESTION_ROBUSTNESS_SPEC 3.8.2，可选节）。

    glob 列表语义：``include_patterns`` 空 = 全部受理；``exclude_patterns``
    优先于 include。缺省 exclude 与 Obsidian 场景对齐，排除噪音目录。
    """

    include_patterns: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(
        default_factory=lambda: [".obsidian/**", ".trash/**", "templates/**"]
    )
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestionConfig:
    """Ingestion Pipeline 配置（可选节，缺失时走默认值）。"""

    chunk_refiner: ChunkRefinerConfig = field(default_factory=ChunkRefinerConfig)
    metadata_enricher: MetadataEnricherConfig = field(default_factory=MetadataEnricherConfig)
    image_captioner: ImageCaptionerConfig = field(default_factory=ImageCaptionerConfig)
    # 编码批大小（C10 BatchProcessor 使用；批次内单次调用底层 provider）。
    batch_size: int = 32
    retry: IngestionRetryConfig = field(default_factory=IngestionRetryConfig)
    version_policy: IngestionVersionPolicyConfig = field(
        default_factory=IngestionVersionPolicyConfig
    )
    source_filters: IngestionSourceFiltersConfig = field(
        default_factory=IngestionSourceFiltersConfig
    )
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgenticSearchConfig:
    """Agentic 检索（Sirchmunk HTTP sidecar）配置（可选节，S1.1）。

    默认 ``enabled=False``：零加载、``tools/list`` 不出现 agentic 工具，
    保证向后兼容。``base_url`` 指向独立进程 ``sirchmunk serve``（绑定
    127.0.0.1 + Bearer Token）；主 venv 永不 import sirchmunk
    （SIRCHMUNK_INTEGRATION_SPEC 第 3/4 章）。
    """

    enabled: bool = False
    backend: str = "sirchmunk_http"
    base_url: str = "http://127.0.0.1:8584"
    api_token: str | None = None
    mode: str = "FAST"  # FAST | DEEP（DEEP 另受 allow_deep 门控）
    allow_deep: bool = False
    # httpx 总超时（秒）；须低于 MCP 客户端的工具超时。
    timeout_s: float = 30.0
    top_k_files: int = 5
    max_loops: int = 6
    max_token_budget: int = 32000
    # ---- 第三路触发策略（S2.2；策略实现见 query_engine/agentic_trigger.py）----
    # tool_only：永不自动触发（默认，安全值）；fallback：主路信号不足时触发；
    # always：每次查询都触发（仅实验/评估）。
    trigger: str = "tool_only"
    # fallback 判定的 top 分数下限。cross-encoder 分数是未校准 logits，
    # 此默认值仅是占位、未经校准；阈值必须在独立校准集上标定，
    # 禁止用评估 golden set 调参（DEV_SPEC 9.5 纪律）。
    trigger_score_threshold: float = 0.5
    # fallback 判定的结果数下限（融合后结果数低于该值视为信号不足）。
    trigger_min_results: int = 1
    # PathGuard 白名单根目录（同时是证据过滤边界；enabled 时必须非空）。
    allowed_roots: list[str] = field(default_factory=list)
    # 白名单内的强制排除子串（fail-closed，宁可误拒）。
    denied_substrings: list[str] = field(default_factory=lambda: [".env", ".git", "data/db"])
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
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)
    agentic_search: AgenticSearchConfig = field(default_factory=AgenticSearchConfig)
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


def _build_ingestion(sec: dict[str, Any]) -> IngestionConfig:
    """ingestion 为可选节，缺失时由调用方传空 dict 走默认值。"""
    cr_sec = sec.get("chunk_refiner") or {}
    if not isinstance(cr_sec, dict):
        raise ConfigError("config section 'ingestion.chunk_refiner' must be a mapping")
    cr_known = {"use_llm", "prompt_path"}
    refiner = ChunkRefinerConfig(
        use_llm=bool(_opt(cr_sec, "use_llm", False)),
        prompt_path=_opt(cr_sec, "prompt_path", "config/prompts/chunk_refinement.txt"),
        extra=_extra(cr_sec, cr_known),
    )
    me_sec = sec.get("metadata_enricher") or {}
    if not isinstance(me_sec, dict):
        raise ConfigError("config section 'ingestion.metadata_enricher' must be a mapping")
    enricher = MetadataEnricherConfig(
        use_llm=bool(_opt(me_sec, "use_llm", False)),
        extra=_extra(me_sec, {"use_llm"}),
    )
    ic_sec = sec.get("image_captioner") or {}
    if not isinstance(ic_sec, dict):
        raise ConfigError("config section 'ingestion.image_captioner' must be a mapping")
    captioner = ImageCaptionerConfig(
        enabled=bool(_opt(ic_sec, "enabled", False)),
        prompt_path=_opt(ic_sec, "prompt_path", "config/prompts/image_captioning.txt"),
        extra=_extra(ic_sec, {"enabled", "prompt_path"}),
    )
    raw_batch_size = _opt(sec, "batch_size", 32)
    if not isinstance(raw_batch_size, int) or isinstance(raw_batch_size, bool) or raw_batch_size < 1:
        raise ConfigError("config field 'ingestion.batch_size' must be a positive integer")
    return IngestionConfig(
        chunk_refiner=refiner,
        metadata_enricher=enricher,
        image_captioner=captioner,
        batch_size=raw_batch_size,
        retry=_build_ingestion_retry(sec.get("retry")),
        version_policy=_build_ingestion_version_policy(sec.get("version_policy")),
        source_filters=_build_ingestion_source_filters(sec.get("source_filters")),
        extra=_extra(
            sec,
            {
                "chunk_refiner", "metadata_enricher", "image_captioner", "batch_size",
                "retry", "version_policy", "source_filters",
            },
        ),
    )


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _build_ingestion_retry(raw: Any) -> IngestionRetryConfig:
    """ingestion.retry 为可选节；整体或单字段缺失均走默认值。"""
    sec = raw or {}
    if not isinstance(sec, dict):
        raise ConfigError("config section 'ingestion.retry' must be a mapping")
    max_attempts = _opt(sec, "max_attempts", 1)
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ConfigError("config field 'ingestion.retry.max_attempts' must be an integer >= 1")
    initial_backoff_s = _opt(sec, "initial_backoff_s", 2.0)
    if not _is_number(initial_backoff_s) or initial_backoff_s < 0:
        raise ConfigError(
            "config field 'ingestion.retry.initial_backoff_s' must be a non-negative number"
        )
    backoff_multiplier = _opt(sec, "backoff_multiplier", 2.0)
    if not _is_number(backoff_multiplier) or backoff_multiplier <= 1:
        raise ConfigError(
            "config field 'ingestion.retry.backoff_multiplier' must be a number > 1"
        )
    backoff_jitter = _opt(sec, "backoff_jitter", 0.2)
    if not _is_number(backoff_jitter) or not 0.0 <= float(backoff_jitter) < 1.0:
        raise ConfigError(
            "config field 'ingestion.retry.backoff_jitter' must be a number in [0, 1)"
        )
    known = {"max_attempts", "initial_backoff_s", "backoff_multiplier", "backoff_jitter"}
    return IngestionRetryConfig(
        max_attempts=max_attempts,
        initial_backoff_s=float(initial_backoff_s),
        backoff_multiplier=float(backoff_multiplier),
        backoff_jitter=float(backoff_jitter),
        extra=_extra(sec, known),
    )


def _build_ingestion_version_policy(raw: Any) -> IngestionVersionPolicyConfig:
    """ingestion.version_policy 为可选节；整体或单字段缺失均走默认值。"""
    sec = raw or {}
    if not isinstance(sec, dict):
        raise ConfigError("config section 'ingestion.version_policy' must be a mapping")
    versions: dict[str, str] = {}
    for key in ("parser_version", "chunk_version", "embedding_version"):
        value = _opt(sec, key, VERSION_AUTO)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(
                f"config field 'ingestion.version_policy.{key}' must be a non-empty string"
            )
        versions[key] = value.strip()
    on_mismatch = _opt(sec, "on_embedding_mismatch", "stale")
    if not isinstance(on_mismatch, str) or on_mismatch not in {"stale", "rebuild"}:
        raise ConfigError(
            "config field 'ingestion.version_policy.on_embedding_mismatch' must be "
            f"stale/rebuild, got {on_mismatch!r}"
        )
    known = {"parser_version", "chunk_version", "embedding_version", "on_embedding_mismatch"}
    return IngestionVersionPolicyConfig(
        **versions,
        on_embedding_mismatch=on_mismatch,
        extra=_extra(sec, known),
    )


def _build_ingestion_source_filters(raw: Any) -> IngestionSourceFiltersConfig:
    """ingestion.source_filters 为可选节；整体或单字段缺失均走默认值。"""
    sec = raw or {}
    if not isinstance(sec, dict):
        raise ConfigError("config section 'ingestion.source_filters' must be a mapping")
    known = {"include_patterns", "exclude_patterns"}
    fields: dict[str, list[str]] = {}
    for key, default in (
        ("include_patterns", []),
        ("exclude_patterns", [".obsidian/**", ".trash/**", "templates/**"]),
    ):
        value = _opt(sec, key, default)
        if not isinstance(value, list):
            raise ConfigError(
                f"config field 'ingestion.source_filters.{key}' must be a list of strings"
            )
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ConfigError(
                    f"config field 'ingestion.source_filters.{key}' "
                    f"must contain non-empty strings, got {item!r}"
                )
        fields[key] = list(value)
    return IngestionSourceFiltersConfig(
        include_patterns=fields["include_patterns"],
        exclude_patterns=fields["exclude_patterns"],
        extra=_extra(sec, known),
    )


def resolve_ingestion_versions(
    settings: Settings, *, loader_name: str | None = None
) -> dict[str, str]:
    """按 ``ingestion.version_policy`` 推导当次运行的三个版本标识。

    ``auto`` 口径见 ``IngestionVersionPolicyConfig`` docstring；``loader_name``
    由调用方传入实际使用的 Loader 类名（仅用于 ``parser_version=auto``）。

    Returns:
        ``{"parser_version", "chunk_version", "embedding_version"}``，
        可直接作为 ``mark_success`` / ``mark_failed`` 的关键字参数展开。
    """
    policy = settings.ingestion.version_policy
    parser = policy.parser_version
    if parser == VERSION_AUTO:
        parser = loader_name or "unknown"
    chunk = policy.chunk_version
    if chunk == VERSION_AUTO:
        splitter = settings.splitter
        refine = "llm" if settings.ingestion.chunk_refiner.use_llm else "rule"
        chunk = (
            f"{splitter.provider}/{splitter.chunk_size}/{splitter.chunk_overlap}/refine={refine}"
        )
    embedding = policy.embedding_version
    if embedding == VERSION_AUTO:
        emb = settings.embedding
        embedding = f"{emb.provider}/{emb.model}/{emb.dimensions or 'default'}"
    return {
        "parser_version": parser,
        "chunk_version": chunk,
        "embedding_version": embedding,
    }


def _build_agentic_search(sec: dict[str, Any]) -> AgenticSearchConfig:
    """agentic_search 为可选节，缺失时由调用方传空 dict 走默认值。"""
    enabled = bool(_opt(sec, "enabled", False))
    backend = _opt(sec, "backend", "sirchmunk_http")
    if not isinstance(backend, str) or not backend.strip():
        raise ConfigError("config field 'agentic_search.backend' must be a non-empty string")
    base_url = _opt(sec, "base_url", "http://127.0.0.1:8584")
    if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
        raise ConfigError(
            "config field 'agentic_search.base_url' must be an http(s) URL, got %r" % (base_url,)
        )
    mode = _opt(sec, "mode", "FAST")
    if not isinstance(mode, str) or mode.upper() not in {"FAST", "DEEP"}:
        raise ConfigError(
            f"config field 'agentic_search.mode' must be FAST or DEEP, got {mode!r}"
        )
    timeout_s = _opt(sec, "timeout_s", 30.0)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ConfigError("config field 'agentic_search.timeout_s' must be a positive number")
    allowed_roots_raw = _opt(sec, "allowed_roots", [])
    if not isinstance(allowed_roots_raw, list) or any(
        not isinstance(r, str) or not r.strip() for r in allowed_roots_raw
    ):
        raise ConfigError(
            "config field 'agentic_search.allowed_roots' must be a list of non-empty strings"
        )
    denied_raw = _opt(sec, "denied_substrings", [".env", ".git", "data/db"])
    if not isinstance(denied_raw, list) or any(not isinstance(d, str) for d in denied_raw):
        raise ConfigError(
            "config field 'agentic_search.denied_substrings' must be a list of strings"
        )

    def _positive_int(key: str, default: int) -> int:
        value = _opt(sec, key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"config field 'agentic_search.{key}' must be a positive integer")
        return value

    top_k_files = _positive_int("top_k_files", 5)
    max_loops = _positive_int("max_loops", 6)
    max_token_budget = _positive_int("max_token_budget", 32000)

    # 触发策略（S2.2）：策略名非法 fail-fast；阈值/下限字段即使当前
    # 策略用不到也照常校验，保证配置整体一致。
    trigger = _opt(sec, "trigger", "tool_only")
    if not isinstance(trigger, str) or trigger not in {"tool_only", "fallback", "always"}:
        raise ConfigError(
            f"config field 'agentic_search.trigger' must be tool_only/fallback/always, "
            f"got {trigger!r}"
        )
    trigger_score_threshold = _opt(sec, "trigger_score_threshold", 0.5)
    if isinstance(trigger_score_threshold, bool) or not isinstance(
        trigger_score_threshold, (int, float)
    ) or not 0.0 <= float(trigger_score_threshold) <= 1.0:
        raise ConfigError(
            "config field 'agentic_search.trigger_score_threshold' must be a number in [0, 1]"
        )
    trigger_min_results = _opt(sec, "trigger_min_results", 1)
    if isinstance(trigger_min_results, bool) or not isinstance(trigger_min_results, int) or (
        trigger_min_results < 0
    ):
        raise ConfigError(
            "config field 'agentic_search.trigger_min_results' must be a non-negative integer"
        )


    # 跨字段要求放最后：先保证各字段自身格式合法，再校验组合约束。
    if enabled and not allowed_roots_raw:
        raise ConfigError(
            "config field 'agentic_search.allowed_roots' must be non-empty when enabled=true"
        )

    known = {
        "enabled", "backend", "base_url", "api_token", "mode", "allow_deep",
        "timeout_s", "top_k_files", "max_loops", "max_token_budget",
        "trigger", "trigger_score_threshold", "trigger_min_results",
        "allowed_roots", "denied_substrings",
    }
    return AgenticSearchConfig(
        enabled=enabled,
        backend=backend,
        base_url=base_url,
        api_token=_opt(sec, "api_token"),
        mode=mode.upper(),
        allow_deep=bool(_opt(sec, "allow_deep", False)),
        timeout_s=float(timeout_s),
        top_k_files=top_k_files,
        max_loops=max_loops,
        max_token_budget=max_token_budget,
        trigger=trigger,
        trigger_score_threshold=float(trigger_score_threshold),
        trigger_min_results=trigger_min_results,
        allowed_roots=list(allowed_roots_raw),
        denied_substrings=list(denied_raw),
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
    ingestion_sec = data.get("ingestion") or {}
    agentic_sec = data.get("agentic_search") or {}
    if not isinstance(splitter_sec, dict):
        raise ConfigError("config section 'splitter' must be a mapping")
    if not isinstance(obs_sec, dict):
        raise ConfigError("config section 'observability' must be a mapping")
    if not isinstance(dashboard_sec, dict):
        raise ConfigError("config section 'dashboard' must be a mapping")
    if not isinstance(ingestion_sec, dict):
        raise ConfigError("config section 'ingestion' must be a mapping")
    if not isinstance(agentic_sec, dict):
        raise ConfigError("config section 'agentic_search' must be a mapping")
    splitter = _build_splitter(splitter_sec)
    observability = _build_observability(obs_sec)
    dashboard = _build_dashboard(dashboard_sec)
    ingestion = _build_ingestion(ingestion_sec)
    agentic_search = _build_agentic_search(agentic_sec)

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
        ingestion=ingestion,
        agentic_search=agentic_search,
        raw=data,
    )
    validate_settings(settings)
    return settings
