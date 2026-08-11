from __future__ import annotations

"""
Backward-compatible re-export.

The real task lives in `app.workers.content_processor.process_content_task`
and is registered as `reforge.process_content`.
"""

from app.workers.content_processor import (  # noqa: F401
    analyze_media_task,
    generate_variants_task,
    launch_content_processing,
    process_content_task,
)

