'''Tree Widget'''

import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QPalette
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTreeWidget, QTreeWidgetItem, QWidget

# holds the group name on a group heading in the mod list
GROUP_ROLE = Qt.ItemDataRole.UserRole + 7


class CustomTreeWidgetItem(QTreeWidgetItem):
    '''Tree Widget Item for proper ordering'''

    def __init__(self, parent: QTreeWidget | QTreeWidgetItem | list[str] | None = None):
        if parent is None:
            super().__init__()
        else:
            super().__init__(parent)

    def __lt__(self, otherItem):
        column = self.treeWidget().sortColumn()
        if not self.text(column) and not otherItem.text(column):
            return self.checkState(column).value < otherItem.checkState(column).value
        try:
            left = int(self.text(column)) if self.text(column) != "-" else sys.maxsize
            right = int(otherItem.text(column)) if otherItem.text(column) != "-" else sys.maxsize
            return left < right
        except ValueError:
            return self.text(column).lower() < otherItem.text(column).lower()


class GroupItem(CustomTreeWidgetItem):
    '''A heading in the mod list with the mods of one group under it. Headings keep the order the user gave the
    groups whichever column the list is sorted by; "" is the heading for mods without a group.'''

    def __init__(self, name: str, rank: int):
        super().__init__([""])
        self.group = name
        self.rank = rank
        self.setData(0, GROUP_ROLE, name)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDropEnabled
        if name:
            flags |= Qt.ItemFlag.ItemIsDragEnabled  # "Other mods" stays where it is
        self.setFlags(flags)
        font = self.font(0)
        font.setBold(True)
        self.setFont(0, font)
        app = QApplication.instance()
        palette = app.palette() if isinstance(app, QApplication) else QPalette()
        self.setBackground(0, QBrush(palette.color(QPalette.ColorRole.AlternateBase)))

    def __lt__(self, otherItem):
        if isinstance(otherItem, GroupItem):
            tree = self.treeWidget()
            descending = tree is not None and tree.header().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
            # Qt reverses the result for a descending sort, so reverse it first to keep the user's order
            return self.rank > otherItem.rank if descending else self.rank < otherItem.rank
        return super().__lt__(otherItem)


def isGroupItem(item: QTreeWidgetItem | None) -> bool:
    return item is not None and item.data(0, GROUP_ROLE) is not None


def groupOf(item: QTreeWidgetItem | None) -> str | None:
    """The separator a row belongs to: its own name for a heading, its heading's for a mod under one"""
    if item is None:
        return None
    if isGroupItem(item):
        return item.data(0, GROUP_ROLE)
    parent = item.parent()
    return parent.data(0, GROUP_ROLE) if isGroupItem(parent) else None


class ModTree(QTreeWidget):
    """The mod list. Selected mods can be dragged to a new place: between two mods to put them there, or onto a
    separator to put them at its end; a separator dragged onto another one takes its place. Only the list's own
    order changes, never the game's load order. The owner does the moving and fills the list again; files dragged
    in from outside are left to the window, which installs them."""

    modsMoved = Signal(list, str, int)  # mod names, separator ("" = none), place among its other mods (-1: end)
    separatorsMoved = Signal(list, int)  # dragged separators, place among the other separators (-1: last)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)  # drops can land between rows
        self.setDropIndicatorShown(True)
        self.setDragEnabled(True)

    def dropEvent(self, event) -> None:
        if event.source() is not self:
            event.ignore()
            return
        item = self.itemAt(event.position().toPoint())
        where = self.dropIndicatorPosition()
        # the list is filled again afterwards, so Qt mustn't move the rows itself
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        selected = {id(row) for row in self.selectedItems()}
        mods = [row.text(1) for row in self.modRows() if id(row) in selected]
        headings = [
            row.data(0, GROUP_ROLE) for row in self.headings() if id(row) in selected and row.data(0, GROUP_ROLE)
        ]
        if mods:
            target = self.modTarget(item, where, mods)
            if target is not None:
                self.modsMoved.emit(mods, *target)
        elif headings:
            position = self.separatorTarget(item, where, headings)
            if position is not None:
                self.separatorsMoved.emit(headings, position)

    def separatorTarget(self, item, where, moved: list[str]) -> int | None:
        """The place among the other separators for separators dropped at this row"""
        Position = QAbstractItemView.DropIndicatorPosition
        group = groupOf(item)
        if item is None or not group:
            return -1  # Other mods, its mods or below everything: last
        if group in moved:
            return None  # onto itself
        others = [heading.data(0, GROUP_ROLE) for heading in self.headings()]
        others = [name for name in others if name and name not in moved]
        index = others.index(group)
        if isGroupItem(item) and where in (Position.AboveItem, Position.OnItem):
            return index
        return index + 1  # below a separator, or onto one of its mods

    def headings(self) -> list:
        return [self.topLevelItem(i) for i in range(self.topLevelItemCount()) if isGroupItem(self.topLevelItem(i))]

    def modRows(self) -> list:
        """The mod rows in the order they are shown"""
        rows = []
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            if isGroupItem(item):
                rows += [item.child(child) for child in range(item.childCount())]
            else:
                rows.append(item)
        return rows

    def namesIn(self, group: str) -> list[str]:
        """The mods shown under a separator ("" is Other mods, or the whole list without separators)"""
        for heading in self.headings():
            if heading.data(0, GROUP_ROLE) == group:
                return [heading.child(i).text(1) for i in range(heading.childCount())]
        return [row.text(1) for row in self.modRows()] if not self.headings() and not group else []

    def modTarget(self, item, where, moved: list[str]) -> tuple[str, int] | None:
        """(separator, place among its other mods) for mods dropped at this row"""
        Position = QAbstractItemView.DropIndicatorPosition
        if item is None:
            return ("", -1)  # below everything: the end of the last group
        if isGroupItem(item):
            group = item.data(0, GROUP_ROLE)
            if where == Position.AboveItem:
                headings = self.headings()
                index = headings.index(item)
                if index > 0:
                    return (headings[index - 1].data(0, GROUP_ROLE), -1)
                return (group, 0)
            if where == Position.BelowItem and item.isExpanded():
                return (group, 0)
            return (group, -1)
        group = groupOf(item)
        if group is None:
            group = ""  # a list without separators
        if item.text(1) in moved:
            return None  # dropped onto itself
        others = [name for name in self.namesIn(group) if name not in moved]
        position = others.index(item.text(1)) if item.text(1) in others else len(others)
        if where in (Position.BelowItem, Position.OnItem):
            position += 1
        return (group, position)
