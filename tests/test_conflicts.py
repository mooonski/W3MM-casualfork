import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from src.core.conflicts import (
    ENTRY_SIZE,
    BundleError,
    ConflictScan,
    FileConflict,
    bundleFiles,
    findConflicts,
    loadOrder,
)
from src.domain.mod import Mod
from src.gui.conflicts_dialog import FileConflictsDialog
from src.gui.main_widget import CustomMainWidget

HARPY = "characters\\npc_entities\\monsters\\harpy_lvl3__erynia.w2ent"
XML = "gameplay\\items\\def_item_weapons.xml"


def makeBundle(path: Path, names: list[str], padding: int = 0) -> None:
    '''A bundle with the given table of contents and no data'''
    toc = b""
    for name in names:
        entry = name.encode("utf-8").ljust(256, b"\0") + b"\x01" * (ENTRY_SIZE - 256)
        toc += entry
    toc += b"\0" * padding
    header = b"POTATO70" + struct.pack("<III", 32 + len(toc), 0, len(toc)) + b"\0" * 12
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + toc)


class ConflictTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.mods = Path(temporary.name) / "Mods"
        makeBundle(self.mods / "modDeathsReworked" / "content" / "blob0.bundle", ["strings.list", HARPY, XML])
        makeBundle(self.mods / "modDeathsReworked" / "content" / "buffers0.bundle", [HARPY.upper()])
        makeBundle(self.mods / "modmoreBlood" / "content" / "blob0.bundle", ["strings.list", HARPY], padding=96)
        makeBundle(self.mods / "modA" / "content" / "blob0.bundle", [XML, "a\\only.xbm"])
        makeBundle(self.mods / "mod0000_MergedFiles" / "content" / "blob0.bundle", [XML])
        makeBundle(self.mods / "~modDisabled" / "content" / "blob0.bundle", [HARPY])
        (self.mods / "modScriptsOnly" / "content" / "scripts").mkdir(parents=True)
        (self.mods / "modBroken" / "content").mkdir(parents=True)
        (self.mods / "modBroken" / "content" / "blob0.bundle").write_bytes(b"not a bundle at all, sorry")
        (self.mods / "readme.txt").write_text("x")
        self.priorities = {"mod0000_MergedFiles": "0", "modmoreBlood": "5", "modA": "", "modBroken": "x"}

    def order(self):
        return loadOrder(str(self.mods), self.priorities.get)

    def test_bundle_table_of_contents(self):
        names = bundleFiles(str(self.mods / "modmoreBlood" / "content" / "blob0.bundle"))
        self.assertEqual(names, ["strings.list", HARPY])
        with self.assertRaises(BundleError):
            bundleFiles(str(self.mods / "modBroken" / "content" / "blob0.bundle"))
        with self.assertRaises(BundleError):
            bundleFiles(str(self.mods / "missing.bundle"))

    def test_load_order_follows_priority_then_name(self):
        self.assertEqual(
            self.order(),
            ["mod0000_MergedFiles", "modmoreBlood", "modA", "modBroken", "modDeathsReworked", "modScriptsOnly"],
        )
        self.assertEqual(loadOrder(str(self.mods / "none"), self.priorities.get), [])

    def test_conflicts_winners_and_merges(self):
        scan = findConflicts(str(self.mods), self.order())
        self.assertEqual(scan.bundles, 6)
        self.assertEqual(len(scan.errors), 1)
        self.assertEqual(
            [(c.path, c.folders, c.merged, c.mergeable) for c in scan.conflicts],
            [
                (HARPY, ["modmoreBlood", "modDeathsReworked"], False, False),
                (XML, ["modA", "modDeathsReworked"], True, True),
            ],
        )
        self.assertEqual(
            {pair: len(files) for pair, files in scan.pairs().items()},
            {("modmoreBlood", "modDeathsReworked"): 1, ("modA", "modDeathsReworked"): 1},
        )
        # without a priority, Deaths Reworked would load before more Blood (alphabetically) and win
        del self.priorities["modmoreBlood"]
        first = findConflicts(str(self.mods), self.order()).conflicts[0]
        self.assertEqual(first.winner, "modDeathsReworked")
        self.assertEqual(first.losers, ["modmoreBlood"])


class ConflictGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_groups_by_winner_and_loser(self):
        scan = ConflictScan(
            [
                FileConflict(HARPY, ["modDeathsReworked", "modmoreBlood"]),
                FileConflict(XML, ["modA", "modDeathsReworked", "modmoreBlood"], merged=True),
                FileConflict("x\\y.csv", ["modA", "modDeathsReworked"]),
            ],
            ["Couldn't read modBroken"],
            7,
        )
        dialog = FileConflictsDialog(None, scan, {"moddeathsreworked": "Deaths Reworked", "moda": "modA"})
        self.assertIn("3 files packed by more than one mod, 1 of them merged by Script Merger", dialog.heading.text())
        self.assertEqual(
            dialog.rows(),
            [
                ("modA", "Deaths Reworked (modDeathsReworked)", "2 files"),
                ("modA", "modmoreBlood", "1 file"),
                ("Deaths Reworked (modDeathsReworked)", "modmoreBlood", "1 file"),
            ],
        )
        first = dialog.tree.topLevelItem(0)
        self.assertEqual(
            [(first.child(i).text(0), first.child(i).text(2)) for i in range(first.childCount())],
            [(XML, "merged by Script Merger"), ("x\\y.csv", "Script Merger can merge this")],
        )
        empty = FileConflictsDialog(None, ConflictScan(bundles=4), {})
        self.assertIn("No two mods pack the same file (4 bundles checked)", empty.heading.text())

    def test_handler_names_mods_by_folder(self):
        widget = Mock()
        mod = Mod(files=["modDeathsReworked", "~modOther"])
        mod._name = "Deaths Reworked"
        widget.model.all.return_value = [mod]
        widget.fileConflicts.return_value = ConflictScan()
        with patch("src.gui.main_widget.FileConflictsDialog") as dialog:
            CustomMainWidget.showFileConflicts(widget)
        self.assertEqual(
            dialog.call_args.args[2], {"moddeathsreworked": "Deaths Reworked", "modother": "Deaths Reworked"}
        )

    def test_scan_uses_the_game_load_order(self):
        widget = Mock()
        config = SimpleNamespace(mods="M", getPriority=lambda name: None)
        with (
            patch("src.gui.main_widget.data.config", config),
            patch("src.gui.main_widget.loadOrder", return_value=["modA"]) as order,
            patch("src.gui.main_widget.findConflicts", return_value="scan") as find,
        ):
            self.assertEqual(CustomMainWidget.fileConflicts(widget), "scan")
            order.assert_called_once_with("M", config.getPriority)
            find.assert_called_once_with("M", ["modA"])
            find.side_effect = OSError("denied")
            self.assertIsNone(CustomMainWidget.fileConflicts(widget))
        self.assertIn("denied", widget.output.call_args.args[0])
        with patch("src.gui.main_widget.data.config", SimpleNamespace(mods="")):
            self.assertIsNone(CustomMainWidget.fileConflicts(widget))


if __name__ == "__main__":
    unittest.main()
