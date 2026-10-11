import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from src.domain.mod import Mod
from src.gui.main_widget import CustomMainWidget
from src.gui.tree_widget import GroupItem, ModTree, groupOf


def mod(name, group=""):
    result = Mod()
    result._name = name
    result.group = group
    return result


class ModTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tree = ModTree()
        self.tree.setColumnCount(3)
        self.headings = {}
        self.rows = {}
        for rank, (name, mods) in enumerate((("Visuals", ["Clouds"]), ("Combat", ["Blood"]), ("", ["Boat"]))):
            heading = GroupItem(name, rank)
            self.tree.addTopLevelItem(heading)
            self.headings[name] = heading
            for modName in mods:
                row = QTreeWidgetItem(["", modName, ""])
                heading.addChild(row)
                self.rows[modName] = row
        self.mods = []
        self.separators = []
        self.tree.modsDropped.connect(lambda names, group: self.mods.append((names, group)))
        self.tree.separatorDropped.connect(lambda name, target: self.separators.append((name, target)))

    def drop(self, target, source=None):
        event = Mock()
        event.source.return_value = self.tree if source is None else source
        event.position.return_value.toPoint.return_value = QPoint(0, 0)
        self.tree.itemAt = lambda point: target
        self.tree.dropEvent(event)
        return event

    def select(self, *items):
        self.tree.clearSelection()
        for item in items:
            item.setSelected(True)

    def test_rows_belong_to_their_separator(self):
        self.assertEqual(groupOf(self.headings["Visuals"]), "Visuals")
        self.assertEqual(groupOf(self.rows["Blood"]), "Combat")
        self.assertEqual(groupOf(self.rows["Boat"]), "")
        self.assertIsNone(groupOf(None))
        self.assertIsNone(groupOf(QTreeWidgetItem(["", "loose"])))

    def test_mods_dropped_on_a_separator_or_a_mod_under_it(self):
        self.select(self.rows["Boat"], self.rows["Clouds"])
        event = self.drop(self.headings["Combat"])
        event.setDropAction.assert_called_once_with(Qt.DropAction.IgnoreAction)
        self.drop(self.rows["Blood"])
        self.drop(self.headings[""])
        self.drop(None)
        self.assertEqual([(sorted(names), group) for names, group in self.mods], [
            (["Boat", "Clouds"], "Combat"), (["Boat", "Clouds"], "Combat"), (["Boat", "Clouds"], ""),
        ])  # fmt: skip
        self.assertEqual(self.tree.topLevelItem(0).childCount(), 1, "the rows themselves stay put")

    def test_separator_dropped_on_another(self):
        self.select(self.headings["Visuals"])
        self.drop(self.headings["Combat"])
        self.drop(self.rows["Boat"])
        self.drop(self.headings["Visuals"])  # onto itself: nothing
        self.assertEqual(self.separators, [("Visuals", "Combat"), ("Visuals", "")])
        self.assertEqual(self.mods, [])

    def test_files_from_outside_are_left_to_the_window(self):
        event = self.drop(self.headings["Combat"], source=object())
        event.ignore.assert_called_once()
        self.assertEqual((self.mods, self.separators), ([], []))

    def test_only_named_separators_can_be_dragged(self):
        self.assertTrue(self.headings["Visuals"].flags() & Qt.ItemFlag.ItemIsDragEnabled)
        self.assertFalse(self.headings[""].flags() & Qt.ItemFlag.ItemIsDragEnabled)
        self.assertTrue(self.headings[""].flags() & Qt.ItemFlag.ItemIsDropEnabled)


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.mods = [mod("Clouds", "Visuals"), mod("Blood", "Combat"), mod("Boat")]
        self.widget = Mock()
        self.widget.model = SimpleNamespace(all=lambda: self.mods, modList={m.name: m for m in self.mods})
        self.config = SimpleNamespace(groups=["Visuals", "Combat", "Quests"])
        patcher = patch("src.gui.main_widget.data.config", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_moving_mods(self):
        CustomMainWidget.modsDroppedOnSeparator(self.widget, ["Boat", "Clouds", "Gone"], "Combat")
        self.assertEqual([m.group for m in self.mods], ["Combat", "Combat", "Combat"])
        self.widget.saveGroups.assert_called_once_with(["Visuals", "Combat", "Quests"])
        CustomMainWidget.modsDroppedOnSeparator(self.widget, ["Boat"], "Combat")  # already there
        self.widget.saveGroups.assert_called_once()
        CustomMainWidget.modsDroppedOnSeparator(self.widget, ["Boat"], "")
        self.assertEqual(self.mods[2].group, "")

    def test_reordering_separators(self):
        order = lambda: self.widget.saveGroups.call_args.args[0]  # noqa: E731
        CustomMainWidget.separatorDroppedOnSeparator(self.widget, "Visuals", "Quests")
        self.assertEqual(order(), ["Combat", "Quests", "Visuals"])
        CustomMainWidget.separatorDroppedOnSeparator(self.widget, "Quests", "Visuals")
        self.assertEqual(order(), ["Quests", "Visuals", "Combat"])
        CustomMainWidget.separatorDroppedOnSeparator(self.widget, "Visuals", "")
        self.assertEqual(order(), ["Combat", "Quests", "Visuals"])
        calls = self.widget.saveGroups.call_count
        CustomMainWidget.separatorDroppedOnSeparator(self.widget, "Nope", "Combat")
        self.assertEqual(self.widget.saveGroups.call_count, calls)


if __name__ == "__main__":
    unittest.main()
