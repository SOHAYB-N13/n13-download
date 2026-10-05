"""dldm:// protocol registration — product-level, path-independent guarantees.

Background (the bug these tests lock down):

An older N13 build re-registered ``dldm://`` from inside the running
application using a *developer* command shape built from ``sys.executable`` and
the internal handler script.  In a packaged one-dir build ``sys.executable`` is
``N13.exe``, so the resulting registry value was::

    "<install>\\N13.exe" "<install>\\_internal\\browser\\dldm_handler.py" "%1"

The production contract is instead::

    "<install dir>\\N13.exe" "%1"

with ``<install dir>`` resolved per installation — never hardcoded to any drive
letter, user name or development path.

These tests run the real registration code against an in-memory registry fake,
for several different installation locations, and also guard the installer
source contract.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import re
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROTOCOL_PY = ROOT / "browser" / "protocol.py"
ENTRY_PY = ROOT / "build" / "n13_entry.py"
ISS_PATH = ROOT / "installer" / "N13-Setup.iss"

# Installation locations the fix must work for.  A) B) C) are the scenarios
# required by the specification; the others cover per-user installs, a portable
# layout and the reporter's own machine (which is just one case among many).
INSTALL_CASES = [
    r"C:\Program Files\N13 Download Manager\N13.exe",
    r"D:\Apps\N13\N13.exe",
    r"E:\PortableApps\N13 Download Manager\N13.exe",
    r"C:\Users\somebody\AppData\Local\Programs\N13 Download Manager\N13.exe",
    r"G:\app\N13 Download Manager\N13.exe",
]

FORBIDDEN_TOKENS = (
    "python.exe",
    "pythonw.exe",
    "py.exe",
    "pyw.exe",
    "cmd.exe",
    "powershell",
    "pwsh.exe",
    "dldm_handler",
    "\\d.py",
    "n13_entry.py",
    "_internal",
)

BROKEN_LEGACY_COMMAND = (
    r'"G:\app\N13 Download Manager\N13.exe" '
    r'"G:\app\N13 Download Manager\_internal\browser\dldm_handler.py" "%1"'
)


# --------------------------------------------------------------------------- #
# In-memory registry fake (no real registry key is ever touched)               #
# --------------------------------------------------------------------------- #

class _FakeKey:
    def __init__(self, registry, root, path):
        self._registry = registry
        self.root = root
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    """Minimal ``winreg`` stand-in backed by a dict of nodes."""

    HKEY_CURRENT_USER = 0x80000001
    HKEY_LOCAL_MACHINE = 0x80000002
    REG_SZ = 1
    REG_EXPAND_SZ = 2

    def __init__(self):
        # (root, normalised path) -> {"values": {name: (value, type)}}
        self.nodes: dict[tuple[int, str], dict] = {}
        self.set_calls = 0
        self.deleted_keys: list[tuple[int, str]] = []

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _norm(path) -> str:
        return str(path).replace("/", "\\").strip("\\").lower()

    def _ensure(self, root, path) -> tuple[int, str]:
        key = (int(root), self._norm(path))
        # Create ancestors too, exactly like the real API does.
        parts = key[1].split("\\") if key[1] else []
        for i in range(len(parts)):
            ancestor = (key[0], "\\".join(parts[: i + 1]))
            self.nodes.setdefault(ancestor, {"values": {}})
        return key

    def _subkeys(self, root, path) -> list[str]:
        base = self._norm(path)
        prefix = base + "\\" if base else ""
        found = []
        for (r, p) in self.nodes:
            if r != int(root) or p == base or not p.startswith(prefix):
                continue
            rest = p[len(prefix):]
            if "\\" not in rest:
                found.append(rest)
        return sorted(found)

    # -- public API ------------------------------------------------------- #
    def CreateKey(self, root, path):
        self._ensure(root, path)
        return _FakeKey(self, int(root), self._norm(path))

    def OpenKey(self, root, path, reserved=0, access=0):
        key = (int(root), self._norm(path))
        if key not in self.nodes:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return _FakeKey(self, key[0], key[1])

    def CloseKey(self, key):
        return None

    def SetValueEx(self, key, name, reserved, type_, value):
        self.set_calls += 1
        node = self.nodes[(key.root, key.path)]
        node["values"][name] = (value, type_)

    def QueryValueEx(self, key, name):
        values = self.nodes[(key.root, key.path)]["values"]
        if name not in values:
            raise FileNotFoundError(2, "value not found")
        return values[name]

    def EnumKey(self, key, index):
        subs = self._subkeys(key.root, key.path)
        if index >= len(subs):
            raise OSError(259, "No more data is available")
        return subs[index]

    def EnumValue(self, key, index):
        values = self.nodes[(key.root, key.path)]["values"]
        items = sorted(values.items())
        if index >= len(items):
            raise OSError(259, "No more data is available")
        name, (value, type_) = items[index]
        return name, value, type_

    def DeleteKey(self, root, path):
        key = (int(root), self._norm(path))
        if key not in self.nodes:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        del self.nodes[key]
        self.deleted_keys.append(key)

    def DeleteValue(self, key, name):
        self.nodes[(key.root, key.path)]["values"].pop(name, None)

    # -- test helpers ----------------------------------------------------- #
    def get(self, root, path, name=""):
        key = (int(root), self._norm(path))
        if key not in self.nodes:
            return None
        return self.nodes[key]["values"].get(name, (None, None))[0]

    def has_key(self, root, path) -> bool:
        return (int(root), self._norm(path)) in self.nodes


def load_protocol_module(fake_winreg, force_integration: bool = True):
    """Execute the real ``browser/protocol.py`` with the fake registry bound.

    ``tests/__init__.py`` disables OS integration process-wide so the suite can
    never touch the developer's real registry.  These tests exercise the
    registration logic itself, so integration is forced back on by default.
    """
    spec = importlib.util.spec_from_file_location("n13_protocol_under_test", PROTOCOL_PY)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"winreg": fake_winreg}):
        spec.loader.exec_module(module)
    if force_integration:
        module.os_integration_disabled = lambda: False
    return module


@contextmanager
def simulated_install(exe_path: str):
    """Make the interpreter believe it *is* an installed ``N13.exe``."""
    exe = str(exe_path)
    had_frozen = hasattr(sys, "frozen")
    old_frozen = getattr(sys, "frozen", None)
    old_exe = sys.executable
    sys.frozen = True
    sys.executable = exe
    try:
        yield exe
    finally:
        if had_frozen:
            sys.frozen = old_frozen
        else:
            try:
                del sys.frozen
            except AttributeError:
                pass
        sys.executable = old_exe


@contextmanager
def simulated_source_checkout():
    """Simulate running from a source checkout (no ``sys.frozen``)."""
    had_frozen = hasattr(sys, "frozen")
    old_frozen = getattr(sys, "frozen", None)
    old_exe = sys.executable
    if had_frozen:
        try:
            del sys.frozen
        except AttributeError:
            pass
    sys.executable = old_exe
    try:
        yield
    finally:
        if had_frozen:
            sys.frozen = old_frozen


class ProtocolRegistrationTest(unittest.TestCase):
    """Production command shape is derived from the install location."""

    # ── the command itself ──────────────────────────────────────────────── #

    def test_command_uses_actual_install_directory_for_every_location(self):
        for exe in INSTALL_CASES:
            with self.subTest(exe=exe):
                fake = FakeWinreg()
                protocol = load_protocol_module(fake)
                with simulated_install(exe):
                    self.assertEqual(
                        protocol.protocol_launch_command(),
                        f'"{exe}" "%1"',
                    )

    def test_command_never_references_interpreters_or_developer_scripts(self):
        for exe in INSTALL_CASES:
            with self.subTest(exe=exe):
                fake = FakeWinreg()
                protocol = load_protocol_module(fake)
                with simulated_install(exe):
                    command = protocol.protocol_launch_command().lower()
                for token in FORBIDDEN_TOKENS:
                    self.assertNotIn(token, command, f"{token!r} leaked into {command!r}")
                self.assertIn("n13.exe", command)

    def test_icon_uses_the_application_not_an_interpreter(self):
        for exe in INSTALL_CASES:
            with self.subTest(exe=exe):
                fake = FakeWinreg()
                protocol = load_protocol_module(fake)
                with simulated_install(exe):
                    icon = protocol.protocol_icon_command()
                self.assertEqual(icon, f'"{exe}",0')
                self.assertNotIn("python", icon.lower())

    def test_no_drive_letter_or_user_path_is_hardcoded(self):
        """No *executable* string literal may contain an absolute path.

        Docstrings are allowed to illustrate examples (e.g. "C:\\Program
        Files\\..."); actual code must never embed one.
        """
        import ast

        source = PROTOCOL_PY.read_text(encoding="utf-8")
        tree = ast.parse(source)

        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                first = body[0] if body else None
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    docstrings.add(id(first.value))

        offenders = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and re.search(r"[A-Za-z]:\\\\", node.value)
        ]
        self.assertEqual(offenders, [], "hardcoded drive-letter path in code")

    def test_registration_function_source_never_mentions_the_handler(self):
        fake = FakeWinreg()
        protocol = load_protocol_module(fake)
        self.assertNotIn("dldm_handler", inspect.getsource(protocol._register_protocol_windows))
        self.assertNotIn("dldm_handler", inspect.getsource(protocol.protocol_launch_command))

    # ── registry writes ─────────────────────────────────────────────────── #

    def test_registration_writes_install_dir_command(self):
        for exe in INSTALL_CASES:
            with self.subTest(exe=exe):
                fake = FakeWinreg()
                protocol = load_protocol_module(fake)
                with simulated_install(exe):
                    self.assertTrue(protocol.register_protocol())

                self.assertEqual(
                    fake.get(FakeWinreg.HKEY_CURRENT_USER,
                             r"Software\Classes\dldm\shell\open\command"),
                    f'"{exe}" "%1"',
                )
                self.assertEqual(
                    fake.get(FakeWinreg.HKEY_CURRENT_USER,
                             r"Software\Classes\dldm\DefaultIcon"),
                    f'"{exe}",0',
                )
                self.assertEqual(
                    fake.get(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm"),
                    "URL:N13 Download Manager Protocol",
                )
                self.assertEqual(
                    fake.get(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm",
                             "URL Protocol"),
                    "",
                )

    def test_ensure_creates_missing_registration(self):
        fake = FakeWinreg()
        protocol = load_protocol_module(fake)
        with simulated_install(INSTALL_CASES[0]):
            self.assertTrue(protocol.ensure_protocol_registration())
        self.assertTrue(fake.has_key(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm"))

    def test_ensure_is_a_no_op_when_already_correct(self):
        fake = FakeWinreg()
        protocol = load_protocol_module(fake)
        with simulated_install(INSTALL_CASES[1]):
            protocol.ensure_protocol_registration()
            before = fake.set_calls
            self.assertTrue(protocol.ensure_protocol_registration())
            self.assertEqual(fake.set_calls, before, "correct registration was rewritten")

    # ── upgrade / repair ────────────────────────────────────────────────── #

    def test_upgrade_repairs_legacy_handler_registration(self):
        """The exact broken value reported on a real machine."""
        fake = FakeWinreg()
        protocol = load_protocol_module(fake)
        with simulated_install(INSTALL_CASES[4]):
            protocol._register_protocol_windows()          # correct baseline
            # Simulate the value an older build left behind.
            with mock.patch.object(protocol, "protocol_launch_command",
                                   return_value=BROKEN_LEGACY_COMMAND):
                protocol._register_protocol_windows()
            self.assertIn("dldm_handler", fake.get(
                FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\shell\open\command"))

            self.assertTrue(protocol.ensure_protocol_registration())
            self.assertEqual(
                fake.get(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\shell\open\command"),
                f'"{INSTALL_CASES[4]}" "%1"',
            )

    def test_upgrade_repairs_python_launcher_registration(self):
        fake = FakeWinreg()
        protocol = load_protocol_module(fake)
        with simulated_install(INSTALL_CASES[0]):
            fake.CreateKey(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\shell\open\command")
            key = protocol.winreg.OpenKey(
                FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\shell\open\command")
            fake.SetValueEx(
                key, "", 0, FakeWinreg.REG_SZ,
                r'"C:\Python312\python.exe" "D:\dev\n13\browser\dldm_handler.py" "%1"',
            )
            fake.set_calls = 0

            self.assertTrue(protocol.ensure_protocol_registration())
            self.assertEqual(
                fake.get(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\shell\open\command"),
                f'"{INSTALL_CASES[0]}" "%1"',
            )

    def test_upgrade_repairs_stale_install_path(self):
        fake = FakeWinreg()
        protocol = load_protocol_module(fake)
        # Registered by a previous install in a different directory.
        fake.CreateKey(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\shell\open\command")
        key = protocol.winreg.OpenKey(
            FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\shell\open\command")
        fake.SetValueEx(key, "", 0, FakeWinreg.REG_SZ, r'"D:\Old\N13.exe" "%1"')

        with simulated_install(INSTALL_CASES[0]):
            self.assertTrue(protocol.ensure_protocol_registration())
            self.assertEqual(
                fake.get(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\shell\open\command"),
                f'"{INSTALL_CASES[0]}" "%1"',
            )

    def test_status_reports_legacy_registration(self):
        fake = FakeWinreg()
        protocol = load_protocol_module(fake)
        fake.CreateKey(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\shell\open\command")
        key = protocol.winreg.OpenKey(
            FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm\shell\open\command")
        fake.SetValueEx(key, "", 0, FakeWinreg.REG_SZ, BROKEN_LEGACY_COMMAND)

        with simulated_install(INSTALL_CASES[0]):
            status = protocol.protocol_registration_status()
        self.assertTrue(status["registered"])
        self.assertTrue(status["legacy"])
        self.assertFalse(status["current_ok"])
        self.assertEqual(status["expected"], f'"{INSTALL_CASES[0]}" "%1"')

    # ── safety: never touch anything else ───────────────────────────────── #

    def test_other_protocol_registrations_are_untouched(self):
        fake = FakeWinreg()
        protocol = load_protocol_module(fake)
        other = r"Software\Classes\someotherprotocol\shell\open\command"
        fake.CreateKey(FakeWinreg.HKEY_CURRENT_USER, other)
        other_key = protocol.winreg.OpenKey(FakeWinreg.HKEY_CURRENT_USER, other)
        fake.SetValueEx(other_key, "", 0, FakeWinreg.REG_SZ, r'"C:\Other\app.exe" "%1"')

        with simulated_install(INSTALL_CASES[1]):
            protocol.ensure_protocol_registration()
            protocol.unregister_protocol()

        self.assertEqual(
            fake.get(FakeWinreg.HKEY_CURRENT_USER, other),
            r'"C:\Other\app.exe" "%1"',
        )

    def test_unrelated_values_inside_the_protocol_key_are_preserved(self):
        fake = FakeWinreg()
        protocol = load_protocol_module(fake)
        with simulated_install(INSTALL_CASES[1]):
            protocol._register_protocol_windows()
            key = protocol.winreg.OpenKey(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm")
            fake.SetValueEx(key, "CustomUserValue", 0, FakeWinreg.REG_SZ, "keep-me")

            protocol.ensure_protocol_registration(force=True)

        self.assertEqual(
            fake.get(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm", "CustomUserValue"),
            "keep-me",
        )

    def test_unregister_removes_only_the_dldm_key(self):
        fake = FakeWinreg()
        protocol = load_protocol_module(fake)
        other = r"Software\Classes\someotherprotocol\shell\open\command"
        fake.CreateKey(FakeWinreg.HKEY_CURRENT_USER, other)

        with simulated_install(INSTALL_CASES[2]):
            protocol._register_protocol_windows()
            self.assertTrue(protocol.is_protocol_registered())
            self.assertTrue(protocol.unregister_protocol())

        self.assertFalse(fake.has_key(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm"))
        self.assertTrue(fake.has_key(FakeWinreg.HKEY_CURRENT_USER, other))

    # ── the suite must never touch the real registry ────────────────────── #

    def test_kill_switch_prevents_every_registry_write(self):
        fake = FakeWinreg()
        protocol = load_protocol_module(fake, force_integration=False)
        with tempfile.TemporaryDirectory() as tmp:
            protocol._native_host_dir = lambda: Path(tmp)
            with mock.patch.dict(os.environ, {"N13_SKIP_OS_INTEGRATION": "1"}):
                with simulated_install(INSTALL_CASES[0]):
                    self.assertFalse(protocol.register_protocol())
                    self.assertFalse(protocol.ensure_protocol_registration())
                    self.assertFalse(protocol.unregister_protocol())
                    self.assertFalse(protocol.register_native_host())

        self.assertEqual(fake.set_calls, 0)
        self.assertFalse(fake.has_key(FakeWinreg.HKEY_CURRENT_USER, r"Software\Classes\dldm"))

    # ── source checkouts keep working ───────────────────────────────────── #

    def test_source_checkout_registers_the_real_entry_point(self):
        fake = FakeWinreg()
        protocol = load_protocol_module(fake)
        with simulated_source_checkout():
            command = protocol.protocol_launch_command()
        self.assertIn("n13_entry.py", command)
        self.assertIn('"%1"', command)
        self.assertNotIn("dldm_handler", command)

    # ── native messaging host launcher (same root cause) ────────────────── #

    def test_native_host_launcher_uses_the_app_when_installed(self):
        fake = FakeWinreg()
        protocol = load_protocol_module(fake)
        with tempfile.TemporaryDirectory() as tmp:
            protocol._native_host_dir = lambda: Path(tmp)
            with simulated_install(INSTALL_CASES[3]):
                with mock.patch.object(protocol, "_discover_loaded_extension_ids",
                                       return_value=[]):
                    self.assertTrue(protocol.register_native_host())
            bat = (Path(tmp) / "n13_native_host.bat").read_text(encoding="utf-8")

        self.assertIn(f'"{INSTALL_CASES[3]}" --native-host', bat)
        for token in ("python.exe", "pythonw.exe", "dldm_handler", "n13_entry.py"):
            self.assertNotIn(token, bat.lower())
        self.assertEqual(
            fake.get(FakeWinreg.HKEY_CURRENT_USER,
                     r"Software\Google\Chrome\NativeMessagingHosts\com.n13.download_manager"),
            str(Path(tmp) / "com.n13.download_manager.json"),
        )


class EntryPointTest(unittest.TestCase):
    """The installed executable parses dldm:// from its own command line."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("n13_entry_under_test", ENTRY_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.entry = module

    def test_single_argument_protocol_invocation(self):
        self.assertEqual(
            self.entry._extract_protocol_url(["dldm://https%3A%2F%2Fexample.com%2Ff.zip"]),
            "dldm://https%3A%2F%2Fexample.com%2Ff.zip",
        )

    def test_url_is_decoded(self):
        self.assertEqual(
            self.entry._decode_protocol_url("dldm://https%3A%2F%2Fexample.com%2Ff.zip"),
            "https://example.com/f.zip",
        )

    def test_double_encoded_url_is_decoded(self):
        self.assertEqual(
            self.entry._decode_protocol_url("dldm://https%253A%252F%252Fexample.com%252Fa.zip"),
            "https://example.com/a.zip",
        )

    def test_launch_signal(self):
        self.assertEqual(self.entry._decode_protocol_url("dldm://launch"), "launch")

    def test_plain_http_invocation(self):
        self.assertEqual(
            self.entry._extract_protocol_url(["https://example.com/x.zip"]),
            "https://example.com/x.zip",
        )

    def test_unknown_argument_is_not_treated_as_a_url(self):
        self.assertIsNone(self.entry._extract_protocol_url(["--help"]))


class InstallerSourceTest(unittest.TestCase):
    """The installer must register {app}\\N13.exe and nothing else."""

    @classmethod
    def setUpClass(cls):
        cls.iss = ISS_PATH.read_text(encoding="utf-8")

    def test_registry_command_is_app_exe_percent1(self):
        self.assertIn(
            'ValueData: """{app}\\{#MyAppExeName}"" ""%1"""',
            self.iss,
        )

    def test_registry_directives_never_register_the_handler_or_an_interpreter(self):
        """Only the [Registry] *directives* matter — comments may explain them."""
        directives = [
            line for line in self.iss.splitlines()
            if line.strip().lower().startswith(("root:", "filename:", "name:"))
        ]
        self.assertTrue(directives)
        for line in directives:
            lowered = line.lower()
            for token in ("dldm_handler", "python.exe", "pythonw.exe",
                          "cmd.exe", "powershell", "n13_entry.py"):
                self.assertNotIn(token, lowered, f"directive registers {token!r}: {line}")

    def test_repair_recognises_legacy_broken_commands(self):
        self.assertIn("IsStaleProtocolCommand", self.iss)
        lowered = self.iss.lower()
        for legacy in ("dldm_handler", "python.exe", "cmd.exe"):
            self.assertIn(legacy, lowered)

    def test_repair_uses_the_dynamic_app_directory(self):
        self.assertIn("ExpandConstant('{app}\\{#MyAppExeName}')", self.iss)
        self.assertIn("RepairProtocolRegistration", self.iss)
        self.assertIn("CurStepChanged", self.iss)

    def test_no_hardcoded_drive_letter_path(self):
        self.assertIsNone(
            re.search(r"[A-Za-z]:\\\\", self.iss),
            "installer contains a hardcoded drive-letter path",
        )

    def test_uninstall_cleans_the_protocol_registration(self):
        self.assertIn("uninsdeletekey", self.iss)
        self.assertIn("RemoveProtocolRegistration", self.iss)


if __name__ == "__main__":
    unittest.main()
