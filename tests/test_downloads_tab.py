import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.archives import NOT_INSTALLED, OTHER_FILE, UNKNOWN, notInstalled, scanArchives
from src.core.nexus import NexusInfo
from src.core.nexusdetails import DetailsCache, ModDetails
from src.domain.mod import Mod
from src.gui.downloads_widget import ARCHIVE, DETAILS, DOWNLOADED, NAME, SIZE, VERSION, DownloadsWidget
from src.gui.main_widget import CustomMainWidget

CAT_NEW = "Cat Hood 8509 2.1 2026-10-01T21-39Z kQW7gSLtu.zip"
CAT_OLD = "Cat Hood 8509 2.0 2026-09-01T10-00Z abcdefghi.zip"
CAT_VARIANT = "Cat Hood Addon Hairstyles 8509 1.0 2026-08-01T10-00Z abcdefghj.zip"
MINIMAP = "Better Minimap 7289 0.5 2026-10-04T10-41Z ZqIodYXkq.zip"
GWENT = "Gwent Redux (Remastered 5.X Port) 13403 20 2026-10-11T12-48Z oDUKYHx0t.zip"
TOOL = "Script Merger Remastered 13076 1.4 2026-10-04T05-07Z 6VaC3PRwq.zip"
LAMP_COPY = "Lamp On Players Boat (2).7z"
RANDOM = "random stuff.rar"


def mod(name, modId=0, archive="", version=""):
    result = Mod()
    result._name = name
    result.nexus = NexusInfo(modId, version=version, archive=archive) if modId else None
    return result


class Folder(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        for index, (name, size) in enumerate(
            (
                (CAT_NEW, 10),
                (CAT_OLD, 20),
                (CAT_VARIANT, 5),
                (MINIMAP, 3 * 1024**2),
                (GWENT, 4),
                (TOOL, 4),
                (LAMP_COPY, 6),
                (RANDOM, 2),
                ("half.zip.part", 9),
            )
        ):
            (self.folder / name).write_bytes(b"x" * size)
            stamp = time.mktime((2026, 10, 1 + index, 12, 0, 0, 0, 0, -1))
            os.utime(self.folder / name, (stamp, stamp))
        self.model = SimpleNamespace(
            all=lambda: [mod("Cat Hood Down", 8509, CAT_NEW, "2.1"), mod("Lamp On Players Boat")]
        )

    def scan(self):
        return str(self.folder), scanArchives(str(self.folder), self.model, {13076: "Script Merger"})


class NotInstalledTests(Folder):
    def test_only_archives_nothing_was_installed_from(self):
        _, archives = self.scan()
        found = {archive.name: archive.state for archive in notInstalled(archives)}
        self.assertEqual(
            found,
            {CAT_VARIANT: OTHER_FILE, MINIMAP: NOT_INSTALLED, GWENT: NOT_INSTALLED, RANDOM: UNKNOWN},
        )
        # the tool, the archive in use, the older version, the copy of an installed mod and .part are left out
        self.assertEqual(
            {archive.name for archive in notInstalled(archives, otherFiles=False)}, {MINIMAP, GWENT, RANDOM}
        )
        self.assertTrue(next(archive for archive in archives if archive.name == TOOL).tool)


class WidgetTests(Folder):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        cache = tempfile.TemporaryDirectory()
        self.addCleanup(cache.cleanup)
        self.cache = DetailsCache(cache.name)
        self.cache.update({13403: ModDetails(13403, name="Gwent Redux", summary="Better gwent")})
        self.widget = DownloadsWidget(None, self.cache, self.scan, True)
        self.counts, self.wanted, self.installs = [], [], []
        self.widget.countChanged.connect(self.counts.append)
        self.widget.detailsWanted.connect(self.wanted.append)
        self.widget.installRequested.connect(self.installs.append)
        self.widget.refresh()

    def rows(self):
        tree = self.widget.tree
        return [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]

    def row(self, archive):
        return next(item for item in self.rows() if item.text(ARCHIVE) == archive)

    def test_lists_newest_download_first(self):
        self.assertEqual(
            [(item.text(NAME), item.text(VERSION)) for item in self.rows()],
            [
                ("random stuff", ""),
                ("Gwent Redux", "20"),  # the name from Nexus where it's known
                ("Better Minimap", "0.5"),
                ("Cat Hood Addon Hairstyles", "1.0"),
            ],
        )
        self.assertEqual(self.row(MINIMAP).text(SIZE), "3.0 MB")
        self.assertTrue(self.row(MINIMAP).text(DOWNLOADED).startswith("2026-10-04"))
        self.assertEqual(self.row(CAT_VARIANT).text(DETAILS), "Another file from the Nexus page of Cat Hood Down")
        self.assertEqual(self.row(GWENT).text(DETAILS), "Better gwent")
        self.assertEqual(self.row(RANDOM).text(DETAILS), "Not a Nexus download name")
        self.assertEqual(self.row(MINIMAP).text(DETAILS), "")
        self.assertEqual(self.counts, [4])
        self.assertEqual(self.wanted, [[7289, 8509]])
        self.assertIn("4 archives (3.0 MB) not installed, in", self.widget.status.text())
        self.widget.tree.sortByColumn(SIZE, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.rows()[-1].text(ARCHIVE), MINIMAP)

    def test_install_and_search(self):
        self.assertFalse(self.widget.installButton.isEnabled())
        self.row(GWENT).setSelected(True)
        self.row(MINIMAP).setSelected(True)
        self.assertEqual(self.widget.preview.title.text(), "Gwent Redux")
        self.widget.installButton.click()
        self.assertEqual(sorted(map(os.path.basename, self.installs[0])), sorted([GWENT, MINIMAP]))
        self.widget.searchBox.setText("minimap")
        self.assertEqual([item.text(NAME) for item in self.rows() if not item.isHidden()], ["Better Minimap"])
        self.assertEqual([archive.name for archive in self.widget.selection()], [MINIMAP])
        self.assertIn("1 shown", self.widget.status.text())

    def test_follows_the_folder(self):
        (self.folder / GWENT).unlink()
        self.model.all = lambda: [
            mod("Cat Hood Down", 8509, CAT_NEW, "2.1"),
            mod("Minimap", 7289, MINIMAP),
            mod("Lamp On Players Boat"),
        ]
        self.widget.refresh()
        self.assertEqual([item.text(ARCHIVE) for item in self.rows()], [RANDOM, CAT_VARIANT])
        self.assertEqual(self.counts[-1], 2)
        self.widget.otherFilesBox.setChecked(False)
        self.assertEqual([item.text(ARCHIVE) for item in self.rows()], [RANDOM])
        self.assertEqual(self.widget.watcher.directories(), [str(self.folder)])

    def test_recycle_asks_first(self):
        self.row(RANDOM).setSelected(True)
        with (
            patch("src.gui.downloads_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.No),
            patch("src.gui.downloads_widget.recycle") as recycle,
        ):
            self.widget.recycleButton.click()
        recycle.assert_not_called()
        with (
            patch(
                "src.gui.downloads_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
            ) as question,
            patch("src.gui.downloads_widget.recycle", return_value=[]) as recycle,
        ):
            self.widget.recycleButton.click()
        self.assertIn("Move 1 archive (0 KB) to the Recycle Bin?", question.call_args.args[2])
        recycle.assert_called_once_with([str(self.folder / RANDOM)])

    def test_unfinished_download_is_flagged(self):
        (self.folder / MINIMAP).write_bytes(b"")
        (self.folder / (MINIMAP[:-4] + ".abc.zip.part")).write_bytes(b"x")
        self.widget.refresh()
        self.assertIn("isn't finished yet", self.row(MINIMAP).text(DETAILS))

    def test_open_page(self):
        self.row(GWENT).setSelected(True)
        self.row(RANDOM).setSelected(True)
        with patch("src.gui.downloads_widget.openUrl") as openUrl:
            self.widget.pageButton.click()
        openUrl.assert_called_once_with("https://www.nexusmods.com/witcher3/mods/13403")
        self.assertFalse(self.widget.folderButton.isEnabled())


class MainWindowTests(unittest.TestCase):
    def test_tab_shows_the_count(self):
        widget = Mock()
        widget.tabs.indexOf.return_value = 1
        CustomMainWidget.downloadsCounted(widget, 9)
        widget.tabs.setTabText.assert_called_with(1, "Not Installed (9)")
        CustomMainWidget.downloadsCounted(widget, 0)
        widget.tabs.setTabText.assert_called_with(1, "Not Installed")

    def test_details_are_asked_for_once(self):
        widget = Mock(detailsAsked=set(), detailsWorker=None)
        CustomMainWidget.downloadDetailsWanted(widget, [1, 2])
        widget.refreshNexusDetails.assert_called_once_with(silent=True, modIds=[1, 2])
        CustomMainWidget.downloadDetailsWanted(widget, [2, 3])
        widget.refreshNexusDetails.assert_called_with(silent=True, modIds=[3])
        CustomMainWidget.downloadDetailsWanted(widget, [1, 3])
        self.assertEqual(widget.refreshNexusDetails.call_count, 2)


if __name__ == "__main__":
    unittest.main()
