import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.saves import (
    MANIFEST,
    SAVES_FOLDER,
    SaveBackup,
    backupSaves,
    gameSavesFolder,
    listSaveBackups,
    newestSaves,
    restoreSaves,
)
from src.gui.main_widget import CustomMainWidget
from src.gui.saves_dialog import SaveBackupsDialog


class SavesTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.documents = self.root / "The Witcher 3"
        self.saves = self.documents / "gamesaves"
        self.saves.mkdir(parents=True)
        self.backups = self.root / "backups"
        for number in range(1, 5):
            self.save(f"QuickSave_{number}", number * 100)
        (self.saves / "steam_autocloud.vdf").write_text("x")

    def save(self, stem, modified, picture=True):
        (self.saves / f"{stem}.sav").write_bytes(b"s" * modified)
        if picture:
            (self.saves / f"{stem}.png").write_bytes(b"p")
            (self.saves / f"{stem}.json").write_text("{}")
        for path in self.saves.glob(stem + ".*"):
            os.utime(path, (1_700_000_000 + modified, 1_700_000_000 + modified))


class SaveBackupTests(SavesTestCase):
    def test_newest_saves_with_their_companions(self):
        newest = newestSaves(str(self.saves), 2)
        self.assertEqual(
            sorted(newest),
            [
                "QuickSave_3.json",
                "QuickSave_3.png",
                "QuickSave_3.sav",
                "QuickSave_4.json",
                "QuickSave_4.png",
                "QuickSave_4.sav",
            ],
        )
        self.assertEqual(newest["QuickSave_4.sav"], [400, 1_700_000_400])
        self.assertEqual(newestSaves(str(self.root / "none")), {})
        self.assertEqual(gameSavesFolder(str(self.documents)), str(self.saves))
        self.assertEqual(gameSavesFolder(""), "")

    def test_backup_skips_unchanged_saves_and_lists_newest_first(self):
        with patch("src.core.saves.time.strftime", return_value="2026-10-11 10:00:00"):
            first = backupSaves(str(self.saves), str(self.backups), "before update", 2)
        self.assertEqual(first.count, 2)
        self.assertEqual(Path(first.directory).name, "2026-10-11 100000 before update")
        self.assertTrue((Path(first.directory) / "QuickSave_4.png").exists())
        self.assertIsNone(backupSaves(str(self.saves), str(self.backups), "before update", 2))
        self.save("AutoSave_5", 500, picture=False)
        with patch("src.core.saves.time.strftime", return_value="2026-10-11 11:00:00"):
            second = backupSaves(str(self.saves), str(self.backups), "manual", 2)
        self.assertEqual(
            sorted(second.saves), ["AutoSave_5.sav", "QuickSave_4.json", "QuickSave_4.png", "QuickSave_4.sav"]
        )
        listed = listSaveBackups(str(self.backups))
        self.assertEqual(
            [(b.created, b.reason, b.count) for b in listed],
            [("2026-10-11 11:00:00", "manual", 2), ("2026-10-11 10:00:00", "before update", 2)],
        )
        self.assertEqual(listed[1].size, 300 + 400 + 2 * 1 + 2 * 2)
        self.assertIsNone(backupSaves(str(self.root / "no saves"), str(self.backups), "manual"))

    def test_same_second_gets_its_own_folder_and_broken_ones_are_ignored(self):
        with patch("src.core.saves.time.strftime", return_value="2026-10-11 10:00:00"):
            backupSaves(str(self.saves), str(self.backups), "manual", 1)
            self.save("QuickSave_9", 900)
            second = backupSaves(str(self.saves), str(self.backups), "manual", 1)
        self.assertTrue(second.directory.endswith("manual (2)"))
        broken = self.backups / SAVES_FOLDER / "broken"
        broken.mkdir()
        (broken / MANIFEST).write_text("{")
        self.assertEqual(len(listSaveBackups(str(self.backups))), 2)
        self.assertEqual(listSaveBackups(""), [])

    def test_failed_copy_leaves_no_half_backup(self):
        with patch("src.core.saves.shutil.copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                backupSaves(str(self.saves), str(self.backups), "manual")
        self.assertEqual(os.listdir(self.backups / SAVES_FOLDER), [])

    def test_restore_never_replaces(self):
        backup = backupSaves(str(self.saves), str(self.backups), "manual", 2)
        (self.saves / "QuickSave_4.sav").unlink()
        (self.saves / "QuickSave_3.png").write_bytes(b"newer picture")
        copied, skipped = restoreSaves(backup, str(self.saves))
        self.assertEqual(copied, ["QuickSave_4.sav"])
        self.assertIn("QuickSave_3.png", skipped)
        self.assertEqual((self.saves / "QuickSave_3.png").read_bytes(), b"newer picture")
        self.assertEqual((self.saves / "QuickSave_4.sav").stat().st_size, 400)


class SaveBackupGuiTests(SavesTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def config(self, enabled="1"):
        return SimpleNamespace(
            settings=str(self.documents), backups=str(self.backups), backupsaves=enabled, archives="A"
        )

    def test_backup_before_update(self):
        widget = Mock()
        widget.backUpSaves = lambda reason: CustomMainWidget.backUpSaves(widget, reason)
        with patch("src.gui.main_widget.data.config", self.config()):
            self.assertTrue(CustomMainWidget.backUpSavesBeforeUpdate(widget))
            self.assertTrue(CustomMainWidget.backUpSavesBeforeUpdate(widget))
        lines = [c.args[0] for c in widget.output.call_args_list]
        self.assertIn("Backed up your 4 newest saves to", lines[0])
        self.assertEqual(lines[1], "Saves: nothing new to back up")
        self.assertEqual(listSaveBackups(str(self.backups))[0].reason, "before update")
        with patch("src.gui.main_widget.data.config", self.config("0")):
            widget.reset_mock()
            self.assertTrue(CustomMainWidget.backUpSavesBeforeUpdate(widget))
            widget.output.assert_not_called()

    def test_failed_backup_asks_whether_to_update(self):
        widget = Mock()
        widget.backUpSaves.side_effect = OSError("disk full")
        with (
            patch("src.gui.main_widget.data.config", self.config()),
            patch("src.gui.main_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as question,
        ):
            self.assertFalse(CustomMainWidget.backUpSavesBeforeUpdate(widget))
            question.return_value = QMessageBox.StandardButton.Yes
            self.assertTrue(CustomMainWidget.backUpSavesBeforeUpdate(widget))
        self.assertIn("disk full", question.call_args.args[2])

    def test_update_stops_when_the_user_says_so(self):
        widget = Mock()
        widget.backUpSavesBeforeUpdate.return_value = False
        with (
            patch("src.gui.main_widget.data.config", self.config()),
            patch("src.gui.main_widget.ModUpdater") as updater,
        ):
            CustomMainWidget.applyUpdates(widget, [("A", 5, Mock(), "a.zip", "", 0)])
            updater.return_value.update.assert_not_called()
            widget.backUpSavesBeforeUpdate.reset_mock()
            CustomMainWidget.applyUpdates(widget, [("A", 5, Mock(), "", "failed", 500)])
        widget.backUpSavesBeforeUpdate.assert_not_called()  # nothing downloaded, nothing to update

    def test_dialog_and_restore(self):
        backupSaves(str(self.saves), str(self.backups), "before update", 2)
        backups = listSaveBackups(str(self.backups))
        dialog = SaveBackupsDialog(None, backups, str(self.backups))
        item = dialog.tree.topLevelItem(0)
        self.assertEqual((item.text(1), item.text(2)), ("Before updating mods", "2"))
        self.assertIn("(1 backup)", dialog.windowTitle())
        called = []
        dialog.backUpNow = lambda: called.append(True)
        dialog.backupButton.click()
        self.assertEqual(called, [True])
        dialog.restoreButton.click()
        self.assertIs(dialog.chosen, backups[0])
        dialog.showBackups([])
        self.assertFalse(dialog.restoreButton.isEnabled())

        widget = Mock()
        (self.saves / "QuickSave_4.sav").unlink()
        with (
            patch("src.gui.main_widget.data.config", self.config()),
            patch("src.gui.main_widget.SaveBackupsDialog") as dialogClass,
        ):
            dialogClass.return_value.exec.return_value = True
            dialogClass.return_value.chosen = backups[0]
            CustomMainWidget.showSaveBackups(widget)
        self.assertTrue((self.saves / "QuickSave_4.sav").exists())
        self.assertIn("Copied back 1 file, 5 files already in the save folder", widget.output.call_args.args[0])

    def test_back_up_now_from_the_dialog(self):
        widget = Mock()
        widget.backUpSaves = lambda reason: CustomMainWidget.backUpSaves(widget, reason)
        dialog = Mock()
        with patch("src.gui.main_widget.data.config", self.config()):
            CustomMainWidget.saveBackupRequested(widget, dialog)
        shown = dialog.showBackups.call_args.args[0]
        self.assertEqual([backup.reason for backup in shown], ["manual"])
        self.assertTrue(all(isinstance(backup, SaveBackup) for backup in shown))
        self.assertEqual(json.loads((Path(shown[0].directory) / MANIFEST).read_text())["reason"], "manual")


if __name__ == "__main__":
    unittest.main()
