"""Persistent download state for resume."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from core.parts import DownloadPart


class DownloadState:
    def __init__(self, state_path: Path):
        self.state_path = state_path

    def save(
        self,
        url: str,
        total_size: int,
        parts: List[DownloadPart],
        num_threads: int,
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
            "parts": [
                {
                    "index": p.index,
                    "start": p.start,
                    "end": p.end,
                    "path": str(p.path),
                    "done": p.done or p.is_complete,
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

    def load(self) -> Optional[Tuple[str, int, int, List[DownloadPart]]]:
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
                )
                for p in data["parts"]
            ]
            num_threads = int(data.get("num_threads", len(parts)))
            return data["url"], int(data["total_size"]), num_threads, parts
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def delete(self) -> None:
        if self.state_path.exists():
            self.state_path.unlink()
