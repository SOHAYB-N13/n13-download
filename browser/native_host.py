#!/usr/bin/env python3
"""Chrome Native Messaging host — silent N13 launcher.

The browser extension calls this host via ``chrome.runtime.sendNativeMessage``
to start the N13 GUI *without* Chrome's "Open N13 Download Manager?" protocol
dialog.  The host is registered per-user (HKCU registry) by
``browser.protocol.register_native_host`` — no admin rights required.

Native messaging protocol (stdio):
  - request : 4-byte little-endian length + UTF-8 JSON
  - response: same framing, exactly one message, then exit

CRITICAL: nothing may ever be written to stdout except the framed response —
any stray print corrupts the channel.  Diagnostics go to a temp log file.
"""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import traceback
from pathlib import Path

NATIVE_HOST_NAME = "com.n13.download_manager"

# Chrome spawns this script with an arbitrary working directory, so the project
# root is NOT on sys.path by default (sys.path[0] is browser/).  Without this,
# every launch failed with ModuleNotFoundError and the extension fell back to
# the dldm:// protocol — resurfacing Chrome's "Open N13 Download Manager?" dialog.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _log(message: str) -> None:
    """Best-effort diagnostic logging (never touches stdout)."""
    try:
        log_path = Path(tempfile.gettempdir()) / "n13_native_host.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except OSError:
        pass


def read_message() -> dict:
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return {}
    (length,) = struct.unpack("<I", raw)
    if length <= 0 or length > 1 << 20:  # 1 MiB sanity cap
        return {}
    payload = sys.stdin.buffer.read(length)
    try:
        msg = json.loads(payload.decode("utf-8"))
        return msg if isinstance(msg, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def send_message(message: dict) -> None:
    data = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def handle(msg: dict) -> dict:
    action = msg.get("action", "")

    if action == "ping":
        return {"ok": True, "pong": True}

    if action == "launch":
        # Reuse the exact GUI launch flow the dldm:// protocol handler uses,
        # so the started instance auto-starts the authenticated Live Server.
        from browser.dldm_handler import _launch_n13

        _launch_n13(Path(_PROJECT_ROOT), sys.executable)
        return {"ok": True, "launched": True}

    return {"ok": False, "error": f"unknown action: {action!r}"}


def main() -> int:
    try:
        msg = read_message()
        if not msg:
            return 0  # empty/invalid frame — exit quietly
        send_message(handle(msg))
    except Exception as exc:  # noqa: BLE001 — must never crash loudly
        _log(f"[{NATIVE_HOST_NAME}] error: {exc}\n{traceback.format_exc()}")
        try:
            send_message({"ok": False, "error": str(exc)})
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
