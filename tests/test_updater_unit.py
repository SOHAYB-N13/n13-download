"""Focused tests for the auto-update system (core/updater.py).
Network access is emulated with local fixtures; one optional test hits the
real GitHub API and is skipped offline.
"""

import functools
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.version import VERSION, compare_versions, parse_version  # noqa: E402
from core import updater  # noqa: E402
from core.updater import (  # noqa: E402
    UpdateController,
    UpdateError,
    UpdateState,
    fetch_latest_release,
    is_newer,
    launch_powershell_updater,
    parse_checksum,
    sha256_file,
    verify_installer,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

FAKE_INSTALLER = b"N13-fake-installer-for-tests\x00\x01\x02" * 4096


def _release_json(version: str, port: int, with_checksum: bool = True,
                  installer_name: str = updater.INSTALLER_NAME) -> dict:
    assets = [{
        "name": installer_name,
        "size": len(FAKE_INSTALLER),
        "browser_download_url": f"http://127.0.0.1:{port}/{installer_name}",
    }]
    if with_checksum:
        assets.append({
            "name": installer_name + ".sha256.txt",
            "size": 98,
            "browser_download_url": f"http://127.0.0.1:{port}/{installer_name}.sha256.txt",
        })
    return {
        "tag_name": f"v{version}",
        "name": f"N13 Download Manager v{version}",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-08-12T00:00:00Z",
        "body": "Release notes\n- improvements",
        "html_url": f"https://example.test/releases/v{version}",
        "assets": assets,
    }


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture()
def file_server(tmp_path):
    """Serve a fake installer + checksum over a loopback HTTP server."""
    (tmp_path / updater.INSTALLER_NAME).write_bytes(FAKE_INSTALLER)
    digest = hashlib.sha256(FAKE_INSTALLER).hexdigest()
    (tmp_path / (updater.INSTALLER_NAME + ".sha256.txt")).write_text(
        f"{digest}  {updater.INSTALLER_NAME}\n", encoding="ascii")
    handler = functools.partial(_QuietHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, tmp_path, digest
    server.shutdown()
    thread.join(timeout=5)


def _wait_state(controller: UpdateController, targets, timeout=30.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if controller.state in targets:
            break
        time.sleep(0.05)
    return controller.state


# --------------------------------------------------------------------------- #
# 1–2. Version detection + semantic comparison
# --------------------------------------------------------------------------- #

def test_current_version_detection():
    assert updater.get_current_version() == VERSION
    assert parse_version(VERSION) is not None


def test_semantic_version_comparison():
    assert compare_versions("1.0.9", "1.0.10") == -1
    assert compare_versions("1.0.10", "1.1.0") == -1
    assert compare_versions("1.9.0", "2.0.0") == -1
    assert compare_versions("2.0.0", "2.0.0") == 0
    assert compare_versions("2.0.1", "2.0.0") == 1
    assert is_newer("1.0.4", "1.0.3")
    assert not is_newer("1.0.3", "1.0.3")
    assert not is_newer("1.0.2", "1.0.3")


# --------------------------------------------------------------------------- #
# 3. Checksum parsing
# --------------------------------------------------------------------------- #

def test_parse_checksum_formats():
    digest = "a" * 64
    assert parse_checksum(f"{digest}  N13-Download-Manager-Setup.exe") == digest
    assert parse_checksum(digest) == digest
    assert parse_checksum(f"SHA256: {digest}") is None  # first token must be the hash
    assert parse_checksum("") is None
    assert parse_checksum("not-a-hash") is None


# --------------------------------------------------------------------------- #
# 4–6. Release discovery (fixtures over loopback HTTP)
# --------------------------------------------------------------------------- #

def test_fetch_latest_release_update_available(file_server, monkeypatch, tmp_path):
    server, _, _ = file_server
    release = _release_json("9.9.9", server.server_port)
    fixture = tmp_path / "latest.json"
    fixture.write_text(json.dumps(release), encoding="utf-8")
    monkeypatch.setenv("N13_UPDATE_API_URL", fixture.as_uri())

    info, err = fetch_latest_release("owner/repo")
    assert err is None
    assert info is not None
    assert info.version == "9.9.9"
    assert info.installer_url.endswith(updater.INSTALLER_NAME)
    assert info.checksum_url.endswith(".sha256.txt")
    assert is_newer(info.version, VERSION)


def test_fetch_latest_release_no_update(file_server, monkeypatch, tmp_path):
    server, _, _ = file_server
    fixture = tmp_path / "latest.json"
    fixture.write_text(json.dumps(_release_json("0.0.1", server.server_port)), encoding="utf-8")
    monkeypatch.setenv("N13_UPDATE_API_URL", fixture.as_uri())

    info, err = fetch_latest_release("owner/repo")
    assert err is None
    assert info is not None
    assert not is_newer(info.version, VERSION)


def test_fetch_latest_release_ignores_prerelease(monkeypatch, tmp_path):
    data = _release_json("9.9.9", 1)
    data["prerelease"] = True
    fixture = tmp_path / "latest.json"
    fixture.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("N13_UPDATE_API_URL", fixture.as_uri())
    info, err = fetch_latest_release("owner/repo")
    assert info is None and err == UpdateError.NO_RELEASE


def test_fetch_latest_release_network_failure(monkeypatch):
    # Port 1 is closed locally — connection must fail fast and cleanly.
    monkeypatch.setenv("N13_UPDATE_API_URL", "http://127.0.0.1:1/releases/latest")
    info, err = fetch_latest_release("owner/repo")
    assert info is None
    assert err == UpdateError.NETWORK


# --------------------------------------------------------------------------- #
# 7–10. Download, progress, cancel, verification
# --------------------------------------------------------------------------- #

def _controller_to_available(monkeypatch, tmp_path, port, version="9.9.9", with_checksum=True):
    fixture = tmp_path / "latest.json"
    fixture.write_text(json.dumps(_release_json(version, port, with_checksum)), encoding="utf-8")
    monkeypatch.setenv("N13_UPDATE_API_URL", fixture.as_uri())
    controller = UpdateController(config=None)
    controller.check()
    assert _wait_state(controller, {UpdateState.AVAILABLE}) == UpdateState.AVAILABLE
    return controller


def test_download_progress_and_verify_success(file_server, monkeypatch, tmp_path):
    server, _, digest = file_server
    controller = _controller_to_available(monkeypatch, tmp_path, server.server_port)
    controller.download()
    state = _wait_state(controller, {UpdateState.READY_TO_INSTALL, UpdateState.FAILED})
    assert state == UpdateState.READY_TO_INSTALL
    snapshot = controller.get_state()
    assert snapshot["progress"]["percent"] == 100
    assert snapshot["progress"]["downloaded_bytes"] == len(FAKE_INSTALLER)
    path = Path(snapshot["download_path"])
    assert path.is_file()
    assert sha256_file(path) == digest
    # Staging lives under %TEMP%\N13-Updater, never in the install dir.
    assert str(tmp_path) not in str(path)
    assert updater.TEMP_ROOT_NAME in str(path)
    controller._cleanup_staging()


def test_download_checksum_mismatch_rejected(file_server, monkeypatch, tmp_path):
    server, serve_dir, _ = file_server
    # Corrupt the published checksum so verification must fail.
    (serve_dir / (updater.INSTALLER_NAME + ".sha256.txt")).write_text(
        "0" * 64 + f"  {updater.INSTALLER_NAME}\n", encoding="ascii")
    controller = _controller_to_available(monkeypatch, tmp_path, server.server_port)
    controller.download()
    state = _wait_state(controller, {UpdateState.READY_TO_INSTALL, UpdateState.FAILED})
    assert state == UpdateState.FAILED
    snapshot = controller.get_state()
    assert snapshot["error"]["code"] == UpdateError.CHECKSUM_MISMATCH
    assert snapshot["download_path"] is None  # invalid installer deleted


def test_download_missing_checksum_rejected(file_server, monkeypatch, tmp_path):
    server, _, _ = file_server
    controller = _controller_to_available(
        monkeypatch, tmp_path, server.server_port, with_checksum=False)
    controller.download()
    state = _wait_state(controller, {UpdateState.READY_TO_INSTALL, UpdateState.FAILED})
    assert state == UpdateState.FAILED
    assert controller.get_state()["error"]["code"] == UpdateError.NO_CHECKSUM


def test_download_cancellation(file_server, monkeypatch, tmp_path):
    server, _, _ = file_server
    controller = _controller_to_available(monkeypatch, tmp_path, server.server_port)

    orig = updater.download_file

    def slow_download(url, dest, progress_cb=None, cancel_event=None):
        # Dribble the file out so the cancel event lands mid-download.
        with open(dest, "wb") as f:
            for i in range(0, len(FAKE_INSTALLER), 1024):
                if cancel_event is not None and cancel_event.is_set():
                    raise updater.DownloadCancelled()
                f.write(FAKE_INSTALLER[i:i + 1024])
                f.flush()
                if progress_cb:
                    progress_cb(i + 1024, len(FAKE_INSTALLER), 1.0, 1.0)
                time.sleep(0.005)
        return None

    monkeypatch.setattr(updater, "download_file", slow_download)
    controller.download()
    time.sleep(0.2)  # let the download get going
    controller.cancel_download()
    state = _wait_state(controller, {UpdateState.CANCELLED, UpdateState.FAILED}, timeout=10)
    monkeypatch.setattr(updater, "download_file", orig)
    assert state == UpdateState.CANCELLED
    snapshot = controller.get_state()
    assert snapshot["download_path"] is None


def test_verify_installer_requires_checksum(tmp_path):
    f = tmp_path / "setup.exe"
    f.write_bytes(FAKE_INSTALLER)
    assert verify_installer(f, hashlib.sha256(FAKE_INSTALLER).hexdigest())
    assert not verify_installer(f, None)
    assert not verify_installer(f, "")
    assert not verify_installer(f, "0" * 64)


# --------------------------------------------------------------------------- #
# 11. State machine guards
# --------------------------------------------------------------------------- #

def test_state_machine_rejects_invalid_transitions():
    controller = UpdateController(config=None)
    assert controller.state == UpdateState.IDLE
    controller.download()  # no release -> stays IDLE
    assert controller.state == UpdateState.IDLE
    assert not controller._transition(UpdateState.DOWNLOADING)
    assert controller.state == UpdateState.IDLE
    ok, err = controller.install()
    assert not ok and err == UpdateError.NOT_READY


# --------------------------------------------------------------------------- #
# 12. PowerShell updater — script content + standalone guard behaviour
# --------------------------------------------------------------------------- #

def test_updater_script_contains_mandatory_stages(tmp_path):
    script = tmp_path / updater.PS1_NAME
    script.write_text(updater.POWERSHELL_UPDATER, encoding="utf-8")
    text = script.read_text(encoding="utf-8")
    for token in (
        "UPDATER_START",
        "N13_EXIT",
        "UNINSTALL",
        "UNINSTALL_VERIFY",
        "USER_DATA_DELETE",
        "INSTALL",
        "INSTALL_VERIFY",
        "RESTART",
        "CLEANUP",
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/NOCANCEL",
        "/DIR=",
        "unins000",
        "System.Security.Cryptography.SHA256",
        "LocalApplicationData",
        "$N13Pid",
    ):
        assert token in text, f"missing token {token!r} in update.ps1"
    # The updater must be explicitly parameterised, never hard-coded paths.
    assert "InstallDir" in text
    assert "ExpectedVersion" in text


def test_launch_updater_requires_install_dir(tmp_path):
    # A source checkout has no install dir -> DEV_MODE, never UPDATER_FAILED.
    ok, err = launch_powershell_updater(
        staging=tmp_path,
        installer_path=tmp_path / "setup.exe",
        expected_checksum="",
        expected_version="1.0.4",
        app_install_dir=None,
    )
    assert not ok
    assert err == UpdateError.DEV_MODE


def test_launch_updater_requires_existing_uninstaller(tmp_path, monkeypatch):
    fake_dir = tmp_path / "install"
    fake_dir.mkdir()
    monkeypatch.setenv("N13_UPDATE_INSTALL_DIR", str(fake_dir))
    ok, err = launch_powershell_updater(
        staging=tmp_path,
        installer_path=tmp_path / "setup.exe",
        expected_checksum="",
        expected_version="1.0.4",
        app_install_dir=fake_dir,
    )
    assert not ok
    assert err == UpdateError.UPDATER_FAILED


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell")
def test_updater_script_standalone_guard(tmp_path):
    script = tmp_path / updater.PS1_NAME
    script.write_text(updater.POWERSHELL_UPDATER, encoding="utf-8")
    # A missing install dir must make the standalone script fail fast (code 11).
    r = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(script),
            "-N13Pid", "0",
            "-ExePath", str(tmp_path / "missing" / "N13.exe"),
            "-InstallDir", str(tmp_path / "missing"),
            "-Uninstaller", str(tmp_path / "missing" / "unins000.exe"),
            "-Installer", str(tmp_path / "setup.exe"),
            "-ExpectedVersion", "1.0.4",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 11
    log_file = tmp_path / "update.log"
    assert log_file.is_file()
    assert "UNINSTALL" in log_file.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 13. Live GitHub check (optional; skipped when offline)
# --------------------------------------------------------------------------- #

def test_live_github_latest_release():
    pytest.importorskip("urllib.request")
    try:
        info, err = updater.fetch_latest_release(updater.DEFAULT_REPO)
    except Exception:
        pytest.skip("GitHub unreachable")
    if info is None:
        pytest.skip(f"GitHub unreachable ({err})")
    assert parse_version(info.version) is not None
    assert info.installer_url.endswith(".exe")
