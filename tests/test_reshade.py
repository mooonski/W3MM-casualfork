import io
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.health import INFO, WARNING, reshadeIssues
from src.core.reshade import (
    ADDON_BUILD,
    BACKUP_FOLDER,
    LIMITED_ADDONS,
    MARKER,
    STANDARD_BUILD,
    LatestReShade,
    ReShadeError,
    download,
    findReShade,
    needsUpdate,
    parseLatest,
    setEnabled,
    setupDll,
    updateReShade,
)


def dll(version: str, build: str = ADDON_BUILD) -> bytes:
    return b"MZ" + MARKER + (LIMITED_ADDONS if build == STANDARD_BUILD else b"") + f" version={version};".encode()


def fakeVersion(file: str) -> str:
    data = Path(file).read_bytes()
    start = data.find(b"version=")
    return data[start + 8 : data.find(b";", start)].decode() if start >= 0 else ""


def setupExe(file: Path, payload: bytes) -> Path:
    '''Like ReShade's setup: a program with a zip archive appended'''
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ReShade32.dll", b"32-bit")
        archive.writestr("ReShade64.dll", payload)
    file.write_bytes(b"MZ setup program" + buffer.getvalue())
    return file


PAGE = '''<a href="/downloads/ReShade_Setup_6.8.0.exe">Download ReShade 6.8.0</a>
<a href="/downloads/ReShade_Setup_6.8.0_Addon.exe">with full add-on support</a>
<a href="/downloads/ReShade_Setup_6.7.3.exe">old</a>'''


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class GameFolder(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.game = self.root / "The Witcher 3"
        self.dx12 = self.game / "bin" / "x64_dx12"
        self.dx11 = self.game / "bin" / "x64"
        self.dx12.mkdir(parents=True)
        self.dx11.mkdir(parents=True)
        (self.dx12 / "dxgi.dll").write_bytes(dll("6.7.3"))
        (self.dx12 / "W3LivingSky.addon64").write_bytes(b"addon")
        (self.dx12 / "DynamicGrass.addon64").write_bytes(b"addon")
        (self.dx12 / "ReShade.ini").write_text("[GENERAL]")
        (self.dx11 / "dinput8.dll").write_bytes(b"MZ some other mod's proxy")
        self.backups = self.root / "backups"

    def find(self):
        return findReShade(str(self.game), fakeVersion)


class ReShadeTests(GameFolder):
    def test_finds_reshade_but_not_other_proxy_dlls(self):
        found = self.find()
        self.assertEqual(len(found), 1)
        install = found[0]
        self.assertEqual((install.binFolder, install.name, install.enabled), ("x64_dx12", "dxgi.dll", True))
        self.assertEqual((install.version, install.build), ("6.7.3", ADDON_BUILD))
        self.assertEqual(install.addons, ["DynamicGrass.addon64", "W3LivingSky.addon64"])

    def test_tells_the_standard_build_apart(self):
        (self.dx11 / "dxgi.dll").write_bytes(dll("6.8.0", STANDARD_BUILD))
        builds = {install.binFolder: install.build for install in self.find()}
        self.assertEqual(builds, {"x64_dx12": ADDON_BUILD, "x64": STANDARD_BUILD})

    def test_disable_and_enable_rename_only_the_dll(self):
        install = self.find()[0]
        setEnabled(install, False, lambda: False)
        self.assertFalse((self.dx12 / "dxgi.dll").exists())
        self.assertTrue((self.dx12 / "dxgi.dll.disabled").exists())
        self.assertTrue((self.dx12 / "ReShade.ini").exists())
        again = self.find()[0]
        self.assertFalse(again.enabled)
        self.assertEqual(again.version, "6.7.3")
        setEnabled(again, True, lambda: False)
        self.assertTrue((self.dx12 / "dxgi.dll").exists())
        self.assertFalse((self.dx12 / "dxgi.dll.disabled").exists())

    def test_refuses_to_rename_while_the_game_runs(self):
        install = self.find()[0]
        with self.assertRaises(ReShadeError):
            setEnabled(install, False, lambda: True)
        self.assertTrue((self.dx12 / "dxgi.dll").exists())

    def test_refuses_when_both_copies_exist(self):
        (self.dx12 / "dxgi.dll.disabled").write_bytes(dll("6.0"))
        install = self.find()[0]
        self.assertTrue(install.duplicate)
        with self.assertRaises(ReShadeError):
            setEnabled(install, False, lambda: False)
        self.assertEqual(fakeVersion(str(self.dx12 / "dxgi.dll.disabled")), "6.0")

    def test_parses_the_newest_setup_links(self):
        latest = parseLatest(PAGE)
        self.assertEqual(latest.version, "6.8.0")
        self.assertEqual(latest.url(STANDARD_BUILD), "https://reshade.me/downloads/ReShade_Setup_6.8.0.exe")
        self.assertEqual(latest.url(ADDON_BUILD), "https://reshade.me/downloads/ReShade_Setup_6.8.0_Addon.exe")
        self.assertIsNone(parseLatest("<html>nothing here</html>"))
        self.assertTrue(needsUpdate(self.find()[0], latest))

    def test_download(self):
        def opener(request, timeout):
            self.assertEqual(request.full_url, "https://reshade.me/downloads/ReShade_Setup_6.8.0_Addon.exe")
            return FakeResponse(b"setup bytes")

        target = download("https://reshade.me/downloads/ReShade_Setup_6.8.0_Addon.exe", str(self.root / "dl"),
                          opener=opener)  # fmt: skip
        self.assertEqual(Path(target).read_bytes(), b"setup bytes")
        self.assertEqual(Path(target).name, "ReShade_Setup_6.8.0_Addon.exe")

    def test_reads_the_dll_from_a_setup_program(self):
        setup = setupExe(self.root / "setup.exe", dll("6.8.0"))
        self.assertEqual(setupDll(str(setup)), dll("6.8.0"))
        (self.root / "broken.exe").write_bytes(b"MZ not a zip")
        with self.assertRaises(ReShadeError):
            setupDll(str(self.root / "broken.exe"))

    def test_update_replaces_the_dll_and_backs_up_the_old_one(self):
        install = self.find()[0]
        setup = setupExe(self.root / "setup.exe", dll("6.8.0"))
        result = updateReShade(install, str(setup), str(self.backups), lambda: False, fakeVersion)
        self.assertEqual((result.previous, result.version), ("6.7.3", "6.8.0"))
        self.assertEqual((self.dx12 / "dxgi.dll").read_bytes(), dll("6.8.0"))
        backups = list((self.backups / BACKUP_FOLDER).glob("6.7.3 */x64_dx12/dxgi.dll"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), dll("6.7.3"))
        self.assertEqual(sorted(p.name for p in self.dx12.iterdir() if "w3mm" in p.name), [])

    def test_update_keeps_a_disabled_dll_disabled(self):
        install = self.find()[0]
        setEnabled(install, False, lambda: False)
        setup = setupExe(self.root / "setup.exe", dll("6.8.0"))
        updateReShade(install, str(setup), str(self.backups), lambda: False, fakeVersion)
        self.assertFalse((self.dx12 / "dxgi.dll").exists())
        self.assertEqual((self.dx12 / "dxgi.dll.disabled").read_bytes(), dll("6.8.0"))

    def test_update_refuses_the_other_build(self):
        install = self.find()[0]
        setup = setupExe(self.root / "setup.exe", dll("6.8.0", STANDARD_BUILD))
        with self.assertRaises(ReShadeError):
            updateReShade(install, str(setup), str(self.backups), lambda: False, fakeVersion)
        self.assertEqual((self.dx12 / "dxgi.dll").read_bytes(), dll("6.7.3"))
        self.assertFalse(self.backups.exists())

    def test_update_refuses_while_the_game_runs(self):
        install = self.find()[0]
        setup = setupExe(self.root / "setup.exe", dll("6.8.0"))
        with self.assertRaises(ReShadeError):
            updateReShade(install, str(setup), str(self.backups), lambda: True, fakeVersion)
        self.assertEqual((self.dx12 / "dxgi.dll").read_bytes(), dll("6.7.3"))


class HealthTests(GameFolder):
    def issues(self):
        return reshadeIssues(str(self.game), lambda game: findReShade(game, fakeVersion))

    def test_no_issues_when_addons_can_load(self):
        self.assertEqual(self.issues(), [])

    def test_disabled_reshade_is_a_note(self):
        setEnabled(self.find()[0], False, lambda: False)
        self.assertEqual([(i.severity, i.problem) for i in self.issues()], [(INFO, "ReShade is disabled")])

    def test_missing_reshade_with_addons(self):
        (self.dx12 / "dxgi.dll").rename(self.root / "moved.dll")
        self.assertEqual([(i.severity, i.problem) for i in self.issues()], [(WARNING, "ReShade add-ons can't load")])

    def test_standard_build_with_addons(self):
        (self.dx12 / "dxgi.dll").write_bytes(dll("6.8.0", STANDARD_BUILD))
        self.assertEqual([i.problem for i in self.issues()], ["ReShade can't load these add-ons"])


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.folder = self.root / "bin" / "x64_dx12"
        self.folder.mkdir(parents=True)
        (self.folder / "dxgi.dll").write_bytes(dll("6.7.3"))
        (self.folder / "W3LivingSky.addon64").write_bytes(b"addon")
        self.messages = []

    def dialog(self, running=lambda: False):
        from src.gui.reshade_dialog import ReShadeDialog

        return ReShadeDialog(
            None,
            str(self.root),
            str(self.root / "backups"),
            self.messages.append,
            running,
            lambda game: findReShade(game, fakeVersion),
            lambda: LatestReShade("6.8.0", "", ""),
        )

    def test_lists_the_install(self):
        dialog = self.dialog()
        self.assertEqual(dialog.tree.topLevelItemCount(), 1)
        item = dialog.tree.topLevelItem(0)
        self.assertEqual(item.text(0), "bin/x64_dx12/dxgi.dll")
        self.assertEqual(item.text(1), "6.7.3")
        self.assertEqual(item.text(4), "W3LivingSky.addon64")
        self.assertEqual(dialog.toggleButton.text(), "Disable")
        self.assertFalse(dialog.updateButton.isEnabled())

    def test_disable_asks_about_addons_and_renames(self):
        dialog = self.dialog()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as asked:
            dialog.toggle()
        asked.assert_called_once()
        self.assertTrue((self.folder / "dxgi.dll").exists())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog.toggle()
        self.assertTrue((self.folder / "dxgi.dll.disabled").exists())
        self.assertEqual(dialog.tree.topLevelItem(0).text(3), "Disabled")
        self.assertEqual(dialog.toggleButton.text(), "Enable")
        dialog.toggle()
        self.assertTrue((self.folder / "dxgi.dll").exists())

    def test_running_game_shows_a_warning(self):
        dialog = self.dialog(running=lambda: True)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                patch.object(QMessageBox, "warning") as warned:  # fmt: skip
            dialog.toggle()
        warned.assert_called_once()
        self.assertTrue((self.folder / "dxgi.dll").exists())

    def test_checked_enables_update(self):
        dialog = self.dialog()
        dialog.checked(LatestReShade("6.8.0", "", ""))
        self.assertTrue(dialog.updateButton.isEnabled())
        self.assertIn("6.8.0", dialog.status.text())
        self.assertIn("6.8.0", self.messages[-1])

    def test_empty_folder(self):
        (self.folder / "dxgi.dll").unlink()
        dialog = self.dialog()
        self.assertEqual(dialog.tree.topLevelItemCount(), 0)
        self.assertIn("isn't installed", dialog.heading.text())
        self.assertFalse(dialog.toggleButton.isEnabled())


if __name__ == "__main__":
    unittest.main()
