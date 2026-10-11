'''Download history dialog'''

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.history import FAILED, INSTALLED, KEPT, UPDATE, UPDATED, WEBSITE, Download
from src.globals.constants import translate
from src.gui.themes import statusColor
from src.util.util import openFolder, openUrl, plural

RESULTS = {
    INSTALLED: ("Installed", ""),
    UPDATED: ("Updated", ""),
    KEPT: ("Downloaded, not installed", "muted"),
    FAILED: ("Failed", "problem"),
    WEBSITE: ("Opened on the website", "muted"),
}


class DownloadHistoryDialog(QDialog):
    '''Lists past Nexus downloads, newest first, and can install a kept archive again'''

    def __init__(self, parent: QWidget | None, entries: list[Download]):
        super().__init__(parent)
        self.setWindowTitle(translate("History", "Download History"))
        self.resize(1100, 560)
        self.entries = entries
        self.chosen: Download | None = None

        layout = QVBoxLayout(self)
        self.heading = QLabel(
            plural(len(entries), translate("History", "download"))
            + " "
            + translate("History", "from Nexus, newest first. Double-click a row to open its Nexus page."),
            self,
        )
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)

        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText(translate("History", "Filter by mod or file name"))
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.applyFilter)
        layout.addWidget(self.filter)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(
            [
                translate("History", "When"),
                translate("History", "Mod"),
                translate("History", "Version"),
                translate("History", "Result"),
                translate("History", "Archive"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(1, 300)
        for index, entry in enumerate(entries):
            label, color = RESULTS.get(entry.result, (entry.result, ""))
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.time)) if entry.time else ""
            name = entry.mod or entry.name or os.path.basename(entry.archive) or str(entry.modId)
            if entry.source == UPDATE and entry.result != UPDATED:
                label = translate("History", "Update") + " " + translate("History", label).lower()
            else:
                label = translate("History", label)
            item = QTreeWidgetItem([when, name, entry.version, label, os.path.basename(entry.archive)])
            if color:
                item.setForeground(3, QBrush(statusColor(color)))
            if entry.message:
                item.setToolTip(3, entry.message)
            item.setToolTip(4, entry.archive)
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            self.tree.addTopLevelItem(item)
        self.tree.itemDoubleClicked.connect(lambda item: self.openPage())
        self.tree.currentItemChanged.connect(lambda *_: self.updateButtons())
        layout.addWidget(self.tree)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.installButton = buttons.addButton(translate("History", "Install"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.folderButton = buttons.addButton(
            translate("History", "Show in Folder"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.pageButton = buttons.addButton(translate("History", "Nexus Page"), QDialogButtonBox.ButtonRole.ActionRole)
        self.installButton.clicked.connect(self.install)
        self.folderButton.clicked.connect(self.showFolder)
        self.pageButton.clicked.connect(self.openPage)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.updateButtons()

    def current(self) -> Download | None:
        item = self.tree.currentItem()
        if item is None or item.isHidden():
            return None
        return self.entries[int(item.data(0, Qt.ItemDataRole.UserRole))]

    def applyFilter(self, text: str) -> None:
        words = text.casefold().split()
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            haystack = " ".join(item.text(column) for column in (1, 4)).casefold()
            item.setHidden(not all(word in haystack for word in words))
        self.updateButtons()

    def updateButtons(self) -> None:
        entry = self.current()
        exists = entry is not None and entry.archiveExists
        self.installButton.setEnabled(exists)
        self.folderButton.setEnabled(exists)
        self.pageButton.setEnabled(entry is not None and bool(entry.url))

    def install(self) -> None:
        entry = self.current()
        if entry is not None and entry.archiveExists:
            self.chosen = entry
            self.accept()

    def showFolder(self) -> None:
        entry = self.current()
        if entry is not None and entry.archive:
            openFolder(entry.archive)

    def openPage(self) -> None:
        entry = self.current()
        if entry is not None and entry.url:
            openUrl(entry.url)
