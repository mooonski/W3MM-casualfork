import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTreeWidgetItem

from src.core.groups import arrange, listKey
from src.domain.mod import Mod
from src.gui.main_widget import CustomMainWidget
from src.gui.tree_widget import GroupItem, ModTree, groupOf

Position = QAbstractItemView.DropIndicatorPosition


def mod(name, group="", listorder=0):
    result = Mod()
    result._name = name
    result.group = group
    result.listorder = listorder
    return result


class OrderTests(unittest.TestCase):
    def test_arrange(self):
        shown = ["A", "B", "C", "D"]
        self.assertEqual(arrange(shown, ["D"], 0), ["D", "A", "B", "C"])
        self.assertEqual(arrange(shown, ["A", "C"], 1), ["B", "A", "C", "D"])
        self.assertEqual(arrange(shown, ["B"], -1), ["A", "C", "D", "B"])
        self.assertEqual(arrange(shown, ["X"], 99), ["A", "B", "C", "D", "X"])

    def test_numbered_mods_first_then_a_to_z(self):
        mods = [mod("b"), mod("Zed", listorder=2), mod("a"), mod("Yak", listorder=1)]
        self.assertEqual([m.name for m in sorted(mods, key=listKey)], ["Yak", "Zed", "a", "b"])


class ModTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tree = ModTree()
        self.tree.setColumnCount(3)
        self.headings = {}
        self.rows = {}
        for rank, (name, mods) in enumerate(
            (("Visuals", ["Clouds", "Rain", "Fog"]), ("Combat", ["Blood"]), ("", ["Boat"]))
        ):
            heading = GroupItem(name, rank)
            self.tree.addTopLevelItem(heading)
            heading.setExpanded(True)
            self.headings[name] = heading
            for modName in mods:
                row = QTreeWidgetItem(["", modName, ""])
                heading.addChild(row)
                self.rows[modName] = row
        self.moves = []
        self.separators = []
        self.tree.modsMoved.connect(lambda names, group, position: self.moves.append((names, group, position)))
        self.tree.separatorsMoved.connect(lambda names, position: self.separators.append((names, position)))

    def drop(self, target, where=Position.OnItem, source=None):
        event = Mock()
        event.source.return_value = self.tree if source is None else source
        event.position.return_value.toPoint.return_value = QPoint(0, 0)
        self.tree.itemAt = lambda point: target
        self.tree.dropIndicatorPosition = lambda: where
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
        self.assertEqual(self.tree.namesIn("Visuals"), ["Clouds", "Rain", "Fog"])
        self.assertEqual(self.tree.namesIn("Nope"), [])

    def test_reordering_within_a_separator(self):
        self.select(self.rows["Fog"])
        event = self.drop(self.rows["Clouds"], Position.AboveItem)
        event.setDropAction.assert_called_once_with(Qt.DropAction.IgnoreAction)
        self.drop(self.rows["Clouds"], Position.BelowItem)
        self.drop(self.rows["Rain"], Position.OnItem)
        self.drop(self.rows["Fog"], Position.AboveItem)  # onto itself: nothing
        self.assertEqual(self.moves, [(["Fog"], "Visuals", 0), (["Fog"], "Visuals", 1), (["Fog"], "Visuals", 2)])
        self.assertEqual(self.tree.topLevelItem(0).child(2).text(1), "Fog", "the rows themselves stay put")

    def test_several_mods_keep_the_order_they_are_shown_in(self):
        self.select(self.rows["Fog"], self.rows["Clouds"])
        self.drop(self.rows["Blood"], Position.AboveItem)
        self.assertEqual(self.moves, [(["Clouds", "Fog"], "Combat", 0)])

    def test_dropping_on_separators_and_below_everything(self):
        self.select(self.rows["Boat"])
        self.drop(self.headings["Combat"], Position.OnItem)
        self.drop(self.headings["Combat"], Position.BelowItem)
        self.drop(self.headings["Combat"], Position.AboveItem)  # the end of the separator above
        self.drop(self.headings["Visuals"], Position.AboveItem)  # nothing above: the top of the first one
        self.drop(None, Position.OnViewport)
        self.assertEqual(
            self.moves,
            [
                (["Boat"], "Combat", -1),
                (["Boat"], "Combat", 0),
                (["Boat"], "Visuals", -1),
                (["Boat"], "Visuals", 0),
                (["Boat"], "", -1),
            ],
        )
        self.headings["Combat"].setExpanded(False)
        self.drop(self.headings["Combat"], Position.BelowItem)
        self.assertEqual(self.moves[-1], (["Boat"], "Combat", -1))

    def test_a_list_without_separators(self):
        tree = ModTree()
        tree.setColumnCount(2)
        rows = [QTreeWidgetItem(["", name]) for name in ("A", "B", "C")]
        tree.addTopLevelItems(rows)
        moves = []
        tree.modsMoved.connect(lambda *args: moves.append(args))
        rows[2].setSelected(True)
        event = Mock()
        event.source.return_value = tree
        tree.itemAt = lambda point: rows[0]
        tree.dropIndicatorPosition = lambda: Position.AboveItem
        tree.dropEvent(event)
        self.assertEqual(moves, [(["C"], "", 0)])
        self.assertTrue(tree.dragEnabled())

    def test_separators_follow_the_drop_line(self):
        self.select(self.headings["Combat"])
        self.drop(self.headings["Visuals"], Position.AboveItem)
        self.drop(self.headings["Visuals"], Position.OnItem)
        self.drop(self.headings["Visuals"], Position.BelowItem)
        self.drop(self.rows["Rain"], Position.AboveItem)  # among Visuals' mods: just after Visuals
        self.drop(self.headings[""], Position.AboveItem)  # Other mods: last
        self.drop(self.rows["Boat"], Position.BelowItem)
        self.drop(None, Position.OnViewport)
        self.drop(self.headings["Combat"], Position.AboveItem)  # onto itself: nothing
        self.drop(self.rows["Blood"], Position.BelowItem)  # onto its own mods: nothing
        self.assertEqual(
            self.separators,
            [(["Combat"], 0), (["Combat"], 0), (["Combat"], 1), (["Combat"], 1), (["Combat"], -1), (["Combat"], -1), (["Combat"], -1)],
        )  # fmt: skip
        self.assertEqual(self.moves, [])

    def test_several_separators_move_together(self):
        self.select(self.headings["Visuals"], self.headings["Combat"], self.headings[""])
        self.drop(None, Position.OnViewport)
        self.assertEqual(self.separators, [(["Visuals", "Combat"], -1)])

    def test_qt_can_start_dragging_a_separator(self):
        index = self.tree.indexFromItem(self.headings["Visuals"])
        self.assertTrue(self.tree.model().flags(index) & Qt.ItemFlag.ItemIsDragEnabled)
        self.assertIsNotNone(self.tree.model().mimeData([index]))
        self.assertTrue(self.tree.dragEnabled())

    def test_files_from_outside_are_left_to_the_window(self):
        event = self.drop(self.headings["Combat"], source=object())
        event.ignore.assert_called_once()
        self.assertEqual((self.moves, self.separators), ([], []))

    def test_only_named_separators_can_be_dragged(self):
        self.assertTrue(self.headings["Visuals"].flags() & Qt.ItemFlag.ItemIsDragEnabled)
        self.assertFalse(self.headings[""].flags() & Qt.ItemFlag.ItemIsDragEnabled)
        self.assertTrue(self.headings[""].flags() & Qt.ItemFlag.ItemIsDropEnabled)


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.mods = [mod("Clouds", "Visuals"), mod("Rain", "Visuals"), mod("Blood", "Combat"), mod("Boat")]
        self.widget = Mock()
        self.widget.model = SimpleNamespace(all=lambda: self.mods, modList={m.name: m for m in self.mods})
        shown = {"Visuals": ["Clouds", "Rain"], "Combat": ["Blood"], "": ["Boat"]}
        self.widget.treeWidget.namesIn = lambda group: list(shown.get(group, []))
        self.config = SimpleNamespace(groups=["Visuals", "Combat", "Quests"], listsort="")
        patcher = patch("src.gui.main_widget.data.config", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_moving_mods_sets_their_place_and_never_the_load_order(self):
        CustomMainWidget.modsMoved(self.widget, ["Boat", "Gone"], "Visuals", 1)
        self.assertEqual(
            [(m.name, m.group, m.listorder) for m in self.mods],
            [("Clouds", "Visuals", 1), ("Rain", "Visuals", 3), ("Blood", "Combat", 0), ("Boat", "Visuals", 2)],
        )
        self.assertEqual(self.config.listsort, "custom")
        self.widget.saveGroups.assert_called_once_with(["Visuals", "Combat", "Quests"])
        self.assertTrue(all(m.priority == "-" for m in self.mods))
        CustomMainWidget.modsMoved(self.widget, ["Gone"], "Visuals", 0)
        self.widget.saveGroups.assert_called_once()

    def test_sort_modes(self):
        CustomMainWidget.sortByMyOrder(self.widget)
        self.assertEqual(self.config.listsort, "custom")
        self.widget.refreshList.assert_called_once()
        CustomMainWidget.listSortChanged(self.widget, 3, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.config.listsort, "")
        CustomMainWidget.listSortChanged(self.widget, -1, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.config.listsort, "custom")

    def test_reordering_separators(self):
        order = lambda: self.widget.saveGroups.call_args.args[0]  # noqa: E731
        CustomMainWidget.separatorsMoved(self.widget, ["Quests"], 0)
        self.assertEqual(order(), ["Quests", "Visuals", "Combat"])
        CustomMainWidget.separatorsMoved(self.widget, ["Visuals"], -1)
        self.assertEqual(order(), ["Combat", "Quests", "Visuals"])
        CustomMainWidget.separatorsMoved(self.widget, ["Quests", "Visuals"], 1)
        self.assertEqual(order(), ["Combat", "Visuals", "Quests"])
        calls = self.widget.saveGroups.call_count
        CustomMainWidget.separatorsMoved(self.widget, ["Nope"], 0)
        CustomMainWidget.separatorsMoved(self.widget, ["Visuals"], 0)  # already there
        self.assertEqual(self.widget.saveGroups.call_count, calls)


class FoldAllTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_collapse_and_expand_every_separator(self):
        widget = Mock()
        widget.treeWidget = ModTree()
        for rank, name in enumerate(("Visuals", "Combat", "")):
            heading = GroupItem(name, rank)
            widget.treeWidget.addTopLevelItem(heading)
            heading.addChild(QTreeWidgetItem(["", f"mod {rank}"]))
            heading.setExpanded(True)
        folded = []
        widget.treeWidget.itemCollapsed.connect(folded.append)
        config = SimpleNamespace(collapsedgroups=["Combat"])
        with patch("src.gui.main_widget.data.config", config):
            CustomMainWidget.setAllFolded(widget, True)
            self.assertEqual(config.collapsedgroups, ["Visuals", "Combat", ""])
            self.assertFalse(any(h.isExpanded() for h in widget.treeWidget.headings()))
            CustomMainWidget.setAllFolded(widget, False)
            self.assertEqual(config.collapsedgroups, [])
            self.assertTrue(all(h.isExpanded() for h in widget.treeWidget.headings()))
        self.assertEqual(folded, [], "one save, not one per separator")

    def test_menu_entries_only_with_separators(self):
        from PySide6.QtWidgets import QMenu

        widget = Mock()
        widget.treeWidget = ModTree()
        menu = QMenu()
        CustomMainWidget.addFoldActions(widget, menu)
        self.assertEqual(menu.actions(), [])
        widget.treeWidget.addTopLevelItem(GroupItem("Visuals", 0))
        CustomMainWidget.addFoldActions(widget, menu)
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(labels, ["Collapse All Separators", "Expand All Separators"])
        menu.actions()[1].trigger()
        widget.setAllFolded.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
