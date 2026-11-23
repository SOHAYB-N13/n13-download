"""Static regression checks for the Windows packaging, installer and uninstaller.

These tests do not replace a real install/upgrade/uninstall run.  They lock in
the architectural guarantees that a future edit could silently break:

* no developer-specific absolute paths,
* a standard, selectable installation directory,
* per-user install by default with normal UAC for all-users installs,
* no user-data path in the uninstaller's delete list,
* a production PyInstaller spec that excludes development files,
* a release script that does not delete unrelated release assets.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "installer" / "N13-Setup.iss").read_text(encoding="utf-8")
SPEC = (ROOT / "build" / "n13.spec").read_text(encoding="utf-8")
BUILD = (ROOT / "build" / "scripts" / "build_release.ps1").read_text(encoding="utf-8")


def _section(name: str, text: str = ISS) -> str:
    match = re.search(
        rf"^\[{re.escape(name)}\](.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL
    )
    assert match, f"missing [{name}] section"
    return match.group(1)


def _directives(body: str) -> str:
    """Section body without Inno Setup ``;`` comment lines."""
    return "\n".join(
        line for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    )


def test_default_install_dir_is_standard_and_selectable():
    assert "DefaultDirName={autopf}\\{#MyAppName}" in _section("Setup")
    assert "DisableDirPage=no" in _section("Setup")
    assert "UsePreviousAppDir=yes" in _section("Setup")


def test_installation_privilege_model_is_per_user_with_uac_override():
    setup = _section("Setup")
    assert "PrivilegesRequired=lowest" in setup
    assert "PrivilegesRequiredOverridesAllowed=dialog" in setup
    # Never bypass elevation or force admin for a per-user application.
    assert "PrivilegesRequired=admin" not in setup
    assert "runasoriginaluser" not in ISS.lower()


def test_no_developer_specific_paths_in_installer():
    # The repository must not encode this developer's drive/user paths.
    for forbidden in (r"C:\\", r"H:\\", r"C:/", r"H:/", "%USERNAME%"):
        assert forbidden.lower() not in ISS.lower(), f"hardcoded path {forbidden!r}"
    assert "SOHAYB-N13" in ISS  # publisher URL only, not a filesystem path


def test_protocol_registration_uses_the_selected_install_directory():
    registry = _directives(_section("Registry"))
    assert "HKCU" in registry
    assert "{app}\\{#MyAppExeName}" in registry
    assert '"%1"' in registry
    assert "dldm_handler" not in registry
    assert "python" not in registry.lower()


def test_uninstaller_never_deletes_user_data():
    uninstall = _directives(_section("UninstallDelete")).lower()
    for user_path in (
        "{localappdata}",
        "{userappdata}",
        "{appdata}",
        "{userprofile}",
        "{userdocs}",
        "{userdownloads}",
        "%localappdata%",
        "\\n13\\data",
    ):
        assert user_path not in uninstall, f"uninstaller targets user data: {user_path}"
    # Only paths under the application directory may be removed.
    for line in _directives(_section("UninstallDelete")).splitlines():
        line = line.strip()
        if line.startswith("Type:"):
            assert 'Name: "{app}' in line, f"unsafe UninstallDelete entry: {line}"


def test_uninstaller_code_does_not_delete_localappdata():
    code = _section("Code").lower()
    assert "deltree" not in code
    assert "remove-item" not in code
    assert "localappdata" in code  # used only in the informational message
    # The message must say data is preserved and name the real path.
    assert "preserved" in code
    assert "expandconstant('{localappdata}\\n13')" in code


def test_installed_apps_metadata_is_complete():
    setup = _section("Setup")
    for key in (
        "AppId={{B4A7C9D2-5E1F-4A3B-9C8D-7E6F5A4B3C2D}",
        "AppName={#MyAppName}",
        "AppVersion={#MyAppVersion}",
        "AppPublisher={#MyAppPublisher}",
        "UninstallDisplayIcon={app}\\{#MyAppExeName}",
        "UninstallDisplayName={#MyAppName}",
        "SetupIconFile=..\\assets\\icon.ico",
    ):
        assert key in setup, f"missing metadata: {key}"


def test_start_menu_and_optional_desktop_shortcuts():
    icons = _section("Icons")
    assert 'Name: "{autoprograms}\\{#MyAppName}"' in icons
    assert 'Name: "{autoprograms}\\Uninstall {#MyAppName}"' in icons
    assert 'Name: "{autodesktop}\\{#MyAppName}"' in icons
    assert "Tasks: desktopicon" in icons
    assert "Flags: unchecked" in _section("Tasks")


def test_pyinstaller_spec_is_a_one_dir_production_bundle():
    assert "COLLECT(" in SPEC
    assert "console=False" in SPEC
    assert "icon=os.path.join(ROOT, \"assets\", \"icon.ico\")" in SPEC
    assert "collect_all(" not in SPEC
    for data in ("ui", "frontend", "extension", "assets", "icon.ico"):
        assert data in SPEC
    for excluded in ("tests", "comtypes.test", "pywinauto.tests", "pywinauto.linux"):
        assert f'"{excluded}"' in SPEC, f"spec must exclude {excluded}"


def test_build_script_preserves_unrelated_release_assets():
    # It must not delete the whole release directory (which also holds APKs).
    assert 'Remove-BuildPath (Join-Path $Root "release")' not in BUILD
    assert '"N13-Download-Manager-Setup.exe.sha256.txt"' in BUILD
    assert "sha256" in BUILD.lower()
    # It must reject a bundle that exposes raw Python or development folders.
    assert "*.py" in BUILD
    assert "refusing to remove path outside repository root" in BUILD.lower()
