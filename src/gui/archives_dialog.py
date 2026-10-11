'''Archive clean-up dialog'''

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.archives import DUPLICATE, IN_USE, NOT_INSTALLED, OLDER, OTHER_FILE, PARTIAL, UNKNOWN, ArchiveInfo
from src.globals.constants import translate
from src.gui.themes import statusColor
from src.util.util import openFolder, plural

STATES = {
    OLDER: ("Older version", ""),
    DUPLICATE: ("Duplicate copy", ""),
    PARTIAL: ("Unfinished download", ""),
    OTHER_FILE: ("Other file of an installed mod", "muted"),
    NOT_INSTALLED: ("Not installed", "muted"),
    UNKNOWN: ("Unknown", "muted"),
    IN_USE: ("In use", "muted"),
}

RECYCLE = 'recycle'
MOVE = 'move'


def sizeText(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GB"
    if size >= 1024**2:
        return f"{size / 1024**2:.1f} MB"
    return f"{max(size, 0) / 1024:.0f} KB"


class ArchiveCleanupDialog(QDialog):
    '''Lists the archives in the archive folder that may not be needed any more. Nothing happens until the user
    ticks archives and picks what to do with them.'''

    def __init__(self, parent: QWidget | None, folder: str, archives: list[ArchiveInfo], moveFolder: str):
        super().__init__(parent)
        self.setWindowTitle(translate("Archives", "Clean Up Archives"))
        self.resize(1150, 600)
        self.folder = folder
        self.archives = [archive for archive in archives if archive.state != IN_USE]
        self.action = ''

        layout = QVBoxLayout(self)
        inUse = len(archives) - len(self.archives)
        heading = QLabel(
            translate("Archives", "In")
            + f" {folder}: "
            + plural(inUse, translate("Archives", "archive"))
            + " "
            + translate("Archives", "in use by installed mods (not listed),")
            + " "
            + plural(len(self.archives), translate("Archives", "other file"))
            + ".\n"
            + translate(
                "Archives",
                "Older versions, duplicate copies and unfinished downloads are ticked. Installed mods don't need "
                "their archives to work; backups of updated mods are kept separately.",
            ),
            self,
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(
            [
                translate("Archives", "Archive"),
                translate("Archives", "Size"),
                translate("Archives", "Status"),
                translate("Archives", "Mod"),
                translate("Archives", "Details"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 420)
        header.resizeSection(3, 220)
        order = {state: index for index, state in enumerate(STATES)}
        for index, archive in sorted(
            enumerate(self.archives), key=lambda pair: (order.get(pair[1].state, 99), pair[1].name.lower())
        ):
            label, color = STATES.get(archive.state, (archive.state, ""))
            item = QTreeWidgetItem(
                [archive.name, sizeText(archive.size), translate("Archives", label), archive.mod, archive.note]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            item.setData(1, Qt.ItemDataRole.UserRole, archive.size)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setToolTip(0, archive.path)
            item.setToolTip(4, archive.note)
            item.setCheckState(0, Qt.CheckState.Checked if archive.suggested else Qt.CheckState.Unchecked)
            if color:
                item.setForeground(2, QBrush(statusColor(color)))
            self.tree.addTopLevelItem(item)
        self.tree.itemChanged.connect(lambda *_: self.updateSummary())
        layout.addWidget(self.tree)

        self.summary = QLabel(self)
        layout.addWidget(self.summary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.recycleButton = buttons.addButton(
            translate("Archives", "Move to Recycle Bin"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.moveButton = buttons.addButton(
            translate("Archives", "Move to") + f" '{moveFolder}'", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.folderButton = buttons.addButton(
            translate("Archives", "Open Folder"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.recycleButton.clicked.connect(lambda: self.finish(RECYCLE))
        self.moveButton.clicked.connect(lambda: self.finish(MOVE))
        self.folderButton.clicked.connect(lambda: openFolder(self.folder))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.updateSummary()

    def keyPressEvent(self, event):
        '''Space ticks or unticks all selected rows'''
        if event.key() == Qt.Key.Key_Space and self.tree.hasFocus():
            items = self.tree.selectedItems()
            if items:
                state = (
                    Qt.CheckState.Unchecked
                    if all(item.checkState(0) == Qt.CheckState.Checked for item in items)
                    else Qt.CheckState.Checked
                )
                for item in items:
                    item.setCheckState(0, state)
                return
        super().keyPressEvent(event)

    def selection(self) -> list[ArchiveInfo]:
        chosen = []
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item is not None and item.checkState(0) == Qt.CheckState.Checked:
                chosen.append(self.archives[int(item.data(0, Qt.ItemDataRole.UserRole))])
        return chosen

    def updateSummary(self) -> None:
        chosen = self.selection()
        self.summary.setText(
            translate("Archives", "Ticked:")
            + f" {plural(len(chosen), translate('Archives', 'file'))}, "
            + sizeText(sum(archive.size for archive in chosen))
        )
        self.recycleButton.setEnabled(bool(chosen))
        self.moveButton.setEnabled(bool(chosen))

    def finish(self, action: str) -> None:
        if self.selection():
            self.action = action
            self.accept()
