"""Remote PaddleOCR-VL-1.6 OCR engine.

★ INTERFACE UNCHANGED — migrate the previous implementation here as-is.
See docs/LEGACY-DESIGN.md §4.2.

Public surface to preserve:
  - class OCREngine
  - get_engine(job_url=None, token=None, **kwargs) -> OCREngine
  - set_progress_callback(cb)
  - clean_ocr_markdown_text(text) -> str
  - optionalPayload = {useDocOrientationClassify, useDocUnwarping,
                       useChartRecognition, visualize=False}
  - upload throttling (SOURCE_OCR_UPLOAD_INTERVAL_SEC), concurrent polling,
    >99-page PDF split + stitch.

TODO (step 2): paste migrated implementation.
"""

from __future__ import annotations


def get_engine(job_url: str | None = None, token: str | None = None, **kwargs):
    raise NotImplementedError("ocr_engine — migrate from previous engine in step 2")
