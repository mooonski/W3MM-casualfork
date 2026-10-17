import configparser
import random
import tempfile
import unittest
import xml.etree.ElementTree as XML
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTreeWidgetItem

from src.core.conflicts import loadOrder
from src.core.loadorder import moveFolder, prioritiesFor
from src.core.model import Model
from src.domain.mod import Mod
from src.gui.load_order_tree import LoadOrderTree
from src.gui.main_widget import CustomMainWidget, priorityNumber

MERGED = "mod0000_MergedFiles"


class PriorityTests(unittest.TestCase):
    def test_move_keeps_merged_files_first(self):
        order = [MERGED, "modA", "modB", "modC"]
        self.assertEqual(moveFolder(order, "modC", 1), [MERGED, "modC", "modA", "modB"])
        self.assertEqual(moveFolder(order, "modC", 0), [MERGED, "modC", "modA", "modB"])
        self.assertEqual(moveFolder(order, "modA", 99), [MERGED, "modB", "modC", "modA"])
        self.assertEqual(moveFolder(["modA", "modB"], "modB", 0), ["modB", "modA"])

    def test_only_what_is_needed_gets_a_number(self):
        order = [MERGED, "modC", "modA", "modB", "modD"]
        current = {MERGED: 0, "modA": None, "modB": None, "modC": None, "modD": None}
        self.assertEqual(prioritiesFor(order, current, "modC"), {MERGED: 0, "modC": 1})
        current["modB"] = 7
        self.assertEqual(prioritiesFor(order, current, "modC"), {MERGED: 0, "modC": 1, "modA": 2, "modB": 3})

    def test_the_game_loads_what_was_arranged(self):
        '''Whatever is dragged where, the game's own sorting of the new priorities gives the arranged order'''
        generator = random.Random(3)
        names = [MERGED] + [f"mod{letter}" for letter in "ABCDEFGHIJ"]
        for _ in range(120):
            priorities = {name: None for name in names}
            priorities[MERGED] = 0
            for name in generator.sample(names[1:], generator.randint(0, 5)):
                priorities[name] = generator.randint(0, 20)
            shown = loadOrder_(names, priorities)
            folder = generator.choice(names[1:])
            arranged = moveFolder(shown, folder, generator.randint(0, len(names)))
            for name, value in prioritiesFor(arranged, priorities, folder).items():
                priorities[name] = value
            self.assertEqual(loadOrder_(names, priorities), arranged)

    def test_priority_numbers(self):
        self.assertEqual([priorityNumber(v) for v in ("3", None, "", " ", "x")], [3, None, None, None, None])


def loadOrder_(names, priorities):
    with tempfile.TemporaryDirectory() as folder:
        for name in names:
            Path(folder, name).mkdir()
        return loadOrder(folder, lambda name: None if priorities[name] is None else str(priorities[name]))


class LoadOrderTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tree(self, *names):
        tree = LoadOrderTree()
        for name in names:
            item = QTreeWidgetItem([name, ""])
            LoadOrderTree.draggable(item, not name.startswith("~") and name != MERGED)
            tree.addTopLevelItem(item)
        return tree

    def test_flags_and_drop_rows(self):
        tree = self.tree(MERGED, "modA", "modB", "~modOff")
        flags = [tree.topLevelItem(i).flags() for i in range(4)]
        self.assertEqual([bool(f & Qt.ItemFlag.ItemIsDragEnabled) for f in flags], [False, True, True, False])
        self.assertFalse(any(f & Qt.ItemFlag.ItemIsDropEnabled for f in flags))
        Position = QAbstractItemView.DropIndicatorPosition
        self.assertEqual(tree.dropTarget(tree.topLevelItem(1), Position.AboveItem), 1)
        self.assertEqual(tree.dropTarget(tree.topLevelItem(1), Position.BelowItem), 2)
        self.assertEqual(tree.dropTarget(None, Position.OnViewport), 4)

    def test_drop_reports_the_move_and_leaves_the_list_alone(self):
        tree = self.tree(MERGED, "modA", "modB", "modC")
        moves = []
        tree.moved.connect(lambda folder, row: moves.append((folder, row)))
        tree.setCurrentItem(tree.topLevelItem(3))
        event = Mock()
        event.source.return_value = tree
        event.position.return_value.toPoint.return_value = QPoint(0, 0)
        tree.itemAt = lambda point: tree.topLevelItem(1)
        tree.dropIndicatorPosition = lambda: QAbstractItemView.DropIndicatorPosition.AboveItem
        tree.dropEvent(event)
        self.assertEqual(moves, [("modC", 1)])
        event.setDropAction.assert_called_once_with(Qt.DropAction.IgnoreAction)
        self.assertEqual(tree.topLevelItem(3).text(0), "modC")
        # dropping below itself counts the row it leaves
        tree.setCurrentItem(tree.topLevelItem(1))
        tree.itemAt = lambda point: tree.topLevelItem(2)
        tree.dropIndicatorPosition = lambda: QAbstractItemView.DropIndicatorPosition.BelowItem
        tree.dropEvent(event)
        self.assertEqual(moves[-1], ("modA", 2))
        # dropping a mod where it already is does nothing
        tree.dropIndicatorPosition = lambda: QAbstractItemView.DropIndicatorPosition.AboveItem
        tree.itemAt = lambda point: tree.topLevelItem(1)
        tree.dropEvent(event)
        self.assertEqual(len(moves), 2)
        other = Mock()
        other.source.return_value = None
        tree.dropEvent(other)
        other.ignore.assert_called_once()

    def test_select_all_and_copy(self):
        tree = self.tree(MERGED, "modA", "modB", "~modOff")
        tree.selectAll()
        self.assertEqual(tree.copiedText(), "\n".join([MERGED, "modA", "modB", "~modOff"]))
        clipboard = Mock()  # never the real one
        tree.clearSelection()
        tree.topLevelItem(2).setSelected(True)
        tree.topLevelItem(1).setSelected(True)
        with unittest.mock.patch("src.gui.load_order_tree.QApplication.clipboard", return_value=clipboard):
            QTest.keyClick(tree, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        clipboard.setText.assert_called_once_with("modA\nmodB")

    def test_ctrl_a_follows_the_focus(self):
        widget = Mock()
        widget.loadOrder.hasFocus.return_value = True
        CustomMainWidget.selectAllMods(widget)
        widget.loadOrder.selectAll.assert_called_once()
        widget.treeWidget.selectAll.assert_not_called()
        widget.loadOrder.hasFocus.return_value = False
        CustomMainWidget.selectAllMods(widget)
        widget.treeWidget.selectAll.assert_called_once()

    def test_handler_writes_priorities_and_updates_the_mod_list(self):
        widget = Mock()
        widget.loadOrder = self.tree(MERGED, "modA", "~modOff", "modB", "modC")
        priorities = {MERGED: "0"}
        config = SimpleNamespace(
            getPriority=priorities.get,
            setPriority=lambda name, value: priorities.__setitem__(name, value),
            write_priority=Mock(),
        )
        mod = Mod(files=["modC"])
        mod._name = "C"
        widget.model.all.return_value = [mod]
        with unittest.mock.patch("src.gui.main_widget.data.config", config):
            CustomMainWidget.loadOrderMoved(widget, "modC", 1)
            self.assertEqual(priorities, {MERGED: "0", "modC": "1"})
            self.assertEqual(mod.priority, "1")
            config.write_priority.assert_called_once()
            widget.refreshList.assert_called_once()
            self.assertEqual(widget.loadOrderFocus, "modC")
            self.assertIn("modC now loads 2.", widget.output.call_args.args[0])
            # nothing changes when the order is already like that
            widget.loadOrder = self.tree(MERGED, "modC", "modA", "modB")
            CustomMainWidget.loadOrderMoved(widget, "modC", 0)
            config.write_priority.assert_called_once()
            CustomMainWidget.loadOrderMoved(widget, "~modOff", 0)
            config.write_priority.assert_called_once()


class PrioritiesConfig:
    '''Just the mods.settings side of the configuration'''

    def __init__(self, text: str = ""):
        self.priority = configparser.ConfigParser()
        self.priority.read_string(text)
        self.readPriority = Mock()
        self.write_priority = Mock()
        self.elapsed = 10_000.0

    def getPriority(self, section):
        return self.priority.get(section, "priority", fallback=None)

    def setPriority(self, section, value):
        if not self.priority.has_section(section):
            self.priority.add_section(section)
        self.priority.set(section, "priority", value)

    def removePriority(self, section):
        self.priority.remove_section(section)

    def write_priority_elapsed(self):
        return self.elapsed


class PrioritySyncTests(unittest.TestCase):
    '''The mod list shows the priorities from mods.settings, which the game uses'''

    def setUp(self):
        self.config = PrioritiesConfig("[modA]\nEnabled=1\nPriority=5\n\n[modB]\nEnabled=1\nPriority=x\n")
        configPatch = unittest.mock.patch("src.globals.data.config", self.config)
        configPatch.start()
        self.addCleanup(configPatch.stop)

    def test_sync_takes_the_number_without_writing(self):
        mod = Mod(files=["modA"], _priority="2")
        mod.syncPriority()
        self.assertEqual(mod.priority, "5")
        for files in (["modB"], ["modMissing"], []):
            mod = Mod(files=files, _priority="2")
            mod.syncPriority()
            self.assertEqual(mod.priority, "-")
        self.config.write_priority.assert_not_called()
        self.assertEqual(self.config.getPriority("modA"), "5")

    def test_check_only_fills_a_missing_priority(self):
        mod = Mod(files=["modA"])
        mod.checkPriority()
        self.assertEqual(mod.priority, "5")
        mod = Mod(files=["modA"], _priority="2")
        mod.checkPriority()
        self.assertEqual(mod.priority, "2")

    def test_loading_installed_xml_prefers_mods_settings(self):
        element = XML.fromstring('<mod name="A" enabled="True" priority="2"><data>modA</data></mod>')
        self.assertEqual(Model.populateModFromXml(Mod(), element).priority, "5")
        element = XML.fromstring('<mod name="C" enabled="True" priority="2"><data>modC</data></mod>')
        self.assertEqual(Model.populateModFromXml(Mod(), element).priority, "-")
        # without a mods.settings to go by, installed.xml's copy stays
        self.config.priority = configparser.ConfigParser()
        self.assertEqual(Model.populateModFromXml(Mod(), element).priority, "2")

    def test_reload_reads_the_file_and_refreshes_both_lists(self):
        widget = Mock()
        mods = [Mod(files=["modA"], _priority="2"), Mod(files=["modC"], _priority="7")]
        widget.model.all.return_value = mods
        CustomMainWidget.reloadPriorities(widget)
        self.config.readPriority.assert_called_once()
        self.assertEqual([mod.priority for mod in mods], ["5", "-"])
        widget.refreshList.assert_called_once_with()

    def test_outside_changes_reload_but_own_writes_dont(self):
        widget = Mock()
        CustomMainWidget.onModsSettingsChanged(widget)
        widget.reloadPrioritiesSoon.assert_called_once()
        self.config.elapsed = 100.0
        CustomMainWidget.onModsSettingsChanged(widget)
        widget.reloadPrioritiesSoon.assert_called_once()


if __name__ == "__main__":
    unittest.main()
