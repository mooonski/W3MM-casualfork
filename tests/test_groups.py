import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu, QTreeWidget

from src.configuration.config import Configuration
from src.core.groups import addGroup, cleanName, moveGroup, orderedGroups, removeGroup, renameGroup
from src.domain.mod import Mod
from src.gui.main_widget import CustomMainWidget
from src.gui.tree_widget import GROUP_ROLE, GroupItem, isGroupItem


def mod(name, group="", enabled=True):
    result = Mod()
    result._name = name
    result.group = group
    result.enabled = enabled
    return result


class GroupLogicTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(cleanName("  Visual   overhauls \t"), "Visual overhauls")
        self.assertEqual(cleanName("   "), "")
        self.assertEqual(len(cleanName("x" * 200)), 60)

    def test_order_add_rename_remove_move(self):
        mods = [mod("A", "Combat"), mod("B", "visuals"), mod("C", "Audio"), mod("D")]
        order = orderedGroups(["Quests", "Combat", "Quests", ""], mods)
        self.assertEqual(order, ["Quests", "Combat", "Audio", "visuals"])
        self.assertEqual(addGroup(order, "Combat"), order)
        self.assertEqual(addGroup(order, "UI")[-1], "UI")
        order = renameGroup(mods, order, "visuals", "Visuals")
        self.assertEqual((mods[1].group, order), ("Visuals", ["Quests", "Combat", "Audio", "Visuals"]))
        order = renameGroup(mods, order, "Audio", "Combat")  # joins the two
        self.assertEqual(
            ([m.group for m in mods], order), (["Combat", "Visuals", "Combat", ""], ["Quests", "Combat", "Visuals"])
        )
        order = removeGroup(mods, order, "Combat")
        self.assertEqual(([m.group for m in mods], order), (["", "Visuals", "", ""], ["Quests", "Visuals"]))
        self.assertEqual(moveGroup(["A", "B", "C"], "C", -1), ["A", "C", "B"])
        self.assertEqual(moveGroup(["A", "B", "C"], "A", -1), ["A", "B", "C"])
        self.assertEqual(moveGroup(["A", "B", "C"], "B", 5), ["A", "C", "B"])
        self.assertEqual(moveGroup(["A"], "X", 1), ["A"])

    def test_configuration_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(Configuration, "write_config"):
            (Path(temporary) / "Documents").mkdir()
            config = Configuration(str(Path(temporary) / "Documents"), configPath=str(Path(temporary) / "Manager"))
            self.assertEqual((config.groups, config.collapsedgroups), ([], []))
            config.groups = ['Quests & "Story"', "100% Combat"]
            config.collapsedgroups = ["b", "a", "a", ""]
            self.assertEqual(config.groups, ['Quests & "Story"', "100% Combat"])
            self.assertEqual(config.collapsedgroups, ["", "a", "b"])
            config.set("GROUPS", "order", "not json")
            self.assertEqual(config.groups, [])
            config.set("GROUPS", "order", '{"a": 1}')
            self.assertEqual(config.groups, [])


class GroupListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.config = SimpleNamespace(groups=["Visuals", "Combat", "Empty"], collapsedgroups=["Combat"])
        patcher = patch("src.gui.main_widget.data.config", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mods = [mod("Clouds", "Visuals"), mod("Rain", "Visuals"), mod("Blood", "Combat"), mod("Boat")]
        self.widget = Mock()
        self.widget.searchString = ""
        self.widget.treeWidget = QTreeWidget()
        self.widget.treeWidget.setColumnCount(3)
        self.widget.treeWidget.setSortingEnabled(True)
        self.widget.model = SimpleNamespace(all=lambda: self.mods, modList={m.name: m for m in self.mods}, write=Mock())
        for name in (
            "modItems",
            "groupHeaders",
            "finishGroupHeaders",
            "getSelectedMods",
            "setModsGroup",
            "saveGroups",
            "askGroupName",
        ):
            setattr(self.widget, name, getattr(CustomMainWidget, name).__get__(self.widget))
        self.widget.refreshList = self.fill

    def fill(self):
        tree = self.widget.treeWidget
        tree.clear()
        headers = self.widget.groupHeaders()
        for m in self.mods:
            CustomMainWidget.addToList(
                self.widget, m.enabled, m.name, "-", 0, 0, 0, 0, 0, 0, "No", 0, "",
                parent=headers.get(m.group if m.group in headers else ""),
            )  # fmt: skip
        self.widget.finishGroupHeaders(headers)

    def headings(self):
        tree = self.widget.treeWidget
        return [
            (tree.topLevelItem(i).text(0), tree.topLevelItem(i).isHidden(), tree.topLevelItem(i).isExpanded())
            for i in range(tree.topLevelItemCount())
        ]

    def test_mods_sit_under_their_group_headings(self):
        self.fill()
        self.assertEqual(
            self.headings(),
            [
                ("Visuals  (2)", False, True),
                ("Combat  (1)", False, False),
                ("Empty  (0)", False, True),
                ("Other mods  (1)", False, True),
            ],
        )
        self.assertEqual([item.text(1) for item in self.widget.modItems()], ["Clouds", "Rain", "Blood", "Boat"])
        self.assertTrue(self.widget.treeWidget.rootIsDecorated())

    def test_headings_keep_their_order_when_sorting(self):
        self.fill()
        tree = self.widget.treeWidget
        tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        self.assertEqual([h[0] for h in self.headings()][:2], ["Visuals  (2)", "Combat  (1)"])
        visuals = tree.topLevelItem(0)
        self.assertEqual([visuals.child(i).text(1) for i in range(2)], ["Rain", "Clouds"])
        tree.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        self.assertEqual([h[0] for h in self.headings()][-1], "Other mods  (1)")

    def test_no_groups_means_a_plain_list(self):
        self.config.groups = []
        for m in self.mods:
            m.group = ""
        self.fill()
        tree = self.widget.treeWidget
        self.assertEqual(tree.topLevelItemCount(), 4)
        self.assertFalse(any(isGroupItem(tree.topLevelItem(i)) for i in range(4)))
        self.assertFalse(tree.rootIsDecorated())

    def test_search_hides_empty_groups(self):
        self.widget.searchString = "x"
        self.fill()
        self.assertTrue(self.headings()[2][1])

    def test_selection_ignores_headings_and_toggling_a_heading_does_nothing(self):
        self.fill()
        tree = self.widget.treeWidget
        tree.topLevelItem(0).setSelected(True)
        tree.topLevelItem(0).child(1).setSelected(True)
        self.assertEqual(self.widget.getSelectedMods(), ["Rain"])
        CustomMainWidget.modToggled(self.widget, tree.topLevelItem(0), 0)
        self.widget.output.assert_not_called()

    def test_folding_is_remembered(self):
        self.fill()
        heading = self.widget.treeWidget.topLevelItem(0)
        CustomMainWidget.groupFolded(self.widget, heading, True)
        self.assertEqual(self.config.collapsedgroups, ["Combat", "Visuals"])
        CustomMainWidget.groupFolded(self.widget, heading, False)
        self.assertEqual(self.config.collapsedgroups, ["Combat"])
        CustomMainWidget.groupFolded(self.widget, self.widget.modItems()[0], True)
        self.assertEqual(self.config.collapsedgroups, ["Combat"])

    def test_moving_mods_between_groups(self):
        self.fill()
        self.widget.treeWidget.topLevelItem(3).child(0).setSelected(True)  # Boat
        self.widget.setModsGroup("Boats & Water")
        self.assertEqual(self.mods[3].group, "Boats & Water")
        self.assertEqual(self.config.groups, ["Visuals", "Combat", "Empty", "Boats & Water"])
        self.widget.model.write.assert_called()
        self.assertIn(("Boats & Water  (1)", False, True), self.headings())
        boat = next(item for item in self.widget.modItems() if item.text(1) == "Boat")
        boat.setSelected(True)
        self.widget.setModsGroup("")
        self.assertEqual(self.mods[3].group, "")

    def test_new_rename_move_and_remove(self):
        self.fill()
        with patch("src.gui.main_widget.QInputDialog.getText", return_value=("  Quests  ", True)):
            CustomMainWidget.newGroup(self.widget)
        self.assertEqual(self.config.groups[-1], "Quests")
        with patch("src.gui.main_widget.QInputDialog.getText", return_value=("Fights", True)):
            CustomMainWidget.renameModGroup(self.widget, "Combat")
        self.assertEqual((self.mods[2].group, self.config.collapsedgroups), ("Fights", ["Fights"]))
        self.assertEqual(self.config.groups, ["Visuals", "Fights", "Empty", "Quests"])
        with patch("src.gui.main_widget.QInputDialog.getText", return_value=("", False)):
            CustomMainWidget.renameModGroup(self.widget, "Fights")
        self.assertEqual(self.config.groups[1], "Fights")
        CustomMainWidget.moveModGroup(self.widget, "Quests", -1)
        self.assertEqual(self.config.groups, ["Visuals", "Fights", "Quests", "Empty"])
        CustomMainWidget.removeModGroup(self.widget, "Visuals")
        self.assertEqual([m.group for m in self.mods], ["", "", "Fights", ""])
        self.assertEqual(self.config.groups, ["Fights", "Quests", "Empty"])
        self.assertIn("Removed the separator Visuals", self.widget.output.call_args.args[0])

    def test_menus(self):
        self.fill()
        self.widget.treeWidget.topLevelItem(0).child(0).setSelected(True)
        menu = CustomMainWidget.moveToGroupMenu(self.widget, None)
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(labels, ["Visuals", "Combat", "Empty", "New Separator...", "Remove from Separator"])
        self.assertTrue(menu.actions()[0].isChecked())
        menu.actions()[1].trigger()
        self.assertEqual(self.mods[0].group, "Combat")
        heading = QMenu()
        CustomMainWidget.fillGroupMenu(self.widget, heading, "Visuals")
        actions = {action.text(): action.isEnabled() for action in heading.actions() if not action.isSeparator()}
        self.assertFalse(actions["Move Separator Up"])
        self.assertTrue(actions["Move Separator Down"])
        ungrouped = QMenu()
        CustomMainWidget.fillGroupMenu(self.widget, ungrouped, "")
        self.assertEqual([action.text() for action in ungrouped.actions()], ["Add Separator"])

    def test_group_item(self):
        item = GroupItem("Visuals", 0)
        self.assertEqual(item.data(0, GROUP_ROLE), "Visuals")
        self.assertTrue(isGroupItem(item))
        self.assertTrue(isGroupItem(GroupItem("", 1)))  # the ungrouped heading
        self.assertFalse(isGroupItem(None))
        self.assertFalse(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertTrue(item.font(0).bold())


if __name__ == "__main__":
    unittest.main()
