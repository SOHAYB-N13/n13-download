#!/usr/bin/env python3
"""Standalone protocol handler — uses unique temp files per invocation.

Receives a dldm:// URL from the OS, decodes it, writes it to a temp file, and
launches the main download script in a new console (Windows) or background
process (POSIX).

Robustness notes:
- The encoded URL may arrive percent-encoded one or more times by the browser
  / shell, so we decode until stable.
- Windows command lines need careful quoting; we pass the URL via a temp file
  (never on the command line) to avoid issues with ``&``, ``%``, quotes and
  spaces.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import urllib.parse
import uuid
from pathlib import Path


def decode_url(arg: str) -> str:
    """Strip the dldm:// scheme and percent-decode the payload."""
    if arg.startswith("dldm://"):
        encoded = arg[len("dldm://"):]
    elif arg.startswith("dldm:"):
        encoded = arg[len("dldm:"):]
    else:
        encoded = arg
    # Browsers sometimes append a trailing slash; strip it before decoding.
    encoded = encoded.rstrip("/")
    prev = encoded
    # Decode repeatedly to handle double-encoding from the shell chain.
    for _ in range(5):
        decoded = urllib.parse.unquote(prev)
        if decoded == prev:
            break
        prev = decoded
    return prev.strip()


def main() -> int:
    if len(sys.argv) < 2:
        return 1

    url = decode_url(sys.argv[1])
    if not url.startswith(("http://", "https://")):
        # Write a tiny diagnostic so silent failures can be traced.
        try:
            log_path = Path(tempfile.gettempdir()) / "dldm_handler.log"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Rejected (not http(s)): {sys.argv[1]!r} -> {url!r}\n")
        except OSError:
            pass
        return 1

    project_root = Path(__file__).resolve().parent.parent
    main_script = project_root / "d.py"
    python_exe = sys.executable

    url_file = Path(tempfile.gettempdir()) / f"dldm_url_{uuid.uuid4().hex}.txt"
    url_file.write_text(url, encoding="utf-8")

    if sys.platform == "win32":
        bat_path = Path(tempfile.gettempdir()) / f"dldm_run_{uuid.uuid4().hex}.bat"
        # Quote paths defensively; % must be doubled inside a .bat file.
        bat_content = (
            "@echo off\n"
            "chcp 65001 >nul 2>&1\n"
            f'"{python_exe}" "{main_script}" --from-browser --url-file "{url_file}"\n'
            f'del "{url_file}" 2>nul\n'
            'del "%~f0" 2>nul\n'
        )
        bat_path.write_text(bat_content, encoding="utf-8")
        subprocess.Popen(
            ["cmd", "/c", str(bat_path)],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            cwd=str(project_root),
        )
    else:
        subprocess.Popen(
            [python_exe, str(main_script), "--from-browser", "--url-file", str(url_file)],
            cwd=str(project_root),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
