'''Tree Widget'''

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QPalette
from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem

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
        self.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
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
