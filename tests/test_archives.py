import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.archives import (
    DUPLICATE,
    IN_USE,
    NOT_INSTALLED,
    OLDER,
    OTHER_FILE,
    PARTIAL,
    UNKNOWN,
    ArchiveInfo,
    moveToFolder,
    recycle,
    scanArchives,
)
from src.core.nexus import NexusInfo
from src.domain.mod import Mod
from src.gui.archives_dialog import MOVE, RECYCLE, ArchiveCleanupDialog, sizeText
from src.gui.main_widget import OLD_ARCHIVES, CustomMainWidget

CAT_NEW = "Cat Hood 8509 2.1 2026-10-01T21-39Z kQW7gSLtu.zip"
CAT_OLD = "Cat Hood 8509 2.0 2026-09-01T10-00Z abcdefghi.zip"
CAT_VARIANT = "Cat Hood Addon Hairstyles 8509 1.0 2026-08-01T10-00Z abcdefghj.zip"
RAIN_OLD = "True Rain 11603 1 2026-09-29T18-43Z Q81OXmK34.zip"
NOT_MINE = "Better Minimap 7289 0.5 2026-10-04T10-41Z ZqIodYXkq.zip"
TOOL = "Script Merger Remastered 13076 1.4 2026-10-04T05-07Z 6VaC3PRwq.zip"
LEGACY = "Lamp On Players Boat-2770-2-1-1776701864.7z"


def mod(name, modId=0, archive="", version=""):
    result = Mod()
    result._name = name
    result.nexus = NexusInfo(modId, version=version, archive=archive) if modId else None
    return result


class ScanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        for name, size in (
            (CAT_NEW, 10),
            (CAT_OLD, 20),
            (CAT_VARIANT, 5),
            (RAIN_OLD, 7),
            (NOT_MINE, 3),
            (TOOL, 4),
            (LEGACY, 6),
            ("Lamp On Players Boat (2).7z", 6),
            ("Better Minimap 7289 0.5 2026-10-04T10-41Z ZqIodYXkq (3).zip", 8),
            ("Cat Hood 8509 2.0 2026-09-01T10-00Z abcdefghi (2).zip", 20),
            ("Cat Hood 8509 2.1 2026-10-01T21-39Z kQW7gSLtu (2).zip", 11),
            ("random stuff.rar", 2),
            ("half.zip.part", 9),
            ("readme.txt", 1),
        ):
            (self.folder / name).write_bytes(b"x" * size)
        (self.folder / "W3MM settings").mkdir()
        (self.folder / "W3MM settings" / "inside.zip").write_bytes(b"x")
        self.model = SimpleNamespace(
            all=lambda: [
                mod("Cat Hood Down", 8509, CAT_NEW, "2.1"),
                mod("True Rain", 11603, "True Rain 11603 2 2026-10-08T10-00Z zzzzzzzzz.zip", "2"),
                mod("Lamp On Players Boat"),
            ]
        )

    def scan(self):
        return {info.name: info for info in scanArchives(str(self.folder), self.model, {13076: "Script Merger"})}

    def test_states(self):
        found = self.scan()
        self.assertEqual(
            {name: info.state for name, info in found.items()},
            {
                CAT_NEW: IN_USE,
                CAT_OLD: OLDER,
                CAT_VARIANT: OTHER_FILE,
                RAIN_OLD: OLDER,
                NOT_MINE: NOT_INSTALLED,
                "Better Minimap 7289 0.5 2026-10-04T10-41Z ZqIodYXkq (3).zip": NOT_INSTALLED,
                TOOL: NOT_INSTALLED,
                LEGACY: NOT_INSTALLED,
                "Lamp On Players Boat (2).7z": UNKNOWN,
                "Cat Hood 8509 2.0 2026-09-01T10-00Z abcdefghi (2).zip": DUPLICATE,
                "Cat Hood 8509 2.1 2026-10-01T21-39Z kQW7gSLtu (2).zip": OTHER_FILE,
                "random stuff.rar": UNKNOWN,
                "half.zip.part": PARTIAL,
            },
        )
        self.assertEqual(found[CAT_NEW].mod, "Cat Hood Down")
        self.assertEqual(found[CAT_OLD].note, "Version 2.0, 2.1 is installed")
        self.assertEqual(found[TOOL].note, "Script Merger download")
        self.assertEqual(found["Lamp On Players Boat (2).7z"].mod, "Lamp On Players Boat")
        self.assertEqual(found[CAT_OLD].size, 20)

    def test_only_the_safe_ones_are_suggested(self):
        suggested = sorted(name for name, info in self.scan().items() if info.suggested)
        self.assertEqual(
            suggested,
            [
                "Cat Hood 8509 2.0 2026-09-01T10-00Z abcdefghi (2).zip",
                CAT_OLD,
                "half.zip.part",
            ],
        )
        # the installed True Rain archive isn't in the folder, so the old one is its only copy
        self.assertTrue(self.scan()[RAIN_OLD].onlyCopy)
        self.assertIn("only copy", self.scan()[RAIN_OLD].note)

    def test_missing_folder(self):
        self.assertEqual(scanArchives(str(self.folder / "nope"), self.model), [])
        self.assertEqual(scanArchives("", self.model), [])

    def test_move_to_folder_never_overwrites(self):
        target = self.folder / OLD_ARCHIVES
        target.mkdir()
        (target / CAT_OLD).write_bytes(b"older copy")
        failed = moveToFolder([str(self.folder / CAT_OLD), str(self.folder / "gone.zip")], str(target))
        self.assertEqual([Path(path).name for path, _ in failed], ["gone.zip"])
        self.assertFalse((self.folder / CAT_OLD).exists())
        self.assertEqual((target / CAT_OLD).read_bytes(), b"older copy")
        self.assertEqual(len(list(target.iterdir())), 2)

    def test_recycle_uses_the_recycle_bin(self):
        first, second = self.folder / CAT_OLD, self.folder / NOT_MINE

        def operation(spec):
            if spec[2] == str(first):
                first.unlink()  # what the shell does for a recycled test file
                return 0, False
            return 5, False

        with patch("win32com.shell.shell.SHFileOperation", side_effect=operation) as shell:
            failed = recycle([str(first), str(second)])
        from win32com.shell import shellcon

        self.assertTrue(shell.call_args_list[0].args[0][4] & shellcon.FOF_ALLOWUNDO)
        self.assertEqual(shell.call_args_list[0].args[0][1], shellcon.FO_DELETE)
        self.assertEqual(failed, [(str(second), "Windows couldn't recycle it (code 5)")])
        with patch("src.core.archives.sys.platform", "linux"):
            self.assertEqual(len(recycle([str(second)])), 1)
        self.assertTrue(second.exists())


class CleanupGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.archives = [
            ArchiveInfo(str(self.folder / "used.zip"), 100, IN_USE, "A"),
            ArchiveInfo(str(self.folder / "b.zip"), 3 * 1024**2, NOT_INSTALLED),
            ArchiveInfo(str(self.folder / "a.zip"), 2 * 1024**3, OLDER, "A", "Version 1"),
            ArchiveInfo(str(self.folder / "c.zip"), 5, OLDER, "C", "only", onlyCopy=True),
        ]
        for archive in self.archives:
            Path(archive.path).write_bytes(b"x")

    def test_dialog(self):
        dialog = ArchiveCleanupDialog(None, str(self.folder), self.archives, OLD_ARCHIVES)
        items = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
        self.assertEqual(
            [(item.text(0), item.text(2), item.checkState(0) == Qt.CheckState.Checked) for item in items],
            [("a.zip", "Older version", True), ("c.zip", "Older version", False), ("b.zip", "Not installed", False)],
        )
        self.assertEqual(items[0].text(1), "2.0 GB")
        self.assertEqual(dialog.summary.text(), "Ticked: 1 file, 2.0 GB")
        items[0].setCheckState(0, Qt.CheckState.Unchecked)
        self.assertFalse(dialog.recycleButton.isEnabled())
        items[2].setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual(dialog.summary.text(), "Ticked: 1 file, 3.0 MB")
        dialog.moveButton.click()
        self.assertEqual((dialog.action, dialog.selection()), (MOVE, [self.archives[1]]))
        self.assertEqual((sizeText(1), sizeText(2048)), ("0 KB", "2 KB"))

    def handler(self, action, answer=QMessageBox.StandardButton.Yes, recycled=()):
        widget = Mock()
        config = SimpleNamespace(archives=str(self.folder), gameversion="re")
        with (
            patch("src.gui.main_widget.data.config", config),
            patch("src.gui.main_widget.scanArchives", return_value=self.archives) as scan,
            patch("src.gui.main_widget.ArchiveCleanupDialog") as dialog,
            patch("src.gui.main_widget.QMessageBox.question", return_value=answer) as question,
            patch("src.gui.main_widget.recycle", return_value=list(recycled)) as recycle_,
        ):
            dialog.return_value.exec.return_value = True
            dialog.return_value.action = action
            dialog.return_value.selection.return_value = self.archives[1:3]
            CustomMainWidget.cleanUpArchives(widget)
        self.assertEqual(scan.call_args.args[2], {13076: "Script Merger"})
        return widget, question, recycle_

    def test_move_handler(self):
        widget, question, recycle_ = self.handler(MOVE)
        question.assert_not_called()
        recycle_.assert_not_called()
        self.assertEqual(sorted(os.listdir(self.folder / OLD_ARCHIVES)), ["a.zip", "b.zip"])
        self.assertIn("Moved 2 archives to", widget.output.call_args.args[0])

    def test_recycle_handler_asks_first(self):
        widget, question, recycle_ = self.handler(RECYCLE, QMessageBox.StandardButton.No)
        recycle_.assert_not_called()
        widget, question, recycle_ = self.handler(RECYCLE, recycled=[(self.archives[1].path, "in use")])
        self.assertIn("Move 2 files (2.0 GB) to the Recycle Bin?", question.call_args.args[2])
        recycle_.assert_called_once_with([self.archives[1].path, self.archives[2].path])
        lines = [c.args[0] for c in widget.output.call_args_list]
        self.assertIn("> Moved 1 archive to the Recycle Bin", lines)
        self.assertIn("  b.zip: in use", lines)
        self.assertTrue(all(Path(archive.path).exists() for archive in self.archives))

    def test_nothing_to_do(self):
        widget = Mock()
        with patch("src.gui.main_widget.data.config", SimpleNamespace(archives=str(self.folder / "x"))):
            CustomMainWidget.cleanUpArchives(widget)
        self.assertIn("doesn't exist", widget.output.call_args.args[0])
        with (
            patch("src.gui.main_widget.data.config", SimpleNamespace(archives=str(self.folder), gameversion="re")),
            patch("src.gui.main_widget.scanArchives", return_value=self.archives[:1]),
            patch("src.gui.main_widget.ArchiveCleanupDialog") as dialog,
        ):
            CustomMainWidget.cleanUpArchives(widget)
        dialog.assert_not_called()
        self.assertIn("Nothing to clean up", widget.output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
