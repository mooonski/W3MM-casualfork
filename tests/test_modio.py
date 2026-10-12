import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu, QTreeWidget

from src.core.modio import CONFIG_FILE, ModioMod, installedModioMods, readConfig, readState
from src.domain.mod import Mod
from src.gui.main_widget import CustomMainWidget
from src.gui.themes import statusColor
from src.gui.tree_widget import ModTree, isModioItem

# the game writes every mod under the same key
CONFIG = '''{
    "mod": {"modID": 5045491, "isEnabled": true, "priority": 1},
    "mod": {"modID": 4975617, "isEnabled": true, "priority": 0},
    "mod": {"modID": 777, "isEnabled": false, "priority": 2}
}'''


def stateEntry(modId, name, folder, size=1000):
    return {
        "ID": modId,
        "PathOnDisk": str(folder / "mods" / str(modId)),
        "SizeOnDisk": size,
        "Profile": {
            "name": name,
            "summary": f"{name} summary",
            "profile_url": f"https://mod.io/g/the-witcher-3/m/{modId}",
            "date_updated": 1789823532,
            "modfile": {"version": "2.0"},
            "submitted_by": {"display_name_portal": "Ard Carraigh", "username": "ArdCarraigh"},
        },
    }


def makeModio(root: Path) -> tuple[Path, Path]:
    '''A documents folder with modioModsConfig.json and a mod.io folder with two described mods, one listed mod
    only its info.json names, and one mod mod.io installed that the game's list doesn't mention'''
    documents, modio = root / "Documents", root / "mod.io"
    documents.mkdir()
    (documents / CONFIG_FILE).write_text(CONFIG, encoding="utf-8")
    for modId, folder in ((4975617, "modletholod"), (5045491, "mod_e3boat"), (777, "modnameless"), (888, "modextra")):
        (modio / "mods" / str(modId) / "mods" / folder / "content").mkdir(parents=True)
    info = {"modName": "Nameless From Info", "version": "0.3", "author": "Someone"}
    (modio / "mods" / "777" / "mods" / "modnameless" / "content" / "info.json").write_text(
        "﻿" + json.dumps(info), encoding="utf-8"
    )
    state = {"Mods": [stateEntry(4975617, "Letho LOD Extended", modio), stateEntry(5045491, "E3 Boat", modio, 2_000_000)]}
    state["Mods"].append({"ID": 888, "Profile": {"name": "Extra"}})
    (modio / "metadata").mkdir()
    (modio / "metadata" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return documents, modio


class ReaderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_config_keeps_every_mod_despite_the_repeated_key(self):
        documents, _ = makeModio(self.root)
        self.assertEqual(readConfig(str(documents / CONFIG_FILE)), [(5045491, True, 1), (4975617, True, 0), (777, False, 2)])

    def test_names_details_and_order(self):
        documents, modio = makeModio(self.root)
        mods = installedModioMods(str(documents), str(modio))
        self.assertEqual(
            [(m.modId, m.label, m.enabled, m.priority) for m in mods],
            [
                (4975617, "[MOD.IO] Letho LOD Extended", True, 0),
                (5045491, "[MOD.IO] E3 Boat", True, 1),
                (777, "[MOD.IO] Nameless From Info", False, 2),
                (888, "[MOD.IO] Extra", False, None),
            ],
        )
        letho = mods[0]
        self.assertEqual((letho.version, letho.author, letho.size), ("2.0", "Ard Carraigh", 1000))
        self.assertEqual(letho.url, "https://mod.io/g/the-witcher-3/m/4975617")
        self.assertEqual((letho.folders, letho.date), (["modletholod"], "2026-09-19 13:12:12"))
        self.assertEqual((mods[2].version, mods[2].author), ("0.3", "Someone"))

    def test_unknown_mod_gets_a_placeholder_name(self):
        documents = self.root / "Documents"
        documents.mkdir()
        (documents / CONFIG_FILE).write_text('{"mod": {"modID": 42, "isEnabled": true, "priority": 0}}')
        mods = installedModioMods(str(documents), str(self.root / "missing"))
        self.assertEqual([(m.label, m.size, m.folders) for m in mods], [("[MOD.IO] mod.io mod 42", 0, [])])

    def test_nothing_or_broken_files_mean_no_mods(self):
        documents = self.root / "Documents"
        documents.mkdir()
        self.assertEqual(installedModioMods(str(documents), str(self.root / "missing")), [])
        self.assertEqual(installedModioMods(None, str(self.root / "missing")), [])
        (documents / CONFIG_FILE).write_text("{broken")
        self.assertEqual(readConfig(str(documents / CONFIG_FILE)), [])
        (documents / CONFIG_FILE).write_text('{"mod": {"modID": "x"}, "mod": 5, "other": {"modID": 3}}')
        self.assertEqual(readConfig(str(documents / CONFIG_FILE)), [])
        (self.root / "metadata").mkdir()
        (self.root / "metadata" / "state.json").write_text("[1, 2]")
        self.assertEqual(readState(str(self.root)), {})


def mod(name):
    result = Mod()
    result._name = name
    return result


class ModListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        documents, modio = makeModio(Path(temporary.name))
        self.config = SimpleNamespace(settings=str(documents))
        for target, value in (
            ("src.gui.main_widget.data.config", self.config),
            ("src.gui.main_widget.installedModioMods", lambda folder: installedModioMods(folder, str(modio))),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.mods = [mod("Clouds")]
        self.widget = Mock()
        self.widget.searchString = ""
        self.widget.treeWidget = ModTree()
        self.widget.treeWidget.setColumnCount(16)
        self.widget.model = SimpleNamespace(all=lambda: self.mods, modList={m.name: m for m in self.mods})
        for name in ("addToList", "addModioRows", "showModio", "fillModioMenu", "modItems", "getSelectedMods"):
            setattr(self.widget, name, getattr(CustomMainWidget, name).__get__(self.widget))

    def fill(self):
        tree = self.widget.treeWidget
        tree.clear()
        for m in self.mods:
            self.widget.addToList(m.enabled, m.name, "-", 0, 0, 0, 0, 0, 0, "No", 0, "")
        self.widget.addModioRows(None)
        return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]

    def test_rows_are_tagged_blue_and_read_only(self):
        rows = self.fill()
        self.assertEqual(
            [row.text(1) for row in rows],
            ["Clouds", "[MOD.IO] Letho LOD Extended", "[MOD.IO] E3 Boat", "[MOD.IO] Nameless From Info", "[MOD.IO] Extra"],
        )
        letho = rows[1]
        self.assertTrue(isModioItem(letho))
        self.assertFalse(isModioItem(rows[0]))
        self.assertEqual((letho.text(2), letho.text(3), letho.text(12)), ("-", "1", "2.0"))
        self.assertEqual(letho.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(rows[3].checkState(0), Qt.CheckState.Unchecked)
        self.assertFalse(letho.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertFalse(letho.flags() & Qt.ItemFlag.ItemIsDragEnabled)
        self.assertFalse(letho.flags() & Qt.ItemFlag.ItemIsDropEnabled)
        for column in range(16):
            self.assertEqual(letho.background(column).style(), Qt.BrushStyle.NoBrush)
            self.assertEqual(letho.foreground(column).color(), statusColor("modio"))
        self.assertTrue(letho.font(1).bold())
        self.assertIn("by Ard Carraigh", letho.toolTip(1))
        self.assertIn("in the game's own mod menu (on, mod.io order 0)", letho.toolTip(5))

    def test_rows_are_never_treated_as_managed_mods(self):
        rows = self.fill()
        for row in rows:
            row.setSelected(True)
        self.assertEqual(self.widget.getSelectedMods(), ["Clouds"])
        self.assertEqual([row.text(1) for row in self.widget.modItems()], ["Clouds"])
        self.assertEqual([row.text(1) for row in self.widget.treeWidget.modRows()], ["Clouds"])
        self.widget.model.modList = {}
        CustomMainWidget.modToggled(self.widget, rows[1], 0)
        CustomMainWidget.modClicked(self.widget, rows[1], 15)
        self.widget.toggleEndorsement.assert_not_called()

    def test_search_filters_them_too(self):
        self.widget.searchString = "boat"
        self.assertEqual([row.text(1) for row in self.fill()], ["Clouds", "[MOD.IO] E3 Boat"])
        self.widget.searchString = "mod.io"
        self.assertEqual(len(self.fill()), 5)

    def test_right_click_opens_the_page_or_folder(self):
        rows = self.fill()
        menu = QMenu()
        self.widget.fillModioMenu(menu, rows[1])
        page, folder = menu.actions()
        self.assertEqual((page.text(), page.isEnabled(), folder.isEnabled()), ("Open mod.io Page", True, True))
        with patch("src.gui.main_widget.openUrl") as openUrl, patch("src.gui.main_widget.openFolder") as openFolder:
            page.trigger()
            folder.trigger()
        openUrl.assert_called_once_with("https://mod.io/g/the-witcher-3/m/4975617")
        self.assertTrue(openFolder.call_args.args[0].endswith("4975617"))
        menu = QMenu()
        self.widget.fillModioMenu(menu, rows[4])  # no page or folder known
        self.assertEqual([action.isEnabled() for action in menu.actions()], [False, True])


if __name__ == "__main__":
    unittest.main()
