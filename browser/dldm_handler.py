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
from typing import Optional


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


def _launch_n13(project_root: Path, python_exe: str, url_file: Optional[Path] = None) -> None:
    """Launch N13 in GUI mode (which auto-starts the authenticated Live Server).

    If *url_file* is provided, pass it as a cold-start browser download (the GUI
    drains it once ready).  The GUI is used for both the ``dldm://launch`` signal
    and the ``dldm://<url>`` flow so the browser extension can always reach the
    authenticated local API after startup.
    """
    main_script = project_root / "d.py"
    if sys.platform == "win32":
        bat_path = Path(tempfile.gettempdir()) / f"dldm_run_{uuid.uuid4().hex}.bat"
        url_arg = f' --url-file "{url_file}"' if url_file else ""
        # Build line-by-line: a single chained ``x if cond else y`` expression
        # here once silently dropped the launch command entirely (implicit
        # string concatenation binds tighter than the conditional), leaving a
        # bat file that only deleted itself — the infamous console flash.
        bat_lines = [
            "@echo off",
            "chcp 65001 >nul 2>&1",
            f'"{python_exe}" "{main_script}" --gui{url_arg}',
        ]
        if url_file:
            bat_lines.append(f'del "{url_file}" 2>nul')
        bat_lines.append('del "%~f0" 2>nul')
        bat_content = "\n".join(bat_lines) + "\n"
        bat_path.write_text(bat_content, encoding="utf-8")
        subprocess.Popen(
            ["cmd", "/c", str(bat_path)],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            cwd=str(project_root),
        )
    else:
        args = [python_exe, str(main_script), "--gui"]
        if url_file:
            args.extend(["--from-browser", "--url-file", str(url_file)])
        subprocess.Popen(args, cwd=str(project_root))


def main() -> int:
    if len(sys.argv) < 2:
        return 1

    url = decode_url(sys.argv[1])

    project_root = Path(__file__).resolve().parent.parent
    python_exe = sys.executable

    # Special launch signal from the extension's "Open N13" button.
    if url == "launch":
        _launch_n13(project_root, python_exe)
        return 0

    if not url.startswith(("http://", "https://")):
        # Write a tiny diagnostic so silent failures can be traced.
        try:
            log_path = Path(tempfile.gettempdir()) / "dldm_handler.log"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Rejected (not http(s)): {sys.argv[1]!r} -> {url!r}\n")
        except OSError:
            pass
        return 1

    url_file = Path(tempfile.gettempdir()) / f"dldm_url_{uuid.uuid4().hex}.txt"
    url_file.write_text(url, encoding="utf-8")
    _launch_n13(project_root, python_exe, url_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
