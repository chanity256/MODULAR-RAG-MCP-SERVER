"""Ingestion Transform 模块（阶段 C5-C7）。

- ``ChunkRefiner``：规则去噪 + 可选 LLM 智能重写（失败降级不阻塞）。
- ``MetadataEnricher``：title/summary/tags 元数据注入（规则兜底 + LLM 增强）。
- ``ImageCaptioner``：Vision LLM 图片 caption 生成（禁用/失败降级不阻塞）。
"""

from ingestion.transform.base_transform import BaseTransform
from ingestion.transform.chunk_refiner import ChunkRefiner
from ingestion.transform.image_captioner import ImageCaptioner
from ingestion.transform.metadata_enricher import MetadataEnricher

__all__ = ["BaseTransform", "ChunkRefiner", "MetadataEnricher", "ImageCaptioner"]
