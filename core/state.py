"""Persistent download state for resume.

Beyond the byte layout, the state records the *identity* of the resource it
belongs to (``ETag`` and ``Last-Modified``).  A resume that only compares the
URL and the total size will happily append the first half of yesterday's file
to the second half of today's when the publisher re-uploads a file of the same
length — the result is the right size and the wrong content.  The validators
are what make "these bytes are still the same bytes" a question the engine can
actually answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from core.parts import DownloadPart


@dataclass
class ResumeState:
    """One saved download, as read back from disk."""

    url: str
    total_size: int
    num_threads: int
    parts: List[DownloadPart] = field(default_factory=list)
    etag: str = ""
    last_modified: str = ""
    layout: str = "parts"       # "parts" (.partN sidecars) | "direct" (final file)

    def identity_matches(self, etag: str, last_modified: str) -> Optional[bool]:
        """Whether a freshly observed resource is the one this state belongs to.

        ``None`` means "cannot tell" — the state predates validator capture, or
        the server does not send them on ranged responses.  Callers treat that
        as "proceed", because refusing every resume from an older state file
        would strand perfectly good partial downloads.
        """
        if self.etag and etag:
            return self.etag == etag
        if self.last_modified and last_modified:
            return self.last_modified == last_modified
        return None


class DownloadState:
    def __init__(self, state_path: Path):
        self.state_path = state_path

    def save(
        self,
        url: str,
        total_size: int,
        parts: List[DownloadPart],
        num_threads: int,
        etag: str = "",
        last_modified: str = "",
        layout: str = "parts",
    ) -> None:
        """Persist state atomically.

        Uses a temporary file + rename so a crash mid-write never leaves a
        zero-byte or partial state file.  The entire serialisation is wrapped
        in a try/except: if json.dump raises (e.g. a non-serialisable path
        type slipped in) we clean up the temp file and raise so the caller
        knows the save failed rather than silently overwriting good state.
        """
        data = {
            "url": url,
            "total_size": total_size,
            "num_threads": num_threads,
            "etag": etag or "",
            "last_modified": last_modified or "",
            "layout": layout or "parts",
            "parts": [
                {
                    "index": p.index,
                    "start": p.start,
                    "end": p.end,
                    "path": str(p.path),
                    "done": p.done or p.is_complete,
                    "shared": bool(p.shared),
                    "written": p.snapshot(),
                }
                for p in parts
            ],
            "timestamp": datetime.now().isoformat(),
        }
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.state_path)
        except Exception:
            # Clean up the temp file so we never leave a corrupt artefact.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def load(self) -> Optional[ResumeState]:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            parts = [
                DownloadPart(
                    index=p["index"],
                    start=p["start"],
                    end=p["end"],
                    path=Path(p["path"]),
                    done=p.get("done", False),
                    shared=bool(p.get("shared", False)),
                    written=max(0, int(p.get("written", 0) or 0)),
                )
                for p in data["parts"]
            ]
            num_threads = int(data.get("num_threads", len(parts)))
            return ResumeState(
                url=data["url"],
                total_size=int(data["total_size"]),
                num_threads=num_threads,
                parts=parts,
                # Absent in state files written before validators were captured.
                etag=str(data.get("etag") or ""),
                last_modified=str(data.get("last_modified") or ""),
                layout=str(data.get("layout") or "parts"),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def delete(self) -> None:
        if self.state_path.exists():
            self.state_path.unlink()
