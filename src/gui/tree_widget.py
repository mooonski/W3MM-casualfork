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
    """The mod list. With separators shown, selected mods can be dragged onto a separator (or a mod under it) to
    move them there, and a separator onto another one to take its place. The owner does the moving and fills the
    list again; files dragged in from outside are left to the window, which installs them."""

    modsDropped = Signal(list, str)  # mod names, separator ("" = none)
    separatorDropped = Signal(str, str)  # dragged separator, separator it was dropped on

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(True)  # drops land on a row, never between rows
        self.setDropIndicatorShown(True)
        self.setDragEnabled(False)  # turned on while there are separators

    def dropEvent(self, event) -> None:
        if event.source() is not self:
            event.ignore()
            return
        target = groupOf(self.itemAt(event.position().toPoint()))
        # the list is filled again afterwards, so Qt mustn't move the rows itself
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        if target is None:
            return
        dragged = self.selectedItems()
        mods = [item.text(1) for item in dragged if not isGroupItem(item)]
        headings = [item.data(0, GROUP_ROLE) for item in dragged if isGroupItem(item) and item.data(0, GROUP_ROLE)]
        if mods:
            self.modsDropped.emit(mods, target)
        elif headings and headings[0] != target:
            self.separatorDropped.emit(headings[0], target)
