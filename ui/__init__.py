"""N13 Download Manager — UI package.

WebView-based desktop interface.
"""

from ui.common import (
    TaskManager, TaskSnapshot, TaskState, DownloadRequest,
    human_size, format_eta,
)

__all__ = [
    "TaskManager", "TaskSnapshot", "TaskState", "DownloadRequest",
    "human_size", "format_eta",
]
