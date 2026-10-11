'''Browsing and searching Witcher 3 mods on Nexus'''

import time
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.nexusdetails import REMASTERED_RELEASE, DetailsCache, ModDetails, NexusGraphQL
from src.globals.constants import translate
from src.gui.mod_preview import ModPreview
from src.gui.nexus_dialogs import NexusWorker
from src.gui.tree_widget import CustomTreeWidgetItem
from src.util.util import openUrl

PAGE_SIZE = 25
SORT_OPTIONS = (
    ("relevance", "Best match"),
    ("endorsements", "Most endorsed"),
    ("downloads", "Most downloaded"),
    ("updatedAt", "Recently updated"),
    ("createdAt", "Newest"),
)


class BrowseWidget(QWidget):
    '''Search Nexus, see a mod's details and download it'''

    installRequested = Signal(object)

    def __init__(
        self,
        parent: QWidget | None,
        cache: DetailsCache,
        installedIds: Callable[[], set[int]],
        remastered: bool,
        graphql: Callable[[], NexusGraphQL] = NexusGraphQL,
    ):
        super().__init__(parent)
        self.cache = cache
        self.installedIds = installedIds
        self.remastered = remastered
        self.graphql = graphql
        self.results: list[ModDetails] = []
        self.total = 0
        self.query: tuple[str, str, int] = ('', 'relevance', 0)
        self.worker: NexusWorker | None = None
        self.imageWorker: NexusWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        self.searchBox = QLineEdit(self)
        self.searchBox.setPlaceholderText(translate("Browse", "Search Witcher 3 mods on Nexus"))
        self.searchBox.returnPressed.connect(self.search)
        self.sortBox = QComboBox(self)
        for key, label in SORT_OPTIONS:
            self.sortBox.addItem(translate("Browse", label), key)
        self.sortBox.currentIndexChanged.connect(lambda _: self.search())
        self.remasteredBox = QCheckBox(translate("Browse", "Updated for Remastered"), self)
        self.remasteredBox.setChecked(remastered)
        self.remasteredBox.toggled.connect(lambda _: self.search())
        self.searchButton = QPushButton(translate("Browse", "Search"), self)
        self.searchButton.clicked.connect(self.search)
        controls.addWidget(self.searchBox, 1)
        controls.addWidget(self.sortBox)
        controls.addWidget(self.remasteredBox)
        controls.addWidget(self.searchButton)
        layout.addLayout(controls)

        splitter = QSplitter(self)
        self.tree = QTreeWidget(splitter)
        self.tree.setColumnCount(8)
        self.tree.setHeaderLabels(
            [
                translate("Browse", "Mod"),
                translate("Browse", "Category"),
                translate("Browse", "Author"),
                translate("Browse", "Version"),
                translate("Browse", "Endorsements"),
                translate("Browse", "Downloads"),
                translate("Browse", "Updated"),
                translate("Browse", "Installed"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().resizeSection(0, 330)
        for column in range(1, 8):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self.showSelected)
        self.tree.itemDoubleClicked.connect(lambda _: self.openPage())
        self.preview = ModPreview(splitter)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([1000, 360])
        layout.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        self.status = QLabel(self)
        self.moreButton = QPushButton(translate("Browse", "Load More"), self)
        self.moreButton.clicked.connect(self.loadMore)
        self.openButton = QPushButton(translate("Browse", "Open on Nexus"), self)
        self.openButton.clicked.connect(self.openPage)
        self.installButton = QPushButton(translate("Browse", "Download && Install"), self)
        self.installButton.clicked.connect(self.requestInstall)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.moreButton)
        bottom.addWidget(self.openButton)
        bottom.addWidget(self.installButton)
        layout.addLayout(bottom)
        self.updateButtons()

    # ------------------------------------------------------------ searching

    def search(self) -> None:
        '''Starts a new search with the current text, sort and filter'''
        since = REMASTERED_RELEASE if self.remasteredBox.isChecked() else 0
        self.query = (self.searchBox.text().strip(), str(self.sortBox.currentData()), since)
        self.results = []
        self.total = 0
        self.tree.clear()
        self.fetch(0)

    def loadMore(self) -> None:
        self.fetch(len(self.results))

    def fetch(self, offset: int) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        text, sort, since = self.query
        graphql = self.graphql

        def work(progress):
            return graphql().search(text, sort, PAGE_SIZE, offset, since)

        self.status.setText(translate("Browse", "Searching..."))
        worker = NexusWorker(work, self)
        worker.succeeded.connect(lambda result: self.showResults(*result))
        worker.failed.connect(lambda message: self.status.setText(message))
        worker.finished.connect(self.updateButtons)
        self.worker = worker
        self.updateButtons()
        worker.start()

    def showResults(self, total: int, results: list[ModDetails]) -> None:
        self.total = total
        installed = self.installedIds()
        for details in results:
            if any(existing.modId == details.modId for existing in self.results):
                continue
            self.results.append(details)
            updated = time.strftime("%Y-%m-%d", time.gmtime(details.updated)) if details.updated else ''
            item = CustomTreeWidgetItem(
                [
                    details.name,
                    details.category,
                    details.author,
                    details.version,
                    str(details.endorsements),
                    str(details.downloads),
                    updated,
                    translate("Browse", "Yes") if details.modId in installed else '',
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, details.modId)
            for column in (4, 5):
                item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tree.addTopLevelItem(item)
        if self.total:
            self.status.setText(f"{len(self.results)} " + translate("Browse", "of") + f" {self.total} " + "mods")
        else:
            self.status.setText(translate("Browse", "No mods found."))
        if self.tree.topLevelItemCount() and not self.tree.selectedItems():
            first = self.tree.topLevelItem(0)
            if first is not None:
                first.setSelected(True)
        self.updateButtons()

    # ------------------------------------------------------------ selection

    def selected(self) -> ModDetails | None:
        items = self.tree.selectedItems()
        if not items:
            return None
        modId = items[0].data(0, Qt.ItemDataRole.UserRole)
        return next((details for details in self.results if details.modId == modId), None)

    def showSelected(self) -> None:
        details = self.selected()
        self.updateButtons()
        if details is None:
            return
        self.preview.showDetails(details, self.cache.image(details), self.remastered)
        if not self.cache.image(details) and details.thumbnailUrl:
            self.loadImage(details)

    def loadImage(self, details: ModDetails) -> None:
        if self.imageWorker is not None and self.imageWorker.isRunning():
            return
        cache = self.cache

        def work(progress):
            return cache.downloadImage(details.thumbnailUrl)

        worker = NexusWorker(work, self)
        worker.succeeded.connect(lambda _: self.imageLoaded(details))
        self.imageWorker = worker
        worker.start()

    def imageLoaded(self, details: ModDetails) -> None:
        current = self.selected()
        if current is not None and current.modId == details.modId:
            self.preview.showDetails(details, self.cache.image(details), self.remastered)
        elif current is not None and not self.cache.image(current) and current.thumbnailUrl:
            self.loadImage(current)

    def updateButtons(self) -> None:
        busy = self.worker is not None and self.worker.isRunning()
        chosen = self.selected() is not None
        self.searchButton.setEnabled(not busy)
        self.moreButton.setEnabled(not busy and len(self.results) < self.total)
        self.openButton.setEnabled(chosen)
        self.installButton.setEnabled(chosen)

    def openPage(self) -> None:
        details = self.selected()
        if details is not None:
            openUrl(details.url)

    def requestInstall(self) -> None:
        details = self.selected()
        if details is not None:
            self.installRequested.emit(details)

    def markInstalled(self) -> None:
        '''Refreshes the Installed column after installs'''
        installed = self.installedIds()
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item is not None:
                modId = item.data(0, Qt.ItemDataRole.UserRole)
                item.setText(7, translate("Browse", "Yes") if modId in installed else '')
