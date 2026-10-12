'''The "Not Installed" tab: archives in the archive folder that nothing was installed from'''

import os
import time
from collections.abc import Callable

from PySide6.QtCore import QFileSystemWatcher, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.archives import OTHER_FILE, ArchiveInfo, notInstalled, recycle, unfinishedDownload
from src.core.nexus import parseArchiveName
from src.core.nexusdetails import GAME_DOMAIN, DetailsCache
from src.globals.constants import translate
from src.gui.archives_dialog import sizeText
from src.gui.mod_preview import ModPreview
from src.gui.themes import statusColor
from src.util.util import openFolder, openUrl, plural

SORT_ROLE = Qt.ItemDataRole.UserRole + 1

NAME, VERSION, DOWNLOADED, SIZE, ARCHIVE, DETAILS = range(6)


class SortItem(QTreeWidgetItem):
    '''Sorts by SORT_ROLE where a column has one (sizes, dates), otherwise by text'''

    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        left, right = self.data(column, SORT_ROLE), other.data(column, SORT_ROLE)
        if left is not None and right is not None:
            return left < right
        return self.text(column).casefold() < other.text(column).casefold()


class DownloadsWidget(QWidget):
    '''Lists the downloaded archives nothing was installed from, to install them or move them out of the way.
    scan returns (archive folder, every archive in it); the list follows the folder as files come and go.'''

    installRequested = Signal(list)  # archive paths
    countChanged = Signal(int)
    detailsWanted = Signal(list)  # Nexus mod ids without details yet

    def __init__(
        self,
        parent: QWidget | None,
        cache: DetailsCache,
        scan: Callable[[], tuple[str, list[ArchiveInfo]]],
        remastered: bool,
    ):
        super().__init__(parent)
        self.cache = cache
        self.scan = scan
        self.remastered = remastered
        self.folder = ''
        self.archives: list[ArchiveInfo] = []
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _: self.folderTimer.start())
        # a download writes the folder many times; look once it has settled
        self.folderTimer = QTimer(self)
        self.folderTimer.setSingleShot(True)
        self.folderTimer.setInterval(1500)
        self.folderTimer.timeout.connect(self.refresh)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        controls = QHBoxLayout()
        self.searchBox = QLineEdit(self)
        self.searchBox.setPlaceholderText(translate("Downloads", "Search"))
        self.searchBox.textChanged.connect(lambda _: self.filter())
        self.otherFilesBox = QCheckBox(translate("Downloads", "Include other files from installed mods' pages"), self)
        self.otherFilesBox.setToolTip(
            translate(
                "Downloads",
                "Archives from the Nexus page of a mod you have installed, but not the file you installed "
                "(an optional file or a patch, for example)",
            )
        )
        self.otherFilesBox.setChecked(True)
        self.otherFilesBox.toggled.connect(lambda _: self.refresh())
        self.refreshButton = QPushButton(translate("Downloads", "Refresh"), self)
        self.refreshButton.clicked.connect(self.refresh)
        controls.addWidget(self.searchBox, 1)
        controls.addWidget(self.otherFilesBox)
        controls.addWidget(self.refreshButton)
        layout.addLayout(controls)

        splitter = QSplitter(self)
        self.tree = QTreeWidget(splitter)
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(
            [
                translate("Downloads", "Mod"),
                translate("Downloads", "Version"),
                translate("Downloads", "Downloaded"),
                translate("Downloads", "Size"),
                translate("Downloads", "Archive"),
                translate("Downloads", "Details"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(DOWNLOADED, Qt.SortOrder.DescendingOrder)
        header = self.tree.header()
        header.setSectionResizeMode(NAME, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(NAME, 320)
        for column in (VERSION, DOWNLOADED, SIZE):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(ARCHIVE, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(ARCHIVE, 320)
        header.setStretchLastSection(True)
        self.tree.itemSelectionChanged.connect(self.showSelected)
        self.tree.itemDoubleClicked.connect(lambda *_: self.install())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.showMenu)
        self.preview = ModPreview(splitter)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([1000, 360])
        layout.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.folderButton = QPushButton(translate("Downloads", "Show in Folder"), self)
        self.folderButton.clicked.connect(self.showInFolder)
        self.pageButton = QPushButton(translate("Downloads", "Open on Nexus"), self)
        self.pageButton.clicked.connect(self.openPage)
        self.recycleButton = QPushButton(translate("Downloads", "Move to Recycle Bin"), self)
        self.recycleButton.clicked.connect(self.recycleSelected)
        self.installButton = QPushButton(translate("Downloads", "Install"), self)
        self.installButton.clicked.connect(self.install)
        bottom.addWidget(self.status, 1)
        for button in (self.folderButton, self.pageButton, self.recycleButton, self.installButton):
            bottom.addWidget(button)
        layout.addLayout(bottom)
        self.updateButtons()

    # ------------------------------------------------------------ the list

    def refresh(self) -> None:
        '''Reads the archive folder again, keeping the selection'''
        try:
            folder, archives = self.scan()
        except OSError as error:
            folder, archives = self.folder, []
            self.status.setText(translate("Downloads", "Couldn't read the archive folder:") + f" {error}")
        self.watch(folder)
        self.folder = folder
        self.archives = notInstalled(archives, self.otherFilesBox.isChecked())
        selected = {archive.path for archive in self.selection()}
        sorting = self.tree.isSortingEnabled()
        self.tree.setSortingEnabled(False)
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            for index, archive in enumerate(self.archives):
                item = self.makeItem(index, archive)
                self.tree.addTopLevelItem(item)
                item.setSelected(archive.path in selected)
        finally:
            self.tree.blockSignals(False)
            self.tree.setSortingEnabled(sorting)
        self.filter()
        self.showSelected()
        self.countChanged.emit(len(self.archives))
        wanted = sorted({modId for modId in map(self.modId, self.archives) if modId and not self.cache.get(modId)})
        if wanted:
            self.detailsWanted.emit(wanted)

    def watch(self, folder: str) -> None:
        if folder == self.folder:
            return
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        if folder and os.path.isdir(folder):
            self.watcher.addPath(folder)

    @staticmethod
    def modId(archive: ArchiveInfo) -> int:
        parsed = parseArchiveName(archive.name)
        return parsed[1] if parsed else 0

    def makeItem(self, index: int, archive: ArchiveInfo) -> QTreeWidgetItem:
        parsed = parseArchiveName(archive.name)
        details = self.cache.get(parsed[1]) if parsed else None
        name = details.name if details and details.name else parsed[0] if parsed else os.path.splitext(archive.name)[0]
        version = parsed[2] if parsed else ''
        try:
            stamp = os.path.getmtime(archive.path)
        except OSError:
            stamp = 0
        waiting = unfinishedDownload(archive.path)
        if waiting:
            note = waiting
        elif archive.state == OTHER_FILE and archive.mod:
            note = translate("Downloads", "Another file from the Nexus page of") + f" {archive.mod}"
        elif not parsed:
            note = translate("Downloads", "Not a Nexus download name")
        else:
            note = details.summary if details else ''
        item = SortItem(
            [
                name,
                version,
                time.strftime('%Y-%m-%d %H:%M', time.localtime(stamp)) if stamp else '',
                sizeText(archive.size),
                archive.name,
                note,
            ]
        )
        item.setData(NAME, Qt.ItemDataRole.UserRole, index)
        item.setData(DOWNLOADED, SORT_ROLE, stamp)
        item.setData(SIZE, SORT_ROLE, archive.size)
        item.setTextAlignment(SIZE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setToolTip(NAME, name)
        item.setToolTip(ARCHIVE, archive.path)
        item.setToolTip(DETAILS, note)
        if waiting:
            item.setForeground(DETAILS, QBrush(statusColor("warning")))
        elif archive.state == OTHER_FILE:
            item.setForeground(DETAILS, QBrush(statusColor("muted")))
        return item

    def filter(self) -> None:
        text = self.searchBox.text().strip().casefold()
        shown = 0
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item is None:
                continue
            hidden = bool(text) and text not in item.text(NAME).casefold() and text not in item.text(ARCHIVE).casefold()
            item.setHidden(hidden)
            shown += not hidden
        total = sum(archive.size for archive in self.archives)
        if not self.folder or not os.path.isdir(self.folder):
            self.status.setText(translate("Downloads", "The archive folder doesn't exist:") + f" {self.folder}")
            return
        message = (
            plural(len(self.archives), translate("Downloads", "archive"))
            + f" ({sizeText(total)}) "
            + translate("Downloads", "not installed, in")
            + f" {self.folder}"
        )
        if text:
            message += f" - {shown} " + translate("Downloads", "shown")
        self.status.setText(message)

    def selection(self) -> list[ArchiveInfo]:
        chosen = []
        for item in self.tree.selectedItems():
            index = item.data(NAME, Qt.ItemDataRole.UserRole)
            if not item.isHidden() and index is not None and 0 <= int(index) < len(self.archives):
                chosen.append(self.archives[int(index)])
        return chosen

    def showSelected(self) -> None:
        self.updateButtons()
        chosen = self.selection()
        if not chosen:
            self.preview.showMod(None, None, '')
            return
        archive = chosen[0]
        modId = self.modId(archive)
        details = self.cache.get(modId) if modId else None
        if details is not None:
            self.preview.showDetails(details, self.cache.image(details), self.remastered)
            return
        self.preview.showMod(None, None, '')
        self.preview.title.setText(archive.name)
        self.preview.summary.setText(
            translate("Downloads", "Nexus details haven't been loaded yet.")
            if modId
            else translate("Downloads", "Not a Nexus download name, so W3MM can't tell which mod page it's from.")
        )

    def updateButtons(self) -> None:
        chosen = self.selection()
        self.installButton.setEnabled(bool(chosen))
        self.recycleButton.setEnabled(bool(chosen))
        self.folderButton.setEnabled(len(chosen) == 1)
        self.pageButton.setEnabled(any(self.modId(archive) for archive in chosen))

    def showMenu(self, position: QPoint) -> None:
        if not self.selection():
            return
        menu = QMenu(self)
        menu.addAction(translate("Downloads", "Install"), self.install)
        menu.addSeparator()
        page = menu.addAction(translate("Downloads", "Open on Nexus"), self.openPage)
        page.setEnabled(self.pageButton.isEnabled())
        folder = menu.addAction(translate("Downloads", "Show in Folder"), self.showInFolder)
        folder.setEnabled(self.folderButton.isEnabled())
        menu.addSeparator()
        menu.addAction(translate("Downloads", "Move to Recycle Bin"), self.recycleSelected)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    # ------------------------------------------------------------ actions

    def install(self) -> None:
        chosen = self.selection()
        if chosen:
            self.installRequested.emit([archive.path for archive in chosen])

    def openPage(self) -> None:
        for modId in dict.fromkeys(self.modId(archive) for archive in self.selection()):
            if modId:
                openUrl(f"https://www.nexusmods.com/{GAME_DOMAIN}/mods/{modId}")

    def showInFolder(self) -> None:
        chosen = self.selection()
        if len(chosen) != 1:
            return
        target = os.path.normpath(chosen[0].path)
        if os.name == 'nt' and os.path.isfile(target):
            import subprocess

            subprocess.Popen(['explorer', '/select,', target])
        else:
            openFolder(os.path.dirname(target))

    def recycleSelected(self) -> None:
        chosen = self.selection()
        if not chosen:
            return
        size = sizeText(sum(archive.size for archive in chosen))
        names = '\n'.join(archive.name for archive in chosen[:10])
        if len(chosen) > 10:
            names += '\n...'
        answer = QMessageBox.question(
            self,
            translate("Downloads", "Move to Recycle Bin"),
            translate("Downloads", "Move")
            + f" {plural(len(chosen), translate('Downloads', 'archive'))} ({size}) "
            + translate("Downloads", "to the Recycle Bin?")
            + f"\n\n{names}\n\n"
            + translate("Downloads", "They can be restored from there."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        failed = recycle([archive.path for archive in chosen])
        if failed:
            QMessageBox.warning(
                self,
                translate("Downloads", "Move to Recycle Bin"),
                '\n'.join(f"{os.path.basename(archive)}: {error}" for archive, error in failed),
            )
        self.refresh()
