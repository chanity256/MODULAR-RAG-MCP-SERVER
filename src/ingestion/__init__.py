"""Ingestion 包（阶段 C2-C14）。

- ``ingestion.pipeline``：``IngestionPipeline`` MVP 流程编排
  （integrity→load→split→transform→encode→store，阶段 C14）。
- 子包：``chunking``（C4）/ ``transform``（C5-C7）/ ``embedding``（C8-C10）/
  ``storage``（C11-C13）。
"""

from ingestion.pipeline import (
    IngestionPipeline,
    IngestionPipelineError,
    IngestionResult,
    ProgressCallback,
)

__all__ = [
    "IngestionPipeline",
    "IngestionPipelineError",
    "IngestionResult",
    "ProgressCallback",
]
