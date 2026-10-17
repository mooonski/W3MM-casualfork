import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.smupdate import (
    BACKUP_FOLDER,
    MANIFEST,
    ScriptMergerUpdateFailed,
    inUse,
    isKept,
    programRoot,
    updateScriptMerger,
)
from src.gui.scriptmerger_dialog import OPEN_PAGE, UPDATE, askScriptMergerUpdate


def write(file: Path, text: str) -> Path:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(text.encode())
    return file


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.folder = self.root / "script merger"
        self.exe = write(self.folder / "WitcherScriptMerger.exe", "exe 1.5")
        write(self.folder / "WitcherScriptMerger.dll", "dll 1.5")
        write(self.folder / "Tools/KDiff3/kdiff3.exe", "kdiff old")
        write(self.folder / "MergeInventory.xml", "my merges")
        write(self.folder / "ScriptMergerRemastered.settings.xml", "my settings")
        write(self.folder / "WitcherScriptMerger.dll.config", "my config")
        write(self.folder / "Data/Bundle Index Cache/a.json", "cache")
        write(self.folder / "Retired.dll", "only in the old version")
        self.backups = self.root / "backups"
        self.archive = write(self.root / "Script Merger Remastered 13076 1.5.1.zip", "zip")
        # what the archive holds, under a top folder like some uploads have
        self.package = self.root / "package" / "Script Merger"
        write(self.package / "WitcherScriptMerger.exe", "exe 1.5.1")
        write(self.package / "WitcherScriptMerger.dll", "dll 1.5.1")
        write(self.package / "Tools/KDiff3/kdiff3.exe", "kdiff new")
        write(self.package / "Languages/de.json", "new file")
        write(self.package / "MergeInventory.xml", "empty inventory")
        write(self.package / "ScriptMergerRemastered.settings.xml", "default settings")
        write(self.package / "Data/Bundle Index Cache/a.json", "other cache")

    def extract(self, archive, target):
        shutil.copytree(self.package.parent, target, dirs_exist_ok=True)
        return target

    @staticmethod
    def version(executable):
        return Path(executable).read_text().split()[-1]

    def update(self, **options):
        options.setdefault("extract", self.extract)
        options.setdefault("version", self.version)
        return updateScriptMerger(str(self.exe), str(self.archive), str(self.backups), **options)

    def test_installs_program_files_and_keeps_settings(self):
        result = self.update()
        self.assertEqual((result.previous, result.version), ("1.5", "1.5.1"))
        self.assertEqual((self.folder / "WitcherScriptMerger.dll").read_text(), "dll 1.5.1")
        self.assertEqual((self.folder / "Tools/KDiff3/kdiff3.exe").read_text(), "kdiff new")
        self.assertEqual((self.folder / "Languages/de.json").read_text(), "new file")
        for name, text in [
            ("MergeInventory.xml", "my merges"),
            ("ScriptMergerRemastered.settings.xml", "my settings"),
            ("WitcherScriptMerger.dll.config", "my config"),
            ("Data/Bundle Index Cache/a.json", "cache"),
            ("Retired.dll", "only in the old version"),
        ]:
            self.assertEqual((self.folder / name).read_text(), text, name)
        self.assertEqual(
            sorted(result.replaced), ["Tools/KDiff3/kdiff3.exe", "WitcherScriptMerger.dll", "WitcherScriptMerger.exe"]
        )
        self.assertEqual(result.added, ["Languages/de.json"])
        self.assertEqual(len(result.kept), 3)

        backup = Path(result.backup)
        self.assertEqual(backup.parent, self.backups / BACKUP_FOLDER)
        self.assertTrue(backup.name.startswith("1.5 "))
        self.assertEqual((backup / "WitcherScriptMerger.exe").read_text(), "exe 1.5")
        self.assertEqual((backup / "Tools/KDiff3/kdiff3.exe").read_text(), "kdiff old")
        manifest = json.loads((backup / MANIFEST).read_text())
        self.assertEqual((manifest["version"], manifest["added"]), ("1.5", ["Languages/de.json"]))

    def test_settings_files_are_added_when_missing(self):
        (self.folder / "MergeInventory.xml").unlink()
        result = self.update()
        self.assertEqual((self.folder / "MergeInventory.xml").read_text(), "empty inventory")
        self.assertIn("MergeInventory.xml", result.added)

    def test_rolls_back_when_a_copy_fails(self):
        real = shutil.copy2

        def copy(source, target, *args, **kwargs):
            # the last file copied into Script Merger's folder fails, after the others were replaced
            if str(target).endswith("de.json"):
                raise PermissionError("locked")
            return real(source, target, *args, **kwargs)

        with patch("src.core.smupdate.shutil.copy2", side_effect=copy):
            with self.assertRaises(ScriptMergerUpdateFailed) as failure:
                self.update()
        self.assertIn("previous version was put back", str(failure.exception))
        self.assertEqual(self.exe.read_text(), "exe 1.5")
        self.assertEqual((self.folder / "Tools/KDiff3/kdiff3.exe").read_text(), "kdiff old")
        self.assertFalse((self.folder / "Languages/de.json").exists())

    def test_refuses_bad_archives_and_missing_program(self):
        with self.assertRaisesRegex(ScriptMergerUpdateFailed, "doesn't contain WitcherScriptMerger.exe"):
            self.update(extract=lambda archive, target: write(Path(target) / "readme.txt", "x"))
        with self.assertRaisesRegex(ScriptMergerUpdateFailed, "Couldn't extract"):
            self.update(extract=lambda archive, target: (_ for _ in ()).throw(IOError("bad zip")))
        with self.assertRaisesRegex(ScriptMergerUpdateFailed, "wasn't found"):
            updateScriptMerger(str(self.root / "missing.exe"), str(self.archive), str(self.backups))
        with self.assertRaisesRegex(ScriptMergerUpdateFailed, "Script Merger is open"):
            with patch("src.core.smupdate.inUse", return_value=True):
                self.update()
        self.assertEqual(self.exe.read_text(), "exe 1.5")
        self.assertFalse(self.backups.exists())

    def test_helpers(self):
        self.assertTrue(isKept("MergeInventory.xml"))
        self.assertTrue(isKept("scriptmergerremastered.SETTINGS.xml"))
        self.assertTrue(isKept("Data/Bundle Index Cache/x.json"))
        self.assertFalse(isKept("WitcherScriptMerger.dll"))
        self.assertFalse(isKept("Tools/MergeInventory.xml"))
        self.assertEqual(programRoot(str(self.package.parent)), str(self.package))
        self.assertEqual(programRoot(str(self.root / "nothing")), "")
        self.assertFalse(inUse(str(self.folder)))
        with patch("builtins.open", side_effect=PermissionError("locked")):
            self.assertTrue(inUse(str(self.folder)))


class QuestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def answer(self, button):
        def click(box):
            [b for b in box.buttons() if b.text() == button][0].click()

        with patch.object(QMessageBox, "exec", click):
            return askScriptMergerUpdate(None, "1.5.1", "1.5")

    def test_buttons(self):
        self.assertEqual(self.answer("Update"), UPDATE)
        self.assertEqual(self.answer("Open Nexus Page"), OPEN_PAGE)
        self.assertEqual(self.answer("Not Now"), "")


if __name__ == "__main__":
    unittest.main()
