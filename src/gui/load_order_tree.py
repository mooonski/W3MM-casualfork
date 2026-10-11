'''The load order list, where enabled mods can be dragged into the order the game should load them'''

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem, QWidget


class LoadOrderTree(QTreeWidget):
    '''Emits moved(folder, index) when a mod is dropped at a new place; the owner works out the priorities and
    fills the list again'''

    moved = Signal(str, int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)

    @staticmethod
    def draggable(item: QTreeWidgetItem, allowed: bool) -> None:
        '''Mods can be dragged unless allowed is False; nothing can be dropped onto an item, only between them'''
        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if allowed:
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        item.setFlags(flags)

    def dropTarget(self, item: QTreeWidgetItem | None, position: QAbstractItemView.DropIndicatorPosition) -> int:
        '''The row the dragged mod should end up on'''
        if item is None or position == QAbstractItemView.DropIndicatorPosition.OnViewport:
            return self.topLevelItemCount()
        row = self.indexOfTopLevelItem(item)
        if position == QAbstractItemView.DropIndicatorPosition.BelowItem:
            row += 1
        return row

    def dropEvent(self, event) -> None:
        dragged = self.currentItem()
        if dragged is None or event.source() is not self:
            event.ignore()
            return
        target = self.dropTarget(self.itemAt(event.position().toPoint()), self.dropIndicatorPosition())
        source = self.indexOfTopLevelItem(dragged)
        # the list is filled again from the new priorities, so Qt mustn't move the item itself
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        if target > source:
            target -= 1
        if target != source:
            self.moved.emit(dragged.text(0), target)
