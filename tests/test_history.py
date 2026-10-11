import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.core import history
from src.core.history import FAILED, INSTALLED, KEPT, LINK, UPDATE, UPDATED, WEBSITE, Download, DownloadHistory
from src.core.nexus import NexusInfo
from src.core.nxm import NxmLink
from src.core.updater import UpdateResult
from src.gui.history_dialog import DownloadHistoryDialog
from src.gui.main_widget import CustomMainWidget
from tests.test_nexus_gui import HandlerTestCase, mod, nexusFile


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = temporary.name

    def test_add_finish_and_reload(self):
        store = DownloadHistory(self.folder)
        first = store.add(Download(0, 5, 7, "Cat", "1.0", "C:/a/cat.zip"))
        second = store.add(Download(100, 6, source=UPDATE))
        self.assertGreater(first.time, 0)
        self.assertEqual(second.time, 100)
        store.finish(first, INSTALLED, mod="Cat Mod")
        again = DownloadHistory(self.folder)
        self.assertEqual([entry.modId for entry in again.entries], [6, 5])
        self.assertEqual((again.entries[1].result, again.entries[1].mod), (INSTALLED, "Cat Mod"))
        self.assertEqual(again.entries[1].url, "https://www.nexusmods.com/witcher3/mods/5")
        self.assertEqual(Download(0, 0).url, "")

    def test_keeps_the_newest_entries(self):
        store = DownloadHistory(self.folder)
        with patch.object(history, "MAX_ENTRIES", 3):
            for number in range(5):
                store.add(Download(number + 1, number))
        self.assertEqual([entry.modId for entry in DownloadHistory(self.folder).entries], [4, 3, 2])

    def test_unreadable_or_unwritable_file(self):
        Path(self.folder, "download_history.json").write_text("{broken")
        self.assertEqual(DownloadHistory(self.folder).entries, [])
        Path(self.folder, "download_history.json").write_text(json.dumps([{"time": 1, "modId": 2, "extra": 3}, 4]))
        self.assertEqual(DownloadHistory(self.folder).entries, [Download(1, 2)])
        store = DownloadHistory(self.folder)
        with patch("src.core.history.os.replace", side_effect=OSError("locked")):
            store.add(Download(5, 6))  # printed, not raised
        self.assertEqual(len(store.entries), 2)

    def test_archive_exists(self):
        archive = Path(self.folder, "a.zip")
        self.assertFalse(Download(1, 2, archive=str(archive)).archiveExists)
        archive.write_bytes(b"x")
        self.assertTrue(Download(1, 2, archive=str(archive)).archiveExists)
        self.assertFalse(Download(1, 2).archiveExists)


class RecordingTests(HandlerTestCase):
    def setUp(self):
        super().setUp()
        self.widget.downloadHistory = DownloadHistory(str(self.root / "Config"))

    def entries(self):
        return [(e.source, e.result, e.modId, e.mod, e.message) for e in self.widget.downloadHistory.entries]

    def test_updates_are_recorded(self):
        self.add(mod("A", NexusInfo(5)), mod("B", NexusInfo(6)))
        file = nexusFile(2, version="2.0")
        with patch("src.gui.main_widget.ModUpdater") as updater:
            updater.return_value.update.side_effect = [UpdateResult(True, False, "x"), UpdateResult(False, False, "")]
            CustomMainWidget.applyUpdates(
                self.widget,
                [
                    ("A", 5, file, str(self.root / "a.zip"), "", 0),
                    ("B", 6, file, str(self.root / "b.zip"), "", 0),
                    ("C", 7, file, "", "download failed", 500),
                    ("A", 5, file, "", "premium only", 403),
                ],
            )
        self.assertEqual(
            self.entries(),
            [
                (UPDATE, WEBSITE, 5, "A", "premium only"),
                (UPDATE, FAILED, 7, "C", "download failed"),
                (UPDATE, FAILED, 6, "B", "The update failed, see the log"),
                (UPDATE, UPDATED, 5, "A", ""),
            ],
        )
        self.assertEqual(self.widget.downloadHistory.entries[-1].version, "2.0")

    def test_links_are_recorded(self):
        file = nexusFile(7, name="Cat", version="1.1")
        link = NxmLink("witcher3", 5, 7)
        CustomMainWidget.nxmDownloaded(self.widget, (link, file, ""))
        answers = [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.Yes]
        self.add(mod("Cat Mod", NexusInfo(5, archive="cat.zip")))
        with patch("src.gui.main_widget.QMessageBox.question", side_effect=answers):
            CustomMainWidget.nxmDownloaded(self.widget, (link, file, "C:/archives/cat.zip"))
            self.widget.installModFiles.return_value = 1
            CustomMainWidget.nxmDownloaded(self.widget, (link, file, "C:/archives/cat.zip"))
            self.widget.installModFiles.return_value = 0
            CustomMainWidget.nxmDownloaded(self.widget, (link, None, "C:/archives/other.zip"))
        self.assertEqual(
            self.entries(),
            [
                (LINK, FAILED, 5, "", "Couldn't install it, see the log"),
                (LINK, INSTALLED, 5, "Cat Mod", ""),
                (LINK, KEPT, 5, "", ""),
                (LINK, WEBSITE, 5, "", ""),
            ],
        )
        self.assertEqual(self.widget.downloadHistory.entries[0].name, "other.zip")

    def test_history_dialog_installs_the_chosen_archive(self):
        entry = Download(1, 5, archive=str(self.root / "a.zip"))
        self.widget.downloadHistory.add(entry)
        with patch("src.gui.main_widget.DownloadHistoryDialog") as dialog:
            dialog.return_value.exec.return_value = True
            dialog.return_value.chosen = entry
            CustomMainWidget.showDownloadHistory(self.widget)
        self.assertEqual(dialog.call_args.args[1], [entry])
        self.widget.installModFiles.assert_called_once_with([entry.archive])


class HistoryDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.archive = Path(temporary.name, "Cat 5 1.1 2026-10-08T12-00Z abc.zip")
        self.archive.write_bytes(b"x")
        self.entries = [
            Download(1760000000, 5, 7, "Cat", "1.1", str(self.archive), LINK, INSTALLED, mod="Cat Mod"),
            Download(1760000000, 6, 8, "Dog", "2.0", "", UPDATE, FAILED, "download failed", "Dog Mod"),
            Download(1760000000, 0, 0, "", "", "C:/gone/x.zip", LINK, KEPT),
        ]

    def test_rows_buttons_and_filter(self):
        dialog = DownloadHistoryDialog(None, self.entries)
        items = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
        self.assertEqual(
            [(item.text(1), item.text(3)) for item in items],
            [("Cat Mod", "Installed"), ("Dog Mod", "Update failed"), ("x.zip", "Downloaded, not installed")],
        )
        self.assertEqual(items[1].toolTip(3), "download failed")
        self.assertTrue(dialog.installButton.isEnabled())
        dialog.tree.setCurrentItem(items[2])
        self.assertFalse(dialog.installButton.isEnabled())
        self.assertFalse(dialog.pageButton.isEnabled())
        dialog.tree.setCurrentItem(items[1])
        with patch("src.gui.history_dialog.openUrl") as opened:
            dialog.pageButton.click()
        opened.assert_called_once_with("https://www.nexusmods.com/witcher3/mods/6")
        dialog.filter.setText("cat")
        self.assertEqual([item.isHidden() for item in items], [False, True, True])
        self.assertIsNone(dialog.current())
        dialog.tree.setCurrentItem(items[0])
        with patch("src.gui.history_dialog.openFolder") as folder:
            dialog.folderButton.click()
        folder.assert_called_once_with(str(self.archive))
        dialog.installButton.click()
        self.assertIs(dialog.chosen, self.entries[0])


if __name__ == "__main__":
    unittest.main()
