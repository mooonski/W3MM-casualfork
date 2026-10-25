'''Main Widget'''

import html
import os
import shlex
import shutil
import time
from collections.abc import Callable
from dataclasses import replace
from os import path
from sys import platform

from PySide6.QtCore import (
    QEvent,
    QFileInfo,
    QMetaObject,
    QObject,
    QRect,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QActionGroup, QBrush, QCursor, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from watchdog.events import DirModifiedEvent, FileModifiedEvent, PatternMatchingEventHandler
from watchdog.observers import Observer

from src.core.archives import IN_USE, moveToFolder, recycle, scanArchives, unfinishedDownload
from src.core.conflicts import ConflictScan, findConflicts, loadOrder
from src.core.duplicates import duplicateEntries, matchingEntries, versionNote
from src.core.endorsements import EndorsementCache
from src.core.fetcher import isArchive
from src.core.groups import (
    addGroup,
    arrange,
    cleanName,
    listKey,
    moveGroup,
    orderedGroups,
    removeGroup,
    renameGroup,
    uniqueName,
)
from src.core.health import ERROR, INFO, WARNING, Issue, checkHealth, sharedutilsIssues
from src.core.history import FAILED as DOWNLOAD_FAILED
from src.core.history import INSTALLED, KEPT, LINK, REINSTALL, UPDATE, UPDATED, WEBSITE, Download, DownloadHistory
from src.core.installer import Installer
from src.core.loadorder import moveFolder, prioritiesFor
from src.core.model import Model
from src.core.modio import ModioMod, installedModioMods
from src.core.nexus import (
    ENDORSED,
    FAILED,
    GAME_DOMAIN,
    UP_TO_DATE,
    UPDATE_AVAILABLE,
    ModFiles,
    NexusClient,
    NexusError,
    NexusFile,
    NexusInfo,
    UpdateCheck,
    changesBetween,
    checkForUpdate,
    parseArchiveName,
    parseModReference,
    uniquePath,
)
from src.core.nexusdetails import DetailsCache, ModDetails, NexusGraphQL, remasteredSuggestions
from src.core.nxm import NxmLink, NxmRegistration, WindowsRegistry, forward, handlerName, parseNxm
from src.core.requirements import DISABLED, DLC_MISSING, EXTERNAL, MISSING, missingRequirements
from src.core.rmmr import RmmrError, applyImport, isRmmrFolder, planImport
from src.core.saves import SaveBackup, backupSaves, gameSavesFolder, listSaveBackups, restoreSaves
from src.core.scriptmerger import (
    MERGED_FOLDER,
    ConflictReport,
    analyse,
    findScriptMerger,
    installedVersion,
    isNewer,
    latestScriptMerger,
    nexusId,
)
from src.core.selfupdate import (
    RELEASES_URL,
    UpdateCheckResult,
    downloadInstaller,
    dueForCheck,
    isInstalledBuild,
    launchInstaller,
    recordCheck,
)
from src.core.selfupdate import (
    checkForUpdate as checkManagerRelease,
)
from src.core.smupdate import ScriptMergerUpdateFailed
from src.core.smupdate import updateScriptMerger as updateScriptMergerFiles
from src.core.updater import ModUpdater
from src.domain.mod import Mod
from src.globals import data
from src.globals.constants import *
from src.gui.alerts import (
    MessageAlertIncompleteInstallation,
    MessageUnsupportedOSAction,
)
from src.gui.archives_dialog import RECYCLE, ArchiveCleanupDialog, sizeText
from src.gui.browse_widget import BrowseWidget
from src.gui.conflicts_dialog import FileConflictsDialog
from src.gui.details_dialog import DetailsDialog
from src.gui.downloads_widget import DownloadsWidget, Transfer
from src.gui.health_dialog import HealthDialog
from src.gui.history_dialog import DownloadHistoryDialog
from src.gui.icons import badgedIcon, icon
from src.gui.load_order_tree import LoadOrderTree
from src.gui.manager_update_dialog import INSTALL, OPEN_PAGE, ManagerUpdateDialog
from src.gui.mod_preview import ModPreview, PreviewSections
from src.gui.nexus_dialogs import (
    SWITCH,
    BackupsDialog,
    NexusFileDialog,
    NexusWorker,
    RmmrImportDialog,
    UpdateRow,
    UpdatesDialog,
    askApiKey,
    linkify,
    statusText,
)
from src.gui.rail import SectionRail
from src.gui.requirements_dialog import RequirementsDialog
from src.gui.reshade_dialog import ReShadeDialog
from src.gui.saves_dialog import SaveBackupsDialog
from src.gui.scriptmerger_dialog import OPEN_PAGE as SM_OPEN_PAGE
from src.gui.scriptmerger_dialog import UPDATE as SM_UPDATE
from src.gui.scriptmerger_dialog import MessageAlertScriptConflicts, ScriptConflictsDialog, askScriptMergerUpdate
from src.gui.settings_dialog import KEYS_TAB, USER_TAB, GameSettingsDialog
from src.gui.setup_dialog import SetupDialog
from src.gui.skin import (
    GROUP_COUNT_ROLE,
    PICTURE_ROLE,
    SUBTITLE_ROLE,
    UPDATE_KIND_ROLE,
    ModListDelegate,
    PlayButton,
)
from src.gui.themes import (
    ManagerStyle,
    accentName,
    get_dark_palette,
    get_light_palette,
    get_system_palette,
    setAccent,
    setTickStyle,
    statusColor,
    styleSheet,
    tickStyle,
)
from src.gui.tree_widget import (
    GROUP_ROLE,
    MODIO_ROLE,
    CustomTreeWidgetItem,
    GroupItem,
    ModTree,
    isGroupItem,
    isModioItem,
)
from src.util.syntax import *
from src.util.util import *


class ModsSettingsEventHandler(PatternMatchingEventHandler):
    def __init__(self, callback: Callable[[DirModifiedEvent | FileModifiedEvent], None]):
        super().__init__(patterns=["*mods.settings"], ignore_patterns=[], ignore_directories=True)
        self.callback = callback

    def on_modified(self, event: DirModifiedEvent | FileModifiedEvent) -> None:
        self.callback(event)


class ModsSettingsWatcher(QThread):
    '''Watches for changes in mods.settings file and signals for UI updates'''

    refresh = Signal(object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.modsEventHandler = ModsSettingsEventHandler(lambda e: self.refresh.emit(e))
        self.running = False
        self.observer = Observer()
        self.observer.schedule(self.modsEventHandler, path=data.getConfig().settings, recursive=False)
        self.observer.start()

    def stop(self):
        if hasattr(self, "observer") and self.observer is not None:
            self.observer.stop()
            self.observer.join()
            self.observer = None


def priorityNumber(value) -> int | None:
    '''A priority from mods.settings as a number, or None when it has none'''
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except ValueError:
        return None


# the mod list's column with the endorse thumb
ENDORSE_COLUMN = 15
UPDATE_COLUMN = 13
# what a mod brings (DLC, menus, key bindings...) as small tags; added last, shown after the priority
CONTENTS_COLUMN = 16
# the separate counts that the Contents column sums up, hidden unless the user shows them again
DETAIL_COLUMNS = (3, 4, 5, 6, 7, 8, 9)
# the mod list's filter buttons
LIST_FILTERS = ("all", "enabled", "disabled", "updates")
# the gap between the rail or the window's right edge and the top bar, the pages and the status row
EDGE = 14
# the top bar's height; the rail centres its logo on the same line
TOP_BAR_HEIGHT = 64


def contentsTags(mod: Mod) -> str:
    '''What a mod brings besides its mod folder, as space-separated tags for the Contents column'''
    tags = []
    if mod.dlcs:
        tags.append("DLC")
    if mod.menus or mod.xmlkeys or mod.hidden:
        tags.append("Menu")
    if mod.inputsettings:
        tags.append("Keys")
    if mod.usersettings:
        tags.append("Ini")
    if mod.bins:
        tags.append("Bin")
    return " ".join(tags)


def updateKind(check: UpdateCheck | None) -> str:
    '''How the Update column shows a check: "update", "error", "ok" or "" (not checked)'''
    if check is None:
        return ""
    return {UPDATE_AVAILABLE: "update", FAILED: "error", UP_TO_DATE: "ok"}.get(check.state, "")


# archives moved away by Clean Up Archives go to this folder inside the archive folder
OLD_ARCHIVES = "Old archives"


class CustomMainWidget(QWidget):
    '''Main Widget'''

    def __init__(self, parent: QMainWindow, model: Model):
        super().__init__(parent)

        self.mainWindow = parent
        self.model = model
        self.searchString = ""
        self.updateChecks: dict[str, UpdateCheck] = {}
        self.nexusWorker: NexusWorker | None = None
        self.nxmQueue: list[str] = []
        self.nexusDetails = DetailsCache(data.getConfig().configuration)
        self.downloadHistory = DownloadHistory(data.getConfig().configuration)
        self.endorsements = EndorsementCache(data.getConfig().configuration)
        self.endorsementsWorker: NexusWorker | None = None
        self.endorseWorker: NexusWorker | None = None
        self.detailsWorker: NexusWorker | None = None
        self.pictureWorker: NexusWorker | None = None
        self.requirementsWorker: NexusWorker | None = None
        self.loadOrderFocus = ""
        # the Nexus download running now, shown on the Downloads page with the links waiting their turn
        self.activeTransfer: Transfer | None = None
        self.notInstalledCount = 0
        # shown as counts on the Update and Health Check buttons
        self.updateCount = 0
        self.healthCount = 0

        self.modsSettingsWatcher = ModsSettingsWatcher()
        # connect to a method so the refresh always runs on the GUI thread
        self.modsSettingsWatcher.refresh.connect(self.onModsSettingsChanged)
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            raise RuntimeError("Application has not been initialized")
        app.aboutToQuit.connect(self.modsSettingsWatcher.stop)

        self.setupMainWindow()
        self.setupUI()
        self.setupMenuBar()

        self.createMenus()
        self.createActions()
        self.populateMenu()

        self.translateUi()
        self.configureUi()
        self.configureToolbar()
        self.configureRail()
        self.checkLanguage()
        self.checkTheme()
        self.refreshList()
        self.reportDuplicates()
        self.detectScriptMerger()
        self.reportHealth()
        QTimer.singleShot(1500, self.checkNxmHandler)
        if dueForCheck(data.getConfig()):
            QTimer.singleShot(4000, lambda: self.checkManagerUpdate(automatic=True))
        self.refreshNexusDetails(silent=True, onlyMissing=True)
        self.refreshEndorsements()

        QMetaObject.connectSlotsByName(self.mainWindow)

        self.mainWindow.resizeEvent = lambda e: self.onResize()  # type: ignore
        self.loadOrder.header().sectionResized.connect(lambda: self.onResize())
        self.treeWidget.header().sectionResized.connect(lambda: self.onResize())

        self.actionAlert_to_run_Script_Merger.setChecked(data.getConfig().allowpopups == "1")
        self.actionUseNativeFileDialogs.setChecked(data.getConfig().get("SETTINGS", "usenativedialog", "1") == "1")
        self.actionKeepArchives.setChecked(data.getConfig().keeparchives == "1")
        self.actionBackupSaves.setChecked(data.getConfig().backupsaves == "1")
        self.actionHandleNxm.setEnabled(platform == "win32")
        self.actionDefaultNxm.setEnabled(platform == "win32")
        if platform == "win32":
            try:
                registered = self.nxmRegistration().isRegistered()
                self.actionHandleNxm.setChecked(registered)
                if registered and not data.getConfig().nxmwanted:
                    data.getConfig().nxmwanted = "1"  # turned on before the choice was remembered
            except OSError:
                pass

    @debounceGui(200)
    def onResize(self):
        '''Save window settings when resized'''
        data.getConfig().saveWindowSettings(self, self.mainWindow)

    def resizeEvent(self, event: QResizeEvent):
        '''Handle resize events'''
        self.onResize()

    def onModsSettingsChanged(self, _event=None):
        '''Handle external mods.settings changes'''
        if data.getConfig().write_priority_elapsed() < 350:
            return
        self.reloadPrioritiesSoon()

    @debounceGui(300)
    def reloadPrioritiesSoon(self):
        '''Editors can save in more than one step, so this waits for the file to settle'''
        self.reloadPriorities()

    def reloadPriorities(self):
        '''Reads mods.settings again and shows its priorities in the mod list as well as the load order'''
        data.getConfig().readPriority()
        self.showPriorities()

    def showPriorities(self):
        '''Shows the priorities set in mods.settings (as last read or set here) in the mod list and load order'''
        for mod in self.model.all():
            mod.syncPriority()
        self.refreshList()

    def setupMainWindow(self):
        '''Configure main window properties'''
        self.mainWindow.setObjectName("MainWindow")

        width = data.getConfig().get("WINDOW", "width")
        height = data.getConfig().get("WINDOW", "height")
        wini = int(width) if width else 1024
        hini = int(height) if height else 720

        self.mainWindow.resize(wini, hini)
        if data.getConfig().get("WINDOW", "maximized") == "1":
            self.mainWindow.setWindowState(self.mainWindow.windowState() | Qt.WindowState.WindowMaximized)
        self.mainWindow.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.mainWindow.setWindowOpacity(1.0)
        self.mainWindow.setStatusTip("")
        self.mainWindow.setAutoFillBackground(False)
        self.mainWindow.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.mainWindow.setAcceptDrops(True)

    def setupMenuBar(self):
        '''Setup the menu bar'''
        self.menubar = QMenuBar(self.mainWindow)
        self.menubar.setGeometry(QRect(0, 0, 583, 21))
        self.menubar.setObjectName("menubar")
        self.mainWindow.setMenuBar(self.menubar)
        # the menus open from the logo at the top of the rail instead (configureRail)
        self.menubar.setVisible(False)
        # says which build this is, at the bottom of the rail
        self.forkLabel = QLabel(forkLabel().replace(", ", "\n"), self.rail)
        self.forkLabel.setObjectName("forkLabel")
        self.forkLabel.setToolTip(getVersionString())
        self.forkLabel.setStyleSheet(f"color: {statusColor('muted').name()};")

        # the top bar: the page's title, then Check for Updates, Update, Add Mod, Script Merger and Play. It heads
        # the column right of the rail, so the rail runs down the whole left side, and its edges line up with the
        # pages' below
        self.topBar = QFrame(self.centralwidget)
        self.topBar.setObjectName("topBar")
        bar = QHBoxLayout(self.topBar)
        bar.setContentsMargins(EDGE, 0, EDGE, 0)
        # a set height, so the logo at the top of the rail (centred on TOP_BAR_HEIGHT / 2) lines up with it
        self.topBar.setFixedHeight(TOP_BAR_HEIGHT)
        bar.setSpacing(8)
        heading = QWidget(self.topBar)
        headingLayout = QVBoxLayout(heading)
        headingLayout.setContentsMargins(0, 0, 0, 0)
        headingLayout.setSpacing(0)
        self.pageTitle = QLabel(heading)
        self.pageTitle.setObjectName("pageTitle")
        self.pageSubtitle = QLabel(heading)
        self.pageSubtitle.setObjectName("pageSubtitle")
        headingLayout.addWidget(self.pageTitle)
        headingLayout.addWidget(self.pageSubtitle)
        bar.addWidget(heading, 0, Qt.AlignmentFlag.AlignVCenter)
        bar.addStretch(1)
        # programs the user put on the bar (right-click it to add one); hidden while there are none
        self.toolBar = QToolBar(self.topBar)
        self.toolBar.setObjectName("toolsBar")
        self.toolBar.setMovable(False)
        self.toolBar.setVisible(False)
        self.customToolbarActions: list[QAction] = []
        bar.addWidget(self.toolBar)
        self.checkButton = QPushButton(self.topBar)
        self.checkButton.setObjectName("checkButton")
        self.checkButton.clicked.connect(lambda: self.actionCheckUpdates.trigger())
        self.updateButton = QPushButton(self.topBar)
        self.updateButton.setObjectName("updateAllButton")
        self.updateButton.clicked.connect(lambda: self.actionUpdateMods.trigger())
        self.updateButton.setVisible(False)
        self.addButton = QPushButton(self.topBar)
        self.addButton.setObjectName("addButton")
        self.addButton.clicked.connect(lambda: self.actionInstall_Mods.trigger())
        self.scriptMergerButton = QPushButton(self.topBar)
        self.scriptMergerButton.setObjectName("scriptMergerButton")
        divider = QFrame(self.topBar)
        divider.setObjectName("topDivider")
        divider.setFixedSize(1, 24)
        self.runGameButton = PlayButton(self.topBar)
        self.runGameButton.setObjectName("runGameButton")
        for button in (self.checkButton, self.updateButton, self.addButton, self.scriptMergerButton):
            button.setProperty("top", True)
            # every button on the bar is as tall as Play
            button.setFixedHeight(PlayButton.HEIGHT)
        for part in (self.checkButton, self.updateButton, self.addButton, divider, self.scriptMergerButton):
            bar.addWidget(part)
        bar.addWidget(self.runGameButton)
        self.verticalLayout_2.insertWidget(0, self.topBar)

    def setupUI(self):
        '''Setup all UI components'''
        # Central widget
        self.centralwidget = QWidget(self.mainWindow)
        self.centralwidget.setObjectName("centralwidget")
        # the rail down the whole left side, and a column with the top bar, the pages and the status row
        self.bodyRow = QHBoxLayout(self.centralwidget)
        self.bodyRow.setContentsMargins(0, 0, 0, 0)
        self.bodyRow.setSpacing(0)
        self.verticalLayout_2 = QVBoxLayout()
        self.verticalLayout_2.setObjectName("verticalLayout_2")
        self.verticalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.verticalLayout_2.setSpacing(0)
        # the tabs and the log, inset by EDGE, the same as the top bar and the status row
        self.contentLayout = QVBoxLayout()
        self.contentLayout.setContentsMargins(EDGE, 10, EDGE, 10)
        self.contentLayout.setSpacing(8)
        # the section buttons, filled in by configureRail once the actions exist
        self.rail = SectionRail(self.centralwidget)
        # in a short window the rail scrolls rather than squeezing its buttons
        self.railScroll = QScrollArea(self.centralwidget)
        self.railScroll.setObjectName("railScroll")
        self.railScroll.setWidget(self.rail)
        self.railScroll.setWidgetResizable(True)
        self.railScroll.setFrameShape(QFrame.Shape.NoFrame)
        self.railScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.railScroll.setFixedWidth(self.rail.width())
        self.bodyRow.addWidget(self.railScroll)
        self.bodyRow.addLayout(self.verticalLayout_2, 1)
        self.verticalLayout_2.addLayout(self.contentLayout, 1)

        # Tabs: the installed mods, and browsing Nexus
        self.tabs = QTabWidget(self.centralwidget)
        self.tabs.setObjectName("tabs")
        self.installedTab = QWidget(self.tabs)
        self.installedLayout = QVBoxLayout(self.installedTab)
        self.installedLayout.setContentsMargins(8, 8, 8, 8)
        self.installedLayout.setSpacing(8)

        # Search, and buttons that show all, enabled, disabled or updatable mods
        self.filterRow = QHBoxLayout()
        self.filterRow.setSpacing(6)
        self.searchWidget = QLineEdit(self.installedTab)
        self.searchWidget.setObjectName("searchWidget")
        self.searchWidget.setPlaceholderText(translate("MainWindow", "Search"))
        self.searchWidget.setClearButtonEnabled(True)
        self.searchAction = self.searchWidget.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.filterRow.addWidget(self.searchWidget, 1)
        self.listFilter = "all"
        self.filterButtons: dict[str, QPushButton] = {}
        self.filterGroup = QButtonGroup(self.installedTab)
        self.filterGroup.setExclusive(True)
        for name in LIST_FILTERS:
            button = QPushButton(self.installedTab)
            button.setObjectName(f"filter_{name}")
            button.setProperty("chip", True)
            button.setCheckable(True)
            button.setChecked(name == "all")
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.clicked.connect(lambda _=False, n=name: self.setListFilter(n))
            self.filterGroup.addButton(button)
            self.filterButtons[name] = button
            self.filterRow.addWidget(button)
        self.installedLayout.addLayout(self.filterRow)

        # Setup tree widget
        self.setupTreeWidget()
        self.tabs.addTab(self.installedTab, translate("MainWindow", "Installed"))

        # archives downloaded but not installed
        self.detailsAsked: set[int] = set()
        self.downloads = DownloadsWidget(
            self.tabs, self.nexusDetails, self.scanDownloads, data.getConfig().gameversion == "re"
        )
        self.downloads.installRequested.connect(self.installModFiles)
        self.downloads.countChanged.connect(self.downloadsCounted)
        self.downloads.detailsWanted.connect(self.downloadDetailsWanted)
        self.tabs.addTab(self.downloads, translate("MainWindow", "Downloads"))

        self.browse = BrowseWidget(
            self.tabs, self.nexusDetails, lambda: set(self.linkedModIds()), data.getConfig().gameversion == "re"
        )
        self.browse.installRequested.connect(self.installFromNexus)
        self.tabs.addTab(self.browse, translate("MainWindow", "Browse Nexus"))
        self.contentLayout.addWidget(self.tabs, 1)

        # The log (shown on demand) and the status row under it
        self.setupOutputArea()

        self.mainWindow.setCentralWidget(self.centralwidget)

    def setupTreeWidget(self):
        '''Setup tree widget and load order panel'''
        # Mod list tree widget
        self.treeWidget = ModTree(self.centralwidget)
        self.treeWidget.setMinimumSize(QSize(600, 350))
        self.treeWidget.setUniformRowHeights(True)
        self.treeWidget.setAnimated(True)
        self.treeWidget.setHeaderHidden(False)
        self.treeWidget.setColumnCount(CONTENTS_COLUMN + 1)
        self.treeWidget.setObjectName("treeWidget")
        self.listDelegate = ModListDelegate(self.treeWidget, CONTENTS_COLUMN, UPDATE_COLUMN)
        self.treeWidget.setItemDelegate(self.listDelegate)
        self.treeWidget.setAlternatingRowColors(True)
        self.treeWidget.header().setCascadingSectionResizes(True)
        self.treeWidget.header().setHighlightSections(False)
        self.treeWidget.header().setSortIndicatorShown(True)
        self.treeWidget.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.treeWidget.header().customContextMenuRequested.connect(self.columnsMenu)
        self.treeWidget.setSortingEnabled(True)

        # Splitter for the tree and load order
        self.horizontalSplitter_tree = QSplitter()
        self.horizontalSplitter_tree.setObjectName("horizontalSplitter_tree")
        self.horizontalSplitter_tree.setHandleWidth(10)
        self.horizontalSplitter_tree.addWidget(self.treeWidget)

        # Load order tree widget
        self.loadOrder = LoadOrderTree(self.centralwidget)
        self.loadOrder.setUniformRowHeights(True)
        self.loadOrder.setAnimated(True)
        self.loadOrder.setHeaderHidden(False)
        self.loadOrder.setColumnCount(2)
        self.loadOrder.setObjectName("loadOrder")
        self.loadOrder.setMinimumWidth(200)
        self.loadOrder.setSortingEnabled(False)

        # The side panel shows the selected mod's details, or the load order (Load Order on the rail, or its switch)
        self.preview = ModPreview(self.centralwidget)
        self.preview.changelogWanted.connect(self.loadChangelog)
        self.preview.nexusRequested.connect(self.openNexusPage)
        self.preview.endorseRequested.connect(self.endorseSelected)
        self.preview.updateRequested.connect(self.updateSelectedMods)
        self.sidePanel = QFrame(self.centralwidget)
        self.sidePanel.setObjectName("sidePanel")
        self.sidePanel.setMinimumWidth(320)
        side = QVBoxLayout(self.sidePanel)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        segment = QFrame(self.sidePanel)
        segment.setObjectName("segment")
        segmentRow = QHBoxLayout(segment)
        segmentRow.setContentsMargins(3, 3, 3, 3)
        segmentRow.setSpacing(2)
        self.sideButtons: dict[str, QPushButton] = {}
        sideGroup = QButtonGroup(segment)
        for key in ("details", "loadorder"):
            button = QPushButton(segment)
            button.setObjectName(f"side_{key}")
            button.setProperty("segment", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self.showSideView(k))
            sideGroup.addButton(button)
            segmentRow.addWidget(button, 1)
            self.sideButtons[key] = button
        side.addWidget(segment)
        self.sideStack = QStackedWidget(self.sidePanel)
        self.sideStack.addWidget(self.preview)
        self.sideStack.addWidget(self.loadOrder)
        side.addWidget(self.sideStack, 1)
        self.horizontalSplitter_tree.addWidget(self.sidePanel)
        self.horizontalSplitter_tree.setCollapsible(0, False)
        self.horizontalSplitter_tree.setCollapsible(1, True)
        self.horizontalSplitter_tree.setStretchFactor(0, 3)
        self.horizontalSplitter_tree.setStretchFactor(1, 1)
        self.installedLayout.addWidget(self.horizontalSplitter_tree)

    def setupOutputArea(self):
        '''The log, which opens from the status row, and the status row: health, the latest message, progress'''
        self.textEdit = QTextEdit(self.centralwidget)
        self.textEdit.setReadOnly(True)
        self.textEdit.setObjectName("textEdit")
        self.textEdit.setMinimumHeight(80)
        self.textEdit.setMaximumHeight(180)
        self.textEdit.setVisible(data.getConfig().get("WINDOW", "log") == "1")
        self.contentLayout.addWidget(self.textEdit)

        self.statusRow = QFrame(self.centralwidget)
        self.statusRow.setObjectName("statusRow")
        row = QHBoxLayout(self.statusRow)
        row.setContentsMargins(EDGE, 5, EDGE, 5)
        row.setSpacing(12)
        self.healthChip = QPushButton(self.statusRow)
        self.healthChip.setObjectName("healthChip")
        self.healthChip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.healthChip.setVisible(False)
        row.addWidget(self.healthChip)
        self.statusLabel = QLabel(self.statusRow)
        self.statusLabel.setObjectName("statusText")
        self.statusLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.statusLabel, 1)
        self.progressBar = QProgressBar(self.statusRow)
        self.progressBar.setProperty("value", 0)
        self.progressBar.setObjectName("progressBar")
        self.progressBar.setTextVisible(False)
        self.progressBar.setFixedSize(160, 6)
        self.progressBar.setVisible(False)
        row.addWidget(self.progressBar)
        self.summaryLabel = QLabel(self.statusRow)
        self.summaryLabel.setObjectName("summaryText")
        row.addWidget(self.summaryLabel)
        self.logButton = QPushButton(self.statusRow)
        self.logButton.setObjectName("logToggle")
        self.logButton.setCheckable(True)
        self.logButton.setChecked(self.textEdit.isVisibleTo(self.centralwidget))
        self.logButton.toggled.connect(self.showLog)
        row.addWidget(self.logButton)
        self.verticalLayout_2.addWidget(self.statusRow)

    def showLog(self, shown: bool) -> None:
        self.textEdit.setVisible(shown)
        if shown:
            self.textEdit.verticalScrollBar().setValue(self.textEdit.verticalScrollBar().maximum())
        data.getConfig().set("WINDOW", "log", "1" if shown else "0")

    def configureUi(self):
        # the endorse column came later; give it a width instead of resetting the saved layout
        if not data.getConfig().getWindowSection(ENDORSE_COLUMN):
            data.getConfig().set("WINDOW", f"section{ENDORSE_COLUMN}", "34")
        if not data.getConfig().getWindowSection(CONTENTS_COLUMN):
            data.getConfig().set("WINDOW", f"section{CONTENTS_COLUMN}", "130")
        for i in range(self.treeWidget.header().count()):
            if not data.getConfig().getWindowSection(i):
                data.getConfig().setDefaultWindow()
                break

        self.treeWidget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.treeWidget.header().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        if data.getConfig().listsort == "custom":
            self.treeWidget.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        else:
            self.treeWidget.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        self.treeWidget.header().sortIndicatorChanged.connect(self.listSortChanged)

        self.loadOrder.header().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loadOrder.header().setStretchLastSection(False)
        self.loadOrder.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.loadOrder.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        # Contents comes after Priority, where the counts it sums up used to be
        header = self.treeWidget.header()
        header.moveSection(header.visualIndex(CONTENTS_COLUMN), header.visualIndex(2) + 1)
        for column in self.hiddenColumns():
            header.setSectionHidden(column, True)

        self.resizeColumns()
        self.applyRowSize()
        self.fitTickColumn()
        # the name takes the width the other columns leave, so the rows reach the list's right edge
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(24)

        self.actionInstall_Mods.triggered.connect(self.installMods)
        self.actionUninstall_Mods.triggered.connect(self.uninstallMods)
        self.actionReinstall_Mods.triggered.connect(self.reinstallMods)
        self.actionReinstallFiles.triggered.connect(self.reinstallFromArchives)
        self.actionAbout.triggered.connect(showAboutWindow)
        self.actionManagerUpdate.triggered.connect(lambda: self.checkManagerUpdate(automatic=False))
        self.actionEnable_Disable_Mods.triggered.connect(self.enableDisableMods)
        self.actionRefresh_Mod_List.triggered.connect(lambda e: self.reloadPriorities())
        self.actionRefresh_Load_Order.triggered.connect(lambda e: self.refreshLoadOrder())
        self.actionScriptConflicts.triggered.connect(self.showScriptConflicts)
        self.actionFileConflicts.triggered.connect(self.showFileConflicts)
        self.actionHealthCheck.triggered.connect(self.showHealthCheck)
        self.actionGameSettings.triggered.connect(lambda: self.showGameSettings(USER_TAB))
        self.actionReShade.triggered.connect(self.showReShade)
        self.actionSetup.triggered.connect(self.showSetup)
        self.actionSelect_All_Mods.triggered.connect(self.selectAllMods)
        self.actionEnableAll.triggered.connect(lambda: self.setVisibleModsEnabled(True))
        self.actionDisableAll.triggered.connect(lambda: self.setVisibleModsEnabled(False))
        self.actionRun_The_Game.triggered.connect(self.runTheGame)
        self.actionRun_Script_Merger.triggered.connect(self.runScriptMerger)
        self.actionMain_Web_Page.triggered.connect(lambda: openUrl(URL_WEB))
        self.actionGitHub.triggered.connect(lambda: openUrl(URL_GIT))
        self.actionAlert_to_run_Script_Merger.triggered.connect(self.alertPopupChanged)
        self.actionUseNativeFileDialogs.triggered.connect(self.nativeFileDialogsChanged)
        self.actionChange_Game_Path.triggered.connect(self.changeGamePath)
        self.actionChange_Script_Merger_Path.triggered.connect(self.changeScriptMergerPath)
        self.actionClearOutput.triggered.connect(self.clear)
        self.actionRename.triggered.connect(self.rename)
        self.actionDetails.triggered.connect(self.details)
        self.actionOpenFolder.triggered.connect(self.openFolder)
        self.actionIncreasePriority.triggered.connect(self.increasePriority)
        self.actionDecreasePriority.triggered.connect(self.decreasePriority)
        self.actionIncreaseLoadOrderPriority.triggered.connect(self.increaseLoadOrderPriority)
        self.actionDecreaseLoadOrderPriority.triggered.connect(self.decreaseLoadOrderPriority)
        self.actionSetPriority.triggered.connect(self.setPriority)
        self.actionUnsetPriority.triggered.connect(self.unsetPriority)
        self.actionRestoreColumns.triggered.connect(self.restoreColumns)
        self.actionCheckUpdates.triggered.connect(lambda: self.checkForUpdates(showDialog=False))
        self.actionUpdateMods.triggered.connect(self.updateSelectedMods)
        self.actionUpdateScriptMerger.triggered.connect(self.checkScriptMergerUpdate)
        self.actionSetNexusMod.triggered.connect(self.setNexusMod)
        self.actionChooseFile.triggered.connect(self.chooseInstalledFile)
        self.actionOpenNexusPage.triggered.connect(self.openNexusPage)
        self.actionDownloadHistory.triggered.connect(self.showDownloadHistory)
        self.actionCleanUpArchives.triggered.connect(self.cleanUpArchives)
        self.actionRestoreBackup.triggered.connect(self.restoreBackup)
        self.actionImportRmmr.triggered.connect(self.importFromRmmr)
        self.actionRefreshDetails.triggered.connect(lambda: self.refreshNexusDetails())
        self.actionCheckRemastered.triggered.connect(self.checkRemastered)
        self.actionCheckRequirements.triggered.connect(lambda: self.checkRequirements())
        self.actionKeepArchives.triggered.connect(self.keepArchivesChanged)
        self.actionBackupSaves.triggered.connect(
            lambda: setattr(data.getConfig(), "backupsaves", "1" if self.actionBackupSaves.isChecked() else "0")
        )
        self.actionSaveBackups.triggered.connect(self.showSaveBackups)
        self.actionHandleNxm.triggered.connect(self.handleNxmChanged)
        self.actionDefaultNxm.triggered.connect(self.makeDefaultNxmHandler)
        self.menuNexus.aboutToShow.connect(self.showNxmState)
        self.actionSetApiKey.triggered.connect(lambda: askApiKey(self))
        self.actionChangeArchiveFolder.triggered.connect(self.changeArchiveFolder)
        self.actionChangeBackupFolder.triggered.connect(self.changeBackupFolder)

        self.scriptMergerButton.clicked.connect(self.runScriptMerger)
        self.runGameButton.clicked.connect(self.runTheGame)
        self.healthChip.clicked.connect(self.showHealthCheck)

        self.treeWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.treeWidget.customContextMenuRequested.connect(self.openMenu)

        self.topBar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.topBar.customContextMenuRequested.connect(self.toolbarMenu)

        self.textEdit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.textEdit.customContextMenuRequested.connect(self.openEditMenu)

        self.treeWidget.itemChanged.connect(self.modToggled)
        self.treeWidget.itemClicked.connect(self.modClicked)
        # queued, so the list is filled again after Qt has finished with the drag
        self.treeWidget.modsMoved.connect(self.modsMoved, Qt.ConnectionType.QueuedConnection)
        self.treeWidget.separatorsMoved.connect(self.separatorsMoved, Qt.ConnectionType.QueuedConnection)
        self.treeWidget.itemExpanded.connect(lambda item: self.groupFolded(item, False))
        self.treeWidget.itemCollapsed.connect(lambda item: self.groupFolded(item, True))
        self.treeWidget.installEventFilter(self)
        self.treeWidget.itemDoubleClicked.connect(self.modDoubleClicked)
        self.treeWidget.itemSelectionChanged.connect(self.updatePreview)
        self.treeWidget.header().setStretchLastSection(False)

        self.loadOrder.itemDoubleClicked.connect(self.loadOrderDoubleClicked)
        self.loadOrder.moved.connect(self.loadOrderMoved)

        self.searchWidget.textChanged.connect(self.setSearchString)

    def hiddenColumns(self) -> list[int]:
        '''The mod list's hidden columns; the separate counts behind Contents unless the user chose otherwise'''
        saved = data.getConfig().get("WINDOW", "hiddencolumns")
        if saved is None:
            return list(DETAIL_COLUMNS)
        return [int(part) for part in saved.split(",") if part.strip().isdigit()]

    def columnsMenu(self, position) -> None:
        '''Right-click on the mod list's header: show or hide columns'''
        header = self.treeWidget.header()
        menu = QMenu(self.treeWidget)
        for visual in range(header.count()):
            column = header.logicalIndex(visual)
            if column in (0, 1):
                continue  # the tick and the name always show
            label = self.treeWidget.headerItem().text(column) or translate("MainWindow", "Endorsed")
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not header.isSectionHidden(column))
            action.toggled.connect(lambda shown, c=column: self.showColumn(c, shown))
        menu.exec(header.mapToGlobal(position))

    def showColumn(self, column: int, shown: bool) -> None:
        header = self.treeWidget.header()
        header.setSectionHidden(column, not shown)
        if shown and header.sectionSize(column) < 20:
            header.resizeSection(column, data.getConfig().getWindowSection(column) or 60)
        hidden = [str(c) for c in range(header.count()) if header.isSectionHidden(c)]
        data.getConfig().set("WINDOW", "hiddencolumns", ",".join(hidden))

    def setListFilter(self, name: str) -> None:
        self.listFilter = name
        self.filterButtons[name].setChecked(True)
        self.refreshList(False)

    def openByConfigKey(self, option):
        '''Open or run any kind of folder/file or executable by configuration key'''
        openFile(getattr(data.getConfig(), option))

    def configureToolbar(self):
        """Draws the top bar's icons again (theme changes) and puts the user's programs on it"""
        self.applyIcons()
        self.toolBar.setIconSize(QSize(20, 20))
        self.actionRefresh_Mod_List.setToolTip(
            translate("MainWindow", "Read mods.settings and the mods again, e.g. after editing them outside (F5)")
        )
        for action in self.customToolbarActions:
            self.toolBar.removeAction(action)
        self.customToolbarActions = []
        self.toolBar.setVisible(False)
        for custom in data.getConfig().getOptions("TOOLBAR"):
            self.addToToolbar(custom)
        self.actionAddToToolbar = QAction(self.mainWindow)
        self.actionAddToToolbar.triggered.connect(self.addToToolbar)
        self.actionAddToToolbar.setText(translate("MainWindow", "Add New.."))

    def foldersMenu(self) -> QMenu:
        menu = QMenu(self.mainWindow)
        for key, label, name in (
            ("mods", translate("MainWindow", "Mods"), "mods"),
            ("dlc", translate("MainWindow", "DLC"), "dlc"),
            ("menu", translate("MainWindow", "Menus"), "menus"),
            ("settings", translate("MainWindow", "Settings"), "folder"),
        ):
            menu.addAction(icon(name), label, lambda k=key: self.openByConfigKey(k))
        return menu

    def configFilesMenu(self) -> QMenu:
        menu = QMenu(self.mainWindow)
        menu.setToolTipsVisible(True)
        menu.addAction(
            icon("xml"),
            translate("MainWindow", "Input Xml"),
            lambda: openTextFile(data.getConfig().menu + "/input.xml"),
        ).setToolTip(translate("MainWindow", "Open input.xml file"))
        menu.addAction(
            icon("keyboard"), translate("MainWindow", "Input Settings"), lambda: self.showGameSettings(KEYS_TAB)
        ).setToolTip(translate("MainWindow", "Your key bindings (input.settings), with the ones from your mods"))
        menu.addAction(
            icon("sliders"), translate("MainWindow", "User Settings"), lambda: self.showGameSettings(USER_TAB)
        ).setToolTip(translate("MainWindow", "The game's settings (user.settings), including your mods' options"))
        menu.addAction(icon("order"), translate("MainWindow", "Mods Settings"), self.showLoadOrderPanel).setToolTip(
            translate("MainWindow", "The load order (mods.settings), shown in the side panel; drag mods to move them")
        )
        return menu

    def configureRail(self) -> None:
        '''The logo with the program's menus, then the three pages instead of tabs, then places that open something'''
        rail = self.rail
        self.mainMenu = QMenu(self.mainWindow)
        for menu in (self.menuFile, self.menuEdit, self.menuNexus, self.menuSettings, self.menuHelp):
            self.mainMenu.addMenu(menu)
        rail.addLogo(
            getIcon("w3mm-fork.ico"),
            translate("MainWindow", "Menu: Mods, Edit, Nexus, Settings, Help"),
            self.mainMenu,
            TOP_BAR_HEIGHT // 2,
        )
        self.keepShortcuts()
        rail.addPage("mods", translate("MainWindow", "Mods"), "grid", translate("MainWindow", "The installed mods"))
        rail.addPage(
            "downloads",
            translate("MainWindow", "Downloads"),
            "update",
            translate("MainWindow", "Downloads in progress, and downloaded archives that aren't installed"),
        )
        rail.addPage("nexus", translate("MainWindow", "Nexus"), "globe", translate("MainWindow", "Browse Nexus"))
        rail.addPlace(
            "loadorder",
            translate("MainWindow", "Load Order"),
            "order",
            translate("MainWindow", "Show the load order (mods.settings) in the side panel, or the details again"),
            self.showLoadOrderFromRail,
        ).setCheckable(True)
        rail.addPlace(
            "health",
            translate("MainWindow", "Health"),
            "health",
            translate("MainWindow", "Look for problems with the installed mods (F9)"),
            self.showHealthCheck,
        )
        rail.addPlace(
            "saves",
            translate("MainWindow", "Saves"),
            "save",
            translate("MainWindow", "Copies of your newest saves, made before updates"),
            self.showSaveBackups,
        )
        rail.addPlace(
            "history",
            translate("MainWindow", "History"),
            "history",
            translate("MainWindow", "Files downloaded from Nexus, what became of them, and installing them again"),
            self.showDownloadHistory,
        )
        rail.addMenu(
            "folders",
            translate("MainWindow", "Folders"),
            "folder",
            translate("MainWindow", "Open a game folder"),
            self.foldersMenu(),
        )
        rail.addMenu(
            "config",
            translate("MainWindow", "Config"),
            "xml",
            translate("MainWindow", "The game's input and settings files"),
            self.configFilesMenu(),
        )
        tools = QMenu(self.mainWindow)
        for action in (
            self.actionRun_Script_Merger,
            self.actionUpdateScriptMerger,
            self.actionScriptConflicts,
            self.actionFileConflicts,
            None,
            self.actionReShade,
            self.actionGameSettings,
            None,
            self.actionRefresh_Mod_List,
        ):
            if action is None:
                tools.addSeparator()
            else:
                tools.addAction(action)
        rail.addMenu(
            "tools",
            translate("MainWindow", "Tools"),
            "tools",
            translate("MainWindow", "Script Merger, conflicts, ReShade and game settings"),
            tools,
        )
        rail.addStretch()
        rail.addMenu("settings", translate("MainWindow", "Settings"), "sliders", "", self.menuSettings)
        rail.addNote(self.forkLabel)
        rail.pageChosen.connect(self.showPage)
        self.tabs.tabBar().hide()
        self.tabs.currentChanged.connect(lambda _: rail.setCurrent(self.pageKey()))
        self.tabs.currentChanged.connect(lambda _: self.showPageTitle())
        rail.setCurrent(self.pageKey())
        rail.setBadge("health", self.healthCount, statusColor("warning"))
        more = QMenu(self.mainWindow)
        for action in (
            self.actionOpenFolder,
            self.actionChooseFile,
            self.actionSetNexusMod,
            self.actionRestoreBackup,
            None,
            self.actionRename,
            self.actionReinstallFiles,
            self.actionEnable_Disable_Mods,
            self.actionUninstall_Mods,
        ):
            if action is None:
                more.addSeparator()
            else:
                more.addAction(action)
        self.preview.setMoreMenu(more)
        self.sideButtons["details"].setText(translate("MainWindow", "Details"))
        self.sideButtons["loadorder"].setText(translate("MainWindow", "Load Order"))
        self.sideButtons["loadorder"].setToolTip(
            translate("MainWindow", "The order the game loads mods in (mods.settings); drag a mod to move it")
        )
        self.showSideView("loadorder" if data.getConfig().get("WINDOW", "sideview") == "loadorder" else "details")

    def keepShortcuts(self) -> None:
        '''The menu bar is hidden, so its actions are added to the window too, where their shortcuts (F5, Del,
        Ctrl+R...) still work'''

        def actionsIn(menu: QMenu) -> list[QAction]:
            found = []
            for action in menu.actions():
                if action.menu() is not None:
                    found += actionsIn(action.menu())
                elif not action.shortcut().isEmpty():
                    found.append(action)
            return found

        actions = []
        for menu in (self.menuFile, self.menuEdit, self.menuNexus, self.menuSettings, self.menuHelp):
            actions += actionsIn(menu)
        # Play and Script Merger are buttons now, so their actions live on in no menu
        actions += [self.actionRun_The_Game, self.actionRun_Script_Merger]
        self.mainWindow.addActions(list(dict.fromkeys(actions)))

    def pageWidgets(self) -> dict:
        return {"mods": self.installedTab, "downloads": self.downloads, "nexus": self.browse}

    def pageKey(self) -> str:
        current = self.tabs.currentWidget()
        return next((key for key, page in self.pageWidgets().items() if page is current), "mods")

    def showPage(self, key: str) -> None:
        self.tabs.setCurrentWidget(self.pageWidgets()[key])

    def showLoadOrderFromRail(self) -> None:
        '''Load Order on the rail switches the side panel to the load order, and back to the details'''
        self.showPage("mods")
        self.showSideView("details" if self.sideStack.currentWidget() is self.loadOrder else "loadorder")

    def showSideView(self, key: str) -> None:
        '''The side panel shows the selected mod's details ("details") or the load order ("loadorder")'''
        loadOrder = key == "loadorder"
        self.sideStack.setCurrentWidget(self.loadOrder if loadOrder else self.preview)
        self.sideButtons[key].setChecked(True)
        if "loadorder" in self.rail.buttons:
            self.rail.buttons["loadorder"].setChecked(loadOrder)
        data.getConfig().set("WINDOW", "sideview", key, write=False)
        if loadOrder:
            self.showModInLoadOrder()

    def showPageTitle(self) -> None:
        """The top bar's title and the line under it, for the page shown"""
        key = self.pageKey()
        if key == "downloads":
            title = translate("MainWindow", "Downloads")
            waiting = len(self.nxmQueue) + (1 if self.activeTransfer is not None else 0)
            subtitle = plural(self.notInstalledCount, translate("MainWindow", "archive"))
            subtitle += " " + translate("MainWindow", "not installed")
            if waiting:
                subtitle += f" · {waiting} " + translate("MainWindow", "downloading")
        elif key == "nexus":
            title = translate("MainWindow", "Nexus")
            subtitle = translate("MainWindow", "Find mods and install them")
        else:
            title = translate("MainWindow", "Mods")
            mods = self.model.all()
            subtitle = plural(len(mods), translate("MainWindow", "mod")) + " " + translate("MainWindow", "installed")
            subtitle += f" · {sum(1 for mod in mods if mod.enabled)} " + translate("MainWindow", "enabled")
        self.pageTitle.setText(title)
        self.pageSubtitle.setText(subtitle)

    def applyIcons(self) -> None:
        '''Line icons in the current theme's colours, with counts on Update and Health Check'''
        for action, name in (
            (self.actionInstall_Mods, "add"),
            (self.actionUninstall_Mods, "remove"),
            (self.actionEnable_Disable_Mods, "toggle"),
            (self.actionCheckUpdates, "check"),
            (self.actionRun_Script_Merger, "merge"),
            (self.actionDownloadHistory, "history"),
            (self.actionRefresh_Mod_List, "refresh"),
        ):
            action.setIcon(icon(name))
            action.setIconVisibleInMenu(False)
        self.actionUpdateMods.setIcon(badgedIcon("update", self.updateCount, statusColor("update")))
        self.actionUpdateMods.setIconVisibleInMenu(False)
        self.actionHealthCheck.setIcon(badgedIcon("health", self.healthCount, statusColor("warning")))
        self.actionHealthCheck.setIconVisibleInMenu(False)
        for button, name in (
            (self.checkButton, "check"),
            (self.updateButton, "update"),
            (self.addButton, "add"),
            (self.scriptMergerButton, "merge"),
        ):
            button.setIcon(icon(name))
            button.setIconSize(QSize(16, 16))
        self.updateButton.setText(translate("MainWindow", "Update") + f" {self.updateCount}")
        self.updateButton.setVisible(self.updateCount > 0)

    def openMenu(self, position):
        '''Right click menu on mod list (Left panel)'''
        menu = QMenu()
        clicked = self.treeWidget.itemAt(position)
        if clicked is None:
            menu.addAction(translate("MainWindow", "Add Separator"), self.addSeparator)
            menu.addAction(translate("MainWindow", "Sort by My Order"), self.sortByMyOrder)
            self.addFoldActions(menu)
            menu.exec(self.treeWidget.viewport().mapToGlobal(position))
            return
        if isGroupItem(clicked):
            self.fillGroupMenu(menu, clicked.data(0, GROUP_ROLE))
            menu.exec(self.treeWidget.viewport().mapToGlobal(position))
            return
        if isModioItem(clicked):
            self.fillModioMenu(menu, clicked)
            menu.exec(self.treeWidget.viewport().mapToGlobal(position))
            return
        menu.addAction(self.actionDetails)
        menu.addSeparator()
        menu.addAction(self.actionSetPriority)
        menu.addAction(self.actionUnsetPriority)
        menu.addSeparator()
        menu.addAction(self.actionOpenFolder)
        menu.addAction(self.actionOpenNexusPage)
        menu.addSeparator()
        menu.addAction(self.actionUpdateMods)
        menu.addAction(self.actionSetNexusMod)
        menu.addAction(self.actionChooseFile)
        menu.addAction(self.actionRestoreBackup)
        menu.addSeparator()
        menu.addMenu(self.moveToGroupMenu(menu))
        menu.addAction(self.actionRename)
        menu.addAction(self.actionReinstallFiles)
        menu.addAction(self.actionReinstall_Mods)
        menu.addAction(self.actionUninstall_Mods)
        menu.addAction(self.actionEnable_Disable_Mods)
        menu.addAction(self.actionEnableAll)
        menu.addAction(self.actionDisableAll)
        menu.exec(self.treeWidget.viewport().mapToGlobal(position))

    def openEditMenu(self, position):
        '''Right click menu on output'''
        menu = QMenu()
        menu.addAction(self.actionClearOutput)
        menu.exec(self.textEdit.viewport().mapToGlobal(position))

    def toolbarMenu(self, position):
        '''Right click menu on toolbar'''
        menu = QMenu(self.mainWindow)
        menu.addAction(self.actionAddToToolbar)
        rem = QMenu(menu)
        rem.setTitle(translate("MainWindow", "Remove.."))
        for action in list(self.customToolbarActions):
            temp = self.makeTempAction(action)
            rem.addAction(temp)
            del action
        menu.addAction(rem.menuAction())
        menu.exec(QCursor.pos())

    def removeFromToolbar(self, action):
        '''Creates menu for removing actions from toolbar'''
        self.toolBar.removeAction(action)
        if action in self.customToolbarActions:
            self.customToolbarActions.remove(action)
        self.toolBar.setVisible(bool(self.customToolbarActions))
        data.getConfig().removeOption("TOOLBAR", action.toolTip())

    def addToToolbar(self, selected=""):
        '''Adds custom action to the toolbar selected by user'''
        try:
            if not selected:
                temp = getFile(self)
                if temp:
                    selected = temp[0]
            if selected:
                fileInfo = QFileInfo(selected)
                iconProvider = QFileIconProvider()
                icon = iconProvider.icon(fileInfo)

                _, file = path.split(selected)
                fl, _ = path.splitext(file)
                actionTemp = QAction(self.mainWindow)
                actionTemp.triggered.connect(lambda: openFile(selected))
                actionTemp.setText(fl)
                actionTemp.setIcon(icon)
                actionTemp.setToolTip(selected)
                self.toolBar.addAction(actionTemp)
                self.customToolbarActions.append(actionTemp)
                self.toolBar.setVisible(True)
                data.getConfig().setOption("TOOLBAR", selected)
        except Exception as err:
            self.output(formatUserError(err))

    def restoreColumns(self):
        data.getConfig().setDefaultWindow()
        self.resizeColumns()

    def resizeColumns(self):
        for i in range(self.treeWidget.header().count()):
            self.treeWidget.header().resizeSection(i, data.getConfig().getWindowSection(i) or 60)
        for i in range(self.loadOrder.header().count() + 1):
            size = data.getConfig().getWindowSection(i, "lo")
            if size:
                self.loadOrder.header().resizeSection(i, size)
        try:
            hsplit0 = data.getConfig().get("WINDOW", "hsplit0")
            hsplit1 = data.getConfig().get("WINDOW", "hsplit1")
            if hsplit0 and hsplit1:
                # the side panel's details need room for the picture
                side = max(int(hsplit1), 380)
                self.horizontalSplitter_tree.setSizes([max(int(hsplit0) + int(hsplit1) - side, 600), side])
        except Exception as e:
            print(f"couldn't restore split: {e}")

    def output(self, appendation):
        '''Prints appendation to the output text field, and its last line in the status row'''
        self.textEdit.append(appendation)
        lines = [line.strip() for line in str(appendation).splitlines() if line.strip()]
        if lines:
            self.statusLabel.setText(lines[-1])
            self.statusLabel.setToolTip(str(appendation))

    def clear(self):
        '''Removes all text from output text field'''
        self.textEdit.setText("")

    def rename(self):
        '''Renames selected mod'''
        selected = self.getSelectedMods()
        if selected:
            try:
                renamed = 0
                for oldname in selected:
                    newname, ok = QInputDialog.getText(
                        None,
                        translate("MainWindow", "Rename") + " " + oldname,
                        translate("MainWindow", "Enter new mod name") + ": ",
                        QLineEdit.EchoMode.Normal,
                        oldname,
                    )
                    if ok and self.model.rename(oldname, newname):
                        renamed += 1
                    elif ok:
                        self.output(translate("MainWindow", "Couldn't rename") + " " + oldname)
                if renamed:
                    self.refreshList()
            except Exception as err:
                self.output(formatUserError(err))

    def details(self):
        '''Shows details of the selected mods'''
        selected = self.getSelectedMods()
        if selected:
            try:
                for modname in selected:
                    details = DetailsDialog(self, self.model.get(modname))
                    details.show()
            except Exception as err:
                self.output(formatUserError(err))

    def openFolder(self):
        '''Open folders of the selected mods/DLCs'''
        selected = self.getSelectedMods()
        if selected:
            try:
                for modname in selected:
                    mod = self.model.get(modname)
                    # Open mod folders
                    for file in mod.files:
                        self.model.explore(mod, file, is_dlc=False)
                    # Open DLC folders
                    for dlc in mod.dlcs:
                        self.model.explore(mod, dlc, is_dlc=True)
            except Exception as err:
                self.output(formatUserError(err))

    def modToggled(self, item, column):
        """Triggered when the mod check state is changed.
        Enables or disables the mod based on the current check state"""
        # other columns change too (icons, colours) and have no tick to read
        if column != 0 or isGroupItem(item) or isModioItem(item):
            return
        mod = self.model.modList.get(item.text(1))
        if mod is None or (item.checkState(0) == Qt.CheckState.Checked) == mod.enabled:
            return
        try:
            if item.checkState(column) == Qt.CheckState.Checked:
                incomplete = self.model.get(item.text(1)).enable()
                if incomplete:
                    for i in incomplete:
                        self.output(
                            translate("MainWindow", "Note: Additions to ")
                            + i
                            + translate("MainWindow", " could not be automatically installed.")
                        )
            elif item.checkState(column) == Qt.CheckState.Unchecked:
                self.model.get(item.text(1)).disable()
            self.model.write()
            self.refreshLoadOrder()
            self.alertRunScriptMerger()
        except Exception as err:
            self.output(formatUserError(err))

    def modDoubleClicked(self):
        '''Triggered when double clicked on the mod'''
        self.setPriority()

    def loadOrderDoubleClicked(self, item):
        '''Triggered when double clicked on the mod on the right panel. Sets priority'''
        try:
            selected = item.text(0)
            if selected[0] == "~":
                QMessageBox.critical(
                    self,
                    translate("MainWindow", "Error"),
                    translate("MainWindow", "You cannot set priority to disabled mod") + " ",
                )
                return
            selectedvalue = item.text(1)
            if selectedvalue:
                value = int(selectedvalue)
            else:
                value = 0
            priority, ok = QInputDialog.getInt(
                self,
                translate("MainWindow", "Set Priority"),
                translate("MainWindow", "Enter new priority") + ": ",
                value,
            )
            if ok:
                data.getConfig().setPriority(str(selected), str(priority))
                data.getConfig().write_priority()
                self.refreshList()
        except Exception as err:
            self.output(formatUserError(err))

    @throttle(25)
    def increaseLoadOrderPriority(self):
        '''Increases the priority of the selected mods in the load order list'''
        items = self.loadOrder.selectedItems()
        if items:
            item = items[0]
            selected = item.text(0)
            selectedvalue = item.text(1)
            if selectedvalue:
                value = int(selectedvalue)
            else:
                value = 0
            value = value + 1
            data.getConfig().setPriority(str(selected), str(value))
            item.setText(1, str(value))
            data.getConfig().write_priority()

    @throttle(25)
    def decreaseLoadOrderPriority(self):
        '''Decreases the priority of the selected mods in the load order list'''
        items = self.loadOrder.selectedItems()
        if items:
            item = items[0]
            selected = item.text(0)
            selectedvalue = item.text(1)
            if selectedvalue:
                value = max(-1, int(selectedvalue) - 1)
                if value < 0:
                    data.getConfig().removePriority(str(selected))
                    item.setText(1, "")
                else:
                    data.getConfig().setPriority(str(selected), str(value))
                    item.setText(1, str(value))
                data.getConfig().write_priority()

    def alertPopupChanged(self):
        '''Triggered when option to alert popup is changed. Saves the change'''
        if self.actionAlert_to_run_Script_Merger.isChecked():
            data.getConfig().allowpopups = "1"
        else:
            data.getConfig().allowpopups = "0"

        data.getConfig().write_config()

    def nativeFileDialogsChanged(self):
        '''Triggered when option to use native file dialogs is changed. Saves the change'''
        if self.actionUseNativeFileDialogs.isChecked():
            data.getConfig().set("SETTINGS", "usenativedialog", "1")
        else:
            data.getConfig().set("SETTINGS", "usenativedialog", "0")

    def changeLanguage(self, language):
        '''Triggered when language is changed. Saves the change and restarts the program'''
        data.getConfig().language = str(language)
        button = QMessageBox.question(
            self,
            translate("MainWindow", "Change language"),
            translate("MainWindow", "You need to restart the program to apply the changes.")
            + "\n"
            + translate("MainWindow", "Do you want to restart it now?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if button == QMessageBox.StandardButton.Yes:
            restartProgram()

    def checkLanguage(self):
        '''Checks which language is selected, and checks it'''
        language = data.getConfig().language
        for lang in self.menuSelect_Language.actions():
            if language == lang.text() + ".qm":
                lang.setChecked(True)
                break

    def setPriority(self):
        '''Sets the priority of the selected mods'''
        try:
            selected = self.getSelectedMods()
            if selected:
                old_priority = self.model.get(selected[0]).priority
                if not old_priority or not old_priority.isdecimal():
                    old_priority = 0
                else:
                    old_priority = int(old_priority)
                priority, ok = QInputDialog.getInt(
                    self,
                    translate("MainWindow", "Set Priority"),
                    translate("MainWindow", "Enter new priority: "),
                    old_priority,
                )
                if not ok:
                    return
                for modname in selected:
                    mod = self.model.get(modname)
                    if mod.enabled:
                        mod.priority = priority
                    else:
                        self.output(
                            translate("MainWindow", "You cannot set priority to disabled mod") + " '" + modname + "'"
                        )
                data.getConfig().write_priority()
                self.refreshList()
        except Exception as err:
            self.output(formatUserError(err))

    def unsetPriority(self):
        '''Removes priority of the selected mods'''
        selected = self.getSelectedMods()
        if selected:
            for modname in selected:
                self.model.get(modname).priority = None
            data.getConfig().write_priority()
            self.refreshList()

    @throttle(25)
    def increasePriority(self):
        '''Increases the priority of the selected mods'''
        selected = self.getSelectedMods()
        if selected:
            for modname in selected:
                self.model.get(modname).increasePriority()
            data.getConfig().write_priority()
            self.refreshList()

    @throttle(25)
    def decreasePriority(self):
        '''Decreases the priority of the selected mods'''
        selected = self.getSelectedMods()
        if selected:
            for modname in selected:
                self.model.get(modname).decreasePriority()
            data.getConfig().write_priority()
            self.refreshList()

    def changeGamePath(self):
        '''Changes game path'''
        if reconfigureGamePath():
            self.refreshList()
            self.translateUi()
            self.configureToolbar()

    def changeScriptMergerPath(self):
        '''Changes script merger path'''
        reconfigureScriptMergerPath()

    def installMods(self):
        '''Installs selected mods'''
        self.clear()
        file = getFile(self, data.getConfig().lastpath or "", "*.zip *.rar *.7z")
        self.installModFiles(file)

    def installModFiles(self, file):
        '''Installs passed list of mods'''
        successCount = 0
        errorCount = 0
        incompleteCount = 0
        try:
            if file:
                progress = 0
                progressMax = len(file)
                before = self.modVersions()
                installer = Installer(self.model, output=self.output, duplicate=self.handleDuplicate)
                for mod in file:
                    waiting = unfinishedDownload(mod)
                    if waiting:
                        self.output(f"{path.basename(mod)}: {waiting}")
                        errorCount += 1
                        progress += 1
                        continue
                    progressStart = 100 * progress / progressMax
                    progressEnd = 100 * (progress + 1) / progressMax
                    progressCur = progressEnd - progressStart
                    installer.progress = lambda p: self.setProgress(progressStart + progressCur * p)
                    result, count, incomplete = installer.installMod(mod)
                    if result:
                        successCount += count
                        if count:
                            self.keepArchive(mod)
                    else:
                        errorCount += 1
                    if incomplete:
                        incompleteCount += 1
                    progress += 1
                    self.setProgress(100 * progress / progressMax)
                lastpath, _ = path.split(file[0])
                data.getConfig().lastpath = lastpath
                self.refreshList()
                self.reportDuplicates()
                if incompleteCount:
                    MessageAlertIncompleteInstallation()
                if successCount:
                    self.alertRunScriptMerger()
                    self.checkRequirementsAfterInstall(file)
                    self.checkSharedutilsAfterInstall(before)
                self.setProgress(0)
            else:
                self.output(translate("MainWindow", "Installation canceled"))
        except Exception as err:
            self.setProgress(0)
            self.output(formatUserError(err))
            errorCount += 1
        self.output(
            "> "
            + translate("MainWindow", "Installed")
            + f" {successCount} "
            + translate("MainWindow", "mods or dlcs")
            + f" ({errorCount} "
            + translate("MainWindow", "errors")
            + ")"
            + (f" ({incompleteCount} " + translate("MainWindow", "incomplete") + ")" if incompleteCount else "")
        )
        return successCount

    def uninstallMods(self):
        '''Uninstalls selected mods'''
        try:
            selected = self.getSelectedMods()
            if selected:
                errors = 0
                clicked = QMessageBox.question(
                    self,
                    translate("MainWindow", "Confirm"),
                    translate("MainWindow", "Are you sure you want to uninstall ")
                    + str(len(selected))
                    + translate("MainWindow", " selected mods?"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if clicked == QMessageBox.StandardButton.Yes:
                    progress = 0
                    progressMax = len(selected)
                    installer = Installer(self.model, output=self.output)
                    for modname in selected:
                        success = installer.uninstallMod(self.model.get(modname))
                        if not success:
                            errors += 1
                        progress += 1
                        self.setProgress(100 * progress / progressMax)
                    self.refreshList()
                    self.setProgress(0)
                    if errors:
                        self.output(
                            translate("MainWindow", "Failed to uninstall ")
                            + str(errors)
                            + " "
                            + translate("MainWindow", "mods")
                        )
                    else:
                        self.alertRunScriptMerger()
        except Exception as err:
            self.setProgress(0)
            self.output(formatUserError(err))

    def modArchive(self, mod: Mod) -> str:
        """The archive a mod was installed from, if it's still around: the one its entry names in the archive
        folder, or the newest download of the same Nexus file that is still there"""
        if mod.nexus and mod.nexus.archive:
            candidate = path.join(data.getConfig().archives, path.basename(mod.nexus.archive))
            if path.isfile(candidate) and path.getsize(candidate) > 0:
                return candidate
        for entry in self.downloadHistory.entries:
            sameFile = (
                mod.nexus and entry.modId == mod.nexus.modId and entry.fileId and entry.fileId == mod.nexus.fileId
            )
            if (sameFile or entry.mod == mod.name) and entry.archiveExists and path.getsize(entry.archive) > 0:
                return entry.archive
        return ""

    def reinstallFromArchives(self) -> None:
        """Installs the selected mods again from their archives, downloading the same Nexus file when the archive
        is gone. Works like an update: each mod is backed up first and keeps its priority, enabled state, separator
        and settings."""
        names = [name for name in self.getSelectedMods() if name in self.model.modList]
        if not names or self.nexusBusy():
            return
        local, downloads, impossible = [], [], []
        for name in names:
            mod = self.model.modList[name]
            archive = self.modArchive(mod)
            if archive:
                local.append((name, archive))
            elif mod.nexus and mod.nexus.modId and mod.nexus.fileId:
                downloads.append(name)
            else:
                impossible.append(name)
        for name in impossible:
            self.output(
                f"{name}: "
                + translate("MainWindow", "its archive isn't in")
                + f" {data.getConfig().archives} "
                + translate("MainWindow", "and the Nexus file isn't known. Drag its archive onto the window instead.")
            )
        if not local and not downloads:
            return
        text = (
            translate("MainWindow", "Reinstall")
            + f" {plural(len(local) + len(downloads), translate('MainWindow', 'mod'))}?\n\n"
            + translate(
                "MainWindow",
                "Each one is backed up first, then installed again from its archive. Its priority, enabled state, "
                "separator and in-game settings are kept.",
            )
        )
        if downloads:
            text += (
                "\n\n"
                + translate("MainWindow", "These are downloaded from Nexus again first:")
                + "\n"
                + "\n".join(downloads)
            )
        answer = QMessageBox.question(
            self,
            translate("MainWindow", "Reinstall"),
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not downloads:
            self.reinstallArchives(local)
            return
        client = self.nexusClient()
        if client is None:
            return
        archives = data.getConfig().archives
        wanted = [(name, self.model.modList[name].nexus) for name in downloads]

        def work(progress):
            results = []
            for index, (name, nexus) in enumerate(wanted):
                try:
                    file = client.modFiles(nexus.modId).files.get(nexus.fileId)
                    if file is None:
                        raise NexusError(translate("MainWindow", "that file is no longer on Nexus"))
                    url = client.downloadLinks(nexus.modId, file.fileId)[0]
                    archive = client.download(
                        url, archives, file.fileName or f"{nexus.modId}-{file.fileId}.zip", file.size
                    )
                    results.append((name, archive, ""))
                except NexusError as error:
                    if error.status in (401, 429):
                        raise
                    results.append((name, "", str(error)))
                progress(index + 1, len(wanted))
            return results

        self.output(
            translate("MainWindow", "Downloading")
            + " "
            + plural(len(wanted), "mod")
            + " "
            + translate("MainWindow", "from Nexus again...")
        )
        self.runNexus(
            work,
            lambda results: self.reinstallDownloaded(local, results),
            download=", ".join(name for name, _ in wanted),
            inBytes=False,
        )

    def reinstallDownloaded(self, local: list, results: list) -> None:
        jobs = list(local)
        for name, archive, error in results:
            mod = self.model.modList.get(name)
            nexus = mod.nexus if mod is not None else None
            self.downloadHistory.add(
                Download(
                    0,
                    nexus.modId if nexus else 0,
                    nexus.fileId if nexus else 0,
                    nexus.fileName if nexus else name,
                    nexus.version if nexus else "",
                    archive,
                    REINSTALL,
                    DOWNLOAD_FAILED if error else KEPT,
                    error,
                    name,
                )
            )
            if error:
                self.output(f"{name}: {error}")
            else:
                jobs.append((name, archive))
        self.reinstallArchives(jobs)

    def reinstallArchives(self, jobs: list) -> None:
        """Installs each (mod name, archive) into the mod's own entry, the way an update does"""
        if not jobs:
            return
        updater = ModUpdater(self.model, output=self.output)
        before = self.modVersions()
        done = failed = incomplete = 0
        for index, (name, archive) in enumerate(jobs):
            mod = self.model.modList.get(name)
            if mod is None:
                continue
            self.output(
                translate("MainWindow", "Reinstalling")
                + f" {name} "
                + translate("MainWindow", "from")
                + f" {path.basename(archive)}"
            )
            updater.progress = lambda p, i=index: self.setProgress(100 * (i + p) / len(jobs))
            nexus = replace(mod.nexus, archive=path.basename(archive)) if mod.nexus else None
            result = updater.update(mod, archive, nexus, reason="reinstall")
            if result.success:
                done += 1
                for entry in self.downloadHistory.entries:
                    if entry.archive == archive and entry.source == REINSTALL and entry.mod == name:
                        self.downloadHistory.finish(entry, INSTALLED)
                        break
            else:
                failed += 1
            if result.incomplete:
                incomplete += 1
        self.setProgress(0)
        self.refreshList()
        self.output(
            "> "
            + translate("MainWindow", "Reinstalled")
            + " "
            + plural(done, translate("MainWindow", "mod"))
            + (f" ({plural(failed, translate('MainWindow', 'error'))})" if failed else "")
        )
        if incomplete:
            MessageAlertIncompleteInstallation()
        if done:
            self.alertRunScriptMerger()
            self.checkSharedutilsAfterInstall(before)

    def reinstallMods(self):
        '''Reinstalls selected mods'''
        try:
            selected = self.getSelectedMods()
            if selected:
                errors = 0
                incompleteCount = 0
                clicked = QMessageBox.question(
                    self,
                    translate("MainWindow", "Confirm"),
                    translate("MainWindow", "Restore default settings for ")
                    + str(len(selected))
                    + translate("MainWindow", " selected mods?")
                    + "\n\n"
                    + translate(
                        "MainWindow",
                        "This resets mod settings to their defaults and enables disabled mods.\n"
                        "Mod files and missing menu XML files are not restored.",
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if clicked == QMessageBox.StandardButton.Yes:
                    self.setProgress(20)
                    installer = Installer(self.model, output=self.output)
                    for modname in selected:
                        success, incomplete = installer.reinstallMod(self.model.get(modname))
                        if not success:
                            errors += 1
                        if incomplete:
                            incompleteCount += 1
                    self.setProgress(100)
                    self.refreshList()
                    self.setProgress(0)
                    if incompleteCount:
                        MessageAlertIncompleteInstallation()
                    if errors:
                        self.output(
                            translate("MainWindow", "Failed to reinstall ")
                            + str(errors)
                            + " "
                            + translate("MainWindow", "mods")
                        )
                    if errors < len(selected):
                        self.alertRunScriptMerger()
        except Exception as err:
            self.setProgress(0)
            self.output(formatUserError(err))

    @staticmethod
    def launchThroughSteam():
        return platform not in ("win32", "cygwin") or (data.getConfig().gameversion == "re" and data.getConfig().steam)

    @staticmethod
    def gameLaunchLabel():
        if not data.getConfig().gamelaunchcommand and CustomMainWidget.launchThroughSteam():
            return "Steam"
        return data.getConfig().graphicsapi

    def runTheGame(self):
        '''Runs the game'''
        try:
            command = data.getConfig().gamelaunchcommand
            if command:
                subprocess.Popen(command if platform in ("win32", "cygwin") else shlex.split(command))
            elif CustomMainWidget.launchThroughSteam():
                openUrl("steam://rungameid/292030")
            else:
                gamepath = data.getConfig().gameexe
                if not gamepath:
                    return
                directory, _ = path.split(gamepath)
                subprocess.Popen([gamepath], cwd=directory)
        except Exception as err:
            self.output(formatUserError(err))

    def runScriptMerger(self):
        '''Runs script merger'''
        try:
            scriptmergerpath = data.getConfig().scriptmerger
            if not scriptmergerpath:
                self.detectScriptMerger()
                scriptmergerpath = data.getConfig().scriptmerger
            if not scriptmergerpath:
                self.changeScriptMergerPath()
                scriptmergerpath = data.getConfig().scriptmerger
            if not scriptmergerpath:
                return
            if self.scriptMergerRunning():
                self.output(translate("MainWindow", "Script Merger is already open."))
                return
            directory, _ = path.split(scriptmergerpath)
            if platform == "win32" or platform == "cygwin":
                process = subprocess.Popen([scriptmergerpath], cwd=directory)
            elif platform == "linux" or platform == "darwin":
                command = data.getConfig().mergerlaunchcommand
                if command:
                    process = subprocess.Popen(command, cwd=directory, shell=True)
                else:
                    process = subprocess.Popen(["wine", scriptmergerpath], cwd=directory)
            else:
                MessageUnsupportedOSAction("")
                return
            self.watchScriptMerger(process)
        except Exception as err:
            self.output(formatUserError(err))

    def scriptMergerRunning(self) -> bool:
        process = getattr(self, "scriptMergerProcess", None)
        return process is not None and process.poll() is None

    def watchScriptMerger(self, process) -> None:
        '''Checks every second whether Script Merger has closed, then reports what still needs merging'''
        self.scriptMergerProcess = process
        timer = getattr(self, "scriptMergerTimer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(1000)
            timer.timeout.connect(self.checkScriptMergerClosed)
            self.scriptMergerTimer = timer
        timer.start()
        self.output(translate("MainWindow", "Script Merger is open. Its results are checked here when you close it."))

    def checkScriptMergerClosed(self) -> None:
        if self.scriptMergerRunning():
            return
        self.scriptMergerTimer.stop()
        self.scriptMergerProcess = None
        self.scriptMergerClosed()

    def scriptMergerClosed(self) -> None:
        self.refreshLoadOrder()
        report = self.scriptConflictReport()
        if report is None:
            return
        pending = report.pending
        if not report.conflicts:
            self.output("> " + translate("MainWindow", "Script Merger closed. No scripts conflict."))
        elif not pending:
            self.output(
                "> "
                + translate("MainWindow", "Script Merger closed. All")
                + " "
                + plural(len(report.conflicts), translate("MainWindow", "script conflict"))
                + " "
                + translate("MainWindow", "merged.")
            )
        else:
            self.output(
                "> "
                + translate("MainWindow", "Script Merger closed.")
                + " "
                + plural(len(pending), translate("MainWindow", "script file"))
                + " "
                + (
                    translate("MainWindow", "still needs")
                    if len(pending) == 1
                    else translate("MainWindow", "still need")
                )
                + " "
                + translate("MainWindow", "merging (Mods > Script Conflicts).")
            )

    def selectAllMods(self):
        '''Selects all mods in the list, or in the load order when that has the focus'''
        if self.loadOrder.hasFocus():
            self.loadOrder.selectAll()
        else:
            self.treeWidget.selectAll()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        '''Space in the mod list ticks or unticks all selected mods'''
        if (
            watched is self.treeWidget
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Space
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            self.toggleSelectedMods()
            return True
        return super().eventFilter(watched, event)

    def toggleSelectedMods(self) -> None:
        '''Enables all selected mods, or disables them if they're all enabled already'''
        names = [name for name in self.getSelectedMods() if name in self.model.modList]
        if names:
            self.setModsEnabled(names, not all(self.model.get(name).enabled for name in names))

    def setVisibleModsEnabled(self, enabled: bool) -> None:
        '''Enables or disables every mod shown in the list (respecting the search)'''
        names = [item.text(1) for item in self.modItems()]
        self.setModsEnabled(names, enabled)

    def setModsEnabled(self, names: list[str], enabled: bool) -> int:
        '''Enables or disables the named mods together, saving and refreshing once'''
        changed = 0
        for name in names:
            mod = self.model.modList.get(name)
            if mod is None or mod.enabled == enabled:
                continue
            try:
                if enabled:
                    for incomplete in mod.enable():
                        self.output(
                            translate("MainWindow", "Note: Additions to ")
                            + incomplete
                            + translate("MainWindow", " could not be automatically installed.")
                        )
                else:
                    mod.disable()
                changed += 1
            except Exception as err:
                self.output(formatUserError(err))
        if not changed:
            return 0
        self.model.write()
        self.treeWidget.blockSignals(True)
        try:
            for item in self.modItems():
                mod = self.model.modList.get(item.text(1))
                if mod is not None:
                    item.setCheckState(0, Qt.CheckState.Checked if mod.enabled else Qt.CheckState.Unchecked)
        finally:
            self.treeWidget.blockSignals(False)
        self.refreshLoadOrder()
        self.output(
            (translate("MainWindow", "Enabled") if enabled else translate("MainWindow", "Disabled"))
            + " "
            + plural(changed, translate("MainWindow", "mod"))
        )
        self.alertRunScriptMerger()
        return changed

    def enableDisableMods(self):
        '''Changes checked state of the selected mods'''
        try:
            selected = [
                item for item in self.treeWidget.selectedItems() if not isGroupItem(item) and not isModioItem(item)
            ]
            if not selected:
                return
            self.setProgress(0)
            progress = 0
            progressMax = len(selected)
            for item in selected:
                if item.checkState(0) == Qt.CheckState.Checked:
                    item.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    item.setCheckState(0, Qt.CheckState.Checked)
                progress += 1
                self.setProgress(100 * progress / progressMax)
            self.refreshList()
            self.alertRunScriptMerger()
            self.setProgress(0)
        except Exception as err:
            self.setProgress(0)
            self.output(formatUserError(err))

    # Nexus

    def nexusClient(self) -> NexusClient | None:
        key = data.getConfig().nexusapikey or askApiKey(self)
        return NexusClient(key) if key else None

    def nexusBusy(self) -> bool:
        if self.nexusWorker is not None and self.nexusWorker.isRunning():
            self.output(translate("MainWindow", "Still working on the previous Nexus request..."))
            return True
        return False

    def runNexus(self, work, done, download: str = "", inBytes: bool = True) -> None:
        '''Runs work(progress) in the background and done(result) on the GUI thread. A download names what it
        fetches, to show it on the Downloads page while it runs.'''
        worker = NexusWorker(work, self)
        worker.progressed.connect(lambda current, total: self.setProgress(100 * current / total if total else 0))
        worker.succeeded.connect(done)
        worker.failed.connect(lambda message: self.output(message))
        worker.finished.connect(lambda: self.setProgress(0))
        worker.finished.connect(lambda: self.nexusFinished(worker))
        if download:
            transfer = Transfer(download, inBytes=inBytes)
            self.activeTransfer = transfer
            worker.progressed.connect(lambda current, total: self.transferProgressed(transfer, current, total))
            worker.finished.connect(lambda: self.transferFinished(transfer))
        self.nexusWorker = worker
        worker.start()
        self.showTransfers()

    def transferProgressed(self, transfer: Transfer, done: int, total: int) -> None:
        transfer.done, transfer.total = done, total
        self.showTransfers()

    def transferFinished(self, transfer: Transfer) -> None:
        if self.activeTransfer is transfer:
            self.activeTransfer = None
        self.showTransfers()

    def nexusLabel(self, modId: int) -> str:
        '''A Nexus mod's name when its details are loaded, else its number'''
        details = self.nexusDetails.get(modId)
        return details.name if details and details.name else translate("MainWindow", "Nexus mod") + f" {modId}"

    def showTransfers(self) -> None:
        '''The running download and the queued Nexus links on the Downloads page, and their count on the rail'''
        transfers = [self.activeTransfer] if self.activeTransfer is not None else []
        for url in self.nxmQueue:
            link = parseNxm(url)
            if link is not None and link.game == GAME_DOMAIN:
                transfers.append(Transfer(self.nexusLabel(link.modId), waiting=True))
        self.downloads.setTransfers(transfers)
        # while something downloads the rail counts that, otherwise the archives not installed
        self.rail.setBadge("downloads", len(transfers) or self.notInstalledCount, statusColor("update"))
        self.showPageTitle()

    def nexusFinished(self, worker: NexusWorker) -> None:
        '''Starts the next queued Nexus link once the previous request is done'''
        if self.nexusWorker is worker:
            self.nexusWorker = None
        self.processNxmQueue()

    def checkForUpdates(
        self, names: list[str] | None = None, showDialog: bool = True, onlyUpdates: bool = False
    ) -> None:
        '''Checks the given mods (default: all with a Nexus ID) and shows the updates dialog'''
        if self.nexusBusy():
            return
        mods = [
            mod for mod in self.model.all() if mod.nexus and mod.nexus.modId and (names is None or mod.name in names)
        ]
        if not mods:
            self.output(
                translate("MainWindow", "No mods with a Nexus ID to check.")
                + " "
                + translate("MainWindow", "Use Nexus > Set Nexus Mod... to link a mod to its Nexus page.")
            )
            return
        client = self.nexusClient()
        if client is None:
            return
        modIds = sorted({mod.nexus.modId for mod in mods if mod.nexus})
        checked = [mod.name for mod in mods]
        # Script Merger itself is checked along with all mods, when its version can be read
        scriptMergerId = 0
        if names is None and installedVersion(data.getConfig().scriptmerger):
            scriptMergerId = nexusId(data.getConfig().gameversion)

        def work(progress):
            results: dict[int | str, ModFiles | str] = {}
            for index, modId in enumerate(modIds):
                try:
                    results[modId] = client.modFiles(modId)
                except NexusError as error:
                    if error.status in (401, 429):
                        raise
                    results[modId] = str(error)
                progress(index + 1, len(modIds))
            if scriptMergerId:
                try:
                    results["scriptmerger"] = client.modFiles(scriptMergerId)
                except NexusError as error:
                    if error.status in (401, 429):
                        raise
                    results["scriptmerger"] = str(error)
            return results

        self.output(
            translate("MainWindow", "Checking")
            + " "
            + plural(len(checked), "mod")
            + " "
            + translate("MainWindow", "on Nexus...")
        )
        self.runNexus(work, lambda results: self.updatesChecked(checked, results, showDialog, onlyUpdates))

    def checkScriptMergerUpdate(self) -> None:
        '''Checks Nexus for a newer Script Merger on its own and offers to update it'''
        if self.nexusBusy():
            return
        if not data.getConfig().scriptmerger and not self.detectScriptMerger():
            self.output(
                translate("MainWindow", "Script Merger wasn't found. Set its path in Settings > Configure Settings.")
            )
            return
        if not installedVersion(data.getConfig().scriptmerger):
            self.output(translate("MainWindow", "Couldn't read the version of") + f" {data.getConfig().scriptmerger}")
            return
        client = self.nexusClient()
        if client is None:
            return
        modId = nexusId(data.getConfig().gameversion)

        def work(progress):
            try:
                return client.modFiles(modId)
            except NexusError as error:
                if error.status in (401, 429):
                    raise
                return str(error)

        self.output(translate("MainWindow", "Checking Script Merger on Nexus..."))
        self.runNexus(work, self.scriptMergerChecked)

    def scriptMergerChecked(self, files: ModFiles | str) -> None:
        '''Reports whether a newer Script Merger is on Nexus and offers to update it'''
        installed = installedVersion(data.getConfig().scriptmerger)
        if isinstance(files, str):
            self.output(translate("MainWindow", "Couldn't check Script Merger:") + f" {files}")
            return
        latest = latestScriptMerger(files)
        if latest is None:
            self.output(translate("MainWindow", "Nexus lists no Script Merger download."))
            return
        if not installed:
            return
        if not isNewer(latest.version, installed):
            self.output(translate("MainWindow", "Script Merger is up to date") + f" ({installed})")
            return
        url = f"https://www.nexusmods.com/{GAME_DOMAIN}/mods/{nexusId(data.getConfig().gameversion)}?tab=files"
        self.output(
            translate("MainWindow", "Script Merger")
            + f" {latest.version} "
            + translate("MainWindow", "is available, you have")
            + f" {installed}: {url}"
        )
        answer = askScriptMergerUpdate(self, latest.version, installed)
        if answer == SM_UPDATE:
            self.updateScriptMerger(latest)
        elif answer == SM_OPEN_PAGE:
            openUrl(url)

    def updateScriptMerger(self, file: NexusFile) -> None:
        '''Downloads file from Script Merger's Nexus page and installs it over the current Script Merger'''
        if self.nexusBusy():
            return
        if self.scriptMergerRunning():
            self.output(translate("MainWindow", "Close Script Merger before updating it."))
            return
        client = self.nexusClient()
        if client is None:
            return
        config = data.getConfig()
        executable, archives, backups = config.scriptmerger, config.archives, config.backups
        modId = nexusId(config.gameversion)

        def work(progress):
            try:
                url = client.downloadLinks(modId, file.fileId)[0]
            except NexusError as error:
                if error.status == 403:
                    # without Premium, Nexus only gives download links through its website
                    return None
                raise
            archive = client.download(url, archives, file.fileName or f"{modId}-{file.fileId}.zip", file.size,
                                      progress=progress)  # fmt: skip
            try:
                return archive, updateScriptMergerFiles(executable, archive, backups)
            except ScriptMergerUpdateFailed as error:
                return archive, str(error)

        self.output(translate("MainWindow", "Downloading Script Merger") + f" {file.version}...")
        self.runNexus(
            work, lambda result: self.scriptMergerUpdated(file, result), download=f"Script Merger {file.version}"
        )

    def scriptMergerUpdated(self, file: NexusFile, result) -> None:
        modId = nexusId(data.getConfig().gameversion)
        if result is None:
            url = f"https://www.nexusmods.com/{GAME_DOMAIN}/mods/{modId}?tab=files&file_id={file.fileId}"
            self.output(translate("MainWindow", "Nexus only allows this download from its website:") + f" {url}")
            openUrl(url)
            return
        archive, outcome = result
        entry = Download(0, modId, file.fileId, "Script Merger", file.version, archive, UPDATE, UPDATED,
                         mod="Script Merger")  # fmt: skip
        if isinstance(outcome, str):
            entry.result, entry.message = DOWNLOAD_FAILED, outcome
            self.downloadHistory.add(entry)
            self.output(outcome)
            QMessageBox.warning(self, translate("MainWindow", "Script Merger update failed"), outcome)
            return
        self.downloadHistory.add(entry)
        message = (
            translate("MainWindow", "Script Merger updated")
            + f" {outcome.previous or '?'} -> {outcome.version or '?'}."
        )
        if outcome.replaced:
            message += " " + translate("MainWindow", "The files it replaced are backed up in") + f" {outcome.backup}"
        self.output(message)
        if outcome.version and isNewer(file.version, outcome.version):
            self.output(
                translate("MainWindow", "Script Merger still reports version")
                + f" {outcome.version}, "
                + translate("MainWindow", "expected")
                + f" {file.version}."
            )

    def updatesChecked(
        self, names: list[str], results: dict, showDialog: bool = True, onlyUpdates: bool = False
    ) -> None:
        if "scriptmerger" in results:
            self.scriptMergerChecked(results["scriptmerger"])
        rows = []
        learned = False
        for name in names:
            mod = self.model.modList.get(name)
            if mod is None or mod.nexus is None:
                continue
            files = results.get(mod.nexus.modId)
            if isinstance(files, ModFiles):
                check = checkForUpdate(mod.nexus, files, mod.name, mod.date)
            else:
                check = UpdateCheck(FAILED, message=str(files or translate("MainWindow", "No response")))
            installed = check.installed
            if check.confident and installed and mod.nexus.fileId != installed.fileId:
                # identified the exact installed file (from its archive name), remember it
                mod.nexus = NexusInfo.fromFile(mod.nexus.modId, installed, mod.nexus.archive, mod.nexus.game)
                learned = True
            self.updateChecks[name] = check
            rows.append(UpdateRow(name, mod.nexus.version, check, mod.nexus.url))
        if learned:
            self.model.write()
        available = sum(1 for row in rows if row.check.state == UPDATE_AVAILABLE)
        failed = sum(1 for row in rows if row.check.state == FAILED)
        self.output(
            "> "
            + plural(available, translate("MainWindow", "update"))
            + " "
            + translate("MainWindow", "available")
            + (f" ({plural(failed, translate('MainWindow', 'error'))})" if failed else "")
        )
        for row in rows:
            if row.check.state == FAILED:
                self.output(f"  {row.name}: {row.check.message}")
        self.refreshList()
        if not showDialog:
            if available:
                self.output(translate("MainWindow", "Click Update to install them."))
            return
        if onlyUpdates:
            rows = [row for row in rows if row.check.state == UPDATE_AVAILABLE]
            if not rows:
                self.output(translate("MainWindow", "Every mod is up to date."))
                return
        if not rows:
            return
        dialog = UpdatesDialog(self, rows)
        if dialog.exec() and dialog.selection():
            self.downloadAndUpdate(dialog.selection())

    def updateSelectedMods(self) -> None:
        selected = self.getSelectedMods()
        if not selected:
            self.checkForUpdates(showDialog=True, onlyUpdates=True)
            return
        missing = [name for name in selected if not (self.model.get(name).nexus and self.model.get(name).nexus.modId)]
        for name in missing:
            self.output(name + ": " + translate("MainWindow", "no Nexus ID, use Set Nexus Mod... first"))
        if len(missing) < len(selected):
            self.checkForUpdates(selected)

    def downloadAndUpdate(self, selection: list[tuple[str, NexusFile]]) -> None:
        if self.nexusBusy():
            return
        client = self.nexusClient()
        if client is None:
            return
        jobs = []
        for name, file in selection:
            mod = self.model.modList.get(name)
            if mod is not None and mod.nexus:
                jobs.append((name, mod.nexus.modId, file))
        archives = data.getConfig().archives
        totalBytes = sum(max(file.size, 1) for _, _, file in jobs)

        def work(progress):
            results = []
            offset = 0
            for name, modId, file in jobs:
                try:
                    url = client.downloadLinks(modId, file.fileId)[0]
                    archive = client.download(
                        url,
                        archives,
                        file.fileName or f"{modId}-{file.fileId}.zip",
                        file.size,
                        progress=lambda done, _total, base=offset: progress(base + done, totalBytes),
                    )
                    results.append((name, modId, file, archive, '', 0))
                except NexusError as error:
                    if error.status in (401, 429):
                        raise
                    results.append((name, modId, file, '', str(error), error.status))
                offset += max(file.size, 1)
            return results

        self.output(
            translate("MainWindow", "Downloading")
            + f" {len(jobs)} "
            + translate("MainWindow", "files to")
            + f" {archives}"
        )
        self.runNexus(work, self.applyUpdates, download=", ".join(name for name, _, _ in jobs))

    def backUpSaves(self, reason: str) -> SaveBackup | None:
        '''Copies the newest game saves to the backup folder. Raises OSError if that fails.'''
        config = data.getConfig()
        backup = backupSaves(gameSavesFolder(config.settings or ""), config.backups, reason)
        if backup is None:
            self.output(translate("MainWindow", "Saves: nothing new to back up"))
        else:
            self.output(
                translate("MainWindow", "Backed up your")
                + f" {plural(backup.count, translate('MainWindow', 'newest save'), translate('MainWindow', 'newest saves'))} "
                + translate("MainWindow", "to")
                + f" {backup.directory}"
            )
        return backup

    def backUpSavesBeforeUpdate(self) -> bool:
        '''Returns False if the saves couldn't be copied and the user would rather not update'''
        if data.getConfig().backupsaves != "1":
            return True
        try:
            self.backUpSaves("before update")
            return True
        except OSError as error:
            self.output(translate("MainWindow", "Couldn't back up your saves:") + f" {error}")
            answer = QMessageBox.question(
                self,
                translate("MainWindow", "Save backup"),
                translate("MainWindow", "Couldn't back up your saves:")
                + f"\n{error}\n\n"
                + translate("MainWindow", "Update the mods anyway?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return answer == QMessageBox.StandardButton.Yes

    def showSaveBackups(self) -> None:
        config = data.getConfig()
        dialog = SaveBackupsDialog(self, listSaveBackups(config.backups), config.backups)
        dialog.backUpNow = lambda: self.saveBackupRequested(dialog)
        if not dialog.exec() or dialog.chosen is None:
            return
        try:
            copied, skipped = restoreSaves(dialog.chosen, gameSavesFolder(config.settings or ""))
        except OSError as error:
            self.output(translate("MainWindow", "Couldn't restore the saves:") + f" {error}")
            return
        self.output(
            "> "
            + translate("MainWindow", "Copied back")
            + f" {plural(len(copied), translate('MainWindow', 'file'))}"
            + (
                f", {plural(len(skipped), translate('MainWindow', 'file'))} "
                + translate("MainWindow", "already in the save folder were left as they are")
                if skipped
                else ""
            )
        )

    def saveBackupRequested(self, dialog) -> None:
        try:
            self.backUpSaves("manual")
        except OSError as error:
            self.output(translate("MainWindow", "Couldn't back up your saves:") + f" {error}")
        dialog.showBackups(listSaveBackups(data.getConfig().backups))

    def applyUpdates(self, results: list[tuple[str, int, NexusFile, str, str, int]]) -> None:
        updated = failed = incomplete = 0
        if any(archive and not error for _, _, _, archive, error, _ in results) and not self.backUpSavesBeforeUpdate():
            self.output(
                translate("MainWindow", "Update cancelled; the downloads are kept in") + f" {data.getConfig().archives}"
            )
            return
        updater = ModUpdater(self.model, output=self.output)
        before = self.modVersions()
        for index, (name, modId, file, archive, error, status) in enumerate(results):
            mod = self.model.modList.get(name)
            record = self.downloadHistory.add(
                Download(0, modId, file.fileId, file.name, file.version, archive, UPDATE, DOWNLOAD_FAILED, error, name)
            )
            if status == 403 and mod is not None:
                # free accounts can only download through the website's "Mod Manager Download" button
                self.downloadHistory.finish(record, WEBSITE)
                self.openNexusDownload(modId, file.fileId, name)
                continue
            if error or mod is None:
                self.output(f"{name}: {error or translate('MainWindow', 'no longer installed')}")
                if not error:
                    self.downloadHistory.finish(
                        record, KEPT, translate("MainWindow", "The mod was no longer installed")
                    )
                failed += 1
                continue
            self.output(
                translate("MainWindow", "Updating") + f" {name} " + translate("MainWindow", "to") + f" {file.version}"
            )
            updater.progress = lambda p, i=index: self.setProgress(100 * (i + p) / len(results))
            game = mod.nexus.game if mod.nexus else NexusInfo().game
            result = updater.update(mod, archive, NexusInfo.fromFile(modId, file, path.basename(archive), game))
            if result.success:
                updated += 1
                self.updateChecks[name] = UpdateCheck(UP_TO_DATE, file, file, confident=True)
                self.downloadHistory.finish(record, UPDATED)
            else:
                failed += 1
                self.downloadHistory.finish(
                    record, DOWNLOAD_FAILED, translate("MainWindow", "The update failed, see the log")
                )
            if result.incomplete:
                incomplete += 1
        self.setProgress(0)
        self.refreshList()
        self.output(
            "> "
            + translate("MainWindow", "Updated")
            + " "
            + plural(updated, translate("MainWindow", "mod"))
            + f" ({plural(failed, translate('MainWindow', 'error'))})"
            + (f" ({incomplete} " + translate("MainWindow", "incomplete") + ")" if incomplete else "")
        )
        if incomplete:
            MessageAlertIncompleteInstallation()
        if updated:
            self.alertRunScriptMerger()
            self.checkSharedutilsAfterInstall(before)

    def setNexusMod(self) -> None:
        '''Links the selected mods to their Nexus mod pages'''
        for name in self.getSelectedMods():
            mod = self.model.get(name)
            current = str(mod.nexus.modId) if mod.nexus and mod.nexus.modId else ""
            text, ok = QInputDialog.getText(
                self,
                translate("MainWindow", "Set Nexus Mod") + f" - {name}",
                translate("MainWindow", "Nexus mod ID or mod page URL (leave empty to unlink):"),
                QLineEdit.EchoMode.Normal,
                current,
            )
            if not ok:
                return
            if not text.strip():
                mod.nexus = None
            else:
                modId = parseModReference(text)
                if modId is None:
                    self.output(translate("MainWindow", "Not a Nexus mod ID or Witcher 3 mod URL:") + f" {text}")
                    continue
                if not (mod.nexus and mod.nexus.modId == modId):
                    mod.nexus = NexusInfo(modId)
            self.updateChecks.pop(name, None)
        self.model.write()
        self.refreshList()
        self.refreshNexusDetails(silent=True, onlyMissing=True)

    def chooseInstalledFile(self) -> None:
        '''Shows the files of the selected mod's Nexus page, to record which one is installed or switch to another'''
        selected = self.getSelectedMods()
        if len(selected) != 1:
            self.output(translate("MainWindow", "Select one mod."))
            return
        name = selected[0]
        mod = self.model.get(name)
        if not (mod.nexus and mod.nexus.modId):
            self.output(name + ": " + translate("MainWindow", "no Nexus ID, use Set Nexus Mod... first"))
            return
        if self.nexusBusy():
            return
        client = self.nexusClient()
        if client is None:
            return
        modId = mod.nexus.modId

        def work(progress):
            return client.modFiles(modId)

        self.runNexus(work, lambda files: self.installedFileChosen(name, files))

    def installedFileChosen(self, name: str, files: ModFiles) -> None:
        mod = self.model.modList.get(name)
        if mod is None or mod.nexus is None:
            return
        check = checkForUpdate(mod.nexus, files, mod.name, mod.date)
        recorded = bool(check.installed and mod.nexus.fileId == check.installed.fileId)
        dialog = NexusFileDialog(self, name, files, check.installed, recorded)
        if not dialog.exec():
            return
        file = dialog.selected()
        if file is None:
            return
        if dialog.choice == SWITCH:
            self.downloadAndUpdate([(name, file)])
            return
        mod.nexus = NexusInfo.fromFile(mod.nexus.modId, file, mod.nexus.archive, mod.nexus.game)
        self.updateChecks[name] = checkForUpdate(mod.nexus, files, mod.name, mod.date)
        self.model.write()
        self.refreshList()
        self.output(name + ": " + translate("MainWindow", "recorded as") + f" {file.label()}")

    def openNexusPage(self) -> None:
        for name in self.getSelectedMods():
            mod = self.model.get(name)
            if mod.nexus and mod.nexus.modId:
                openUrl(mod.nexus.url)
            else:
                self.output(name + ": " + translate("MainWindow", "no Nexus ID, use Set Nexus Mod... first"))

    def restoreBackup(self) -> None:
        selected = self.getSelectedMods()
        if len(selected) != 1:
            self.output(translate("MainWindow", "Select one mod to restore."))
            return
        name = selected[0]
        updater = ModUpdater(self.model, output=self.output)
        backups = updater.listBackups(name)
        if not backups:
            self.output(name + ": " + translate("MainWindow", "no backups found in") + f" {data.getConfig().backups}")
            return
        dialog = BackupsDialog(self, name, backups)
        backup = dialog.selected() if dialog.exec() else None
        if backup is None:
            return
        updater.progress = lambda p: self.setProgress(100 * p)
        result = updater.restore(backup)
        self.updateChecks.pop(name, None)
        self.setProgress(0)
        self.refreshList()
        if result.success:
            self.output("> " + translate("MainWindow", "Restored") + f" {name} ({backup.label()})")
            if result.incomplete:
                MessageAlertIncompleteInstallation()
            self.alertRunScriptMerger()
        else:
            self.output("> " + translate("MainWindow", "Could not restore") + f" {name}")

    def handleDuplicate(self, mod: Mod, source: str) -> tuple[bool, int, int] | None:
        '''Asks what to do when a mod being installed looks like one that's already installed.
        Returns the install result when it's handled here, or None to install it as usual.'''
        matches = matchingEntries(self.model, mod)
        if not matches:
            return None
        first = matches[0]
        version = versionNote(mod, first.mod)
        updater = ModUpdater(self.model, output=self.output)
        newName = updater.newNameFor(first.mod, mod.name)
        keeping = (
            translate("MainWindow", "with this version, renaming it to")
            + f" '{newName}' "
            + translate("MainWindow", "and keeping its priority, enabled state and settings, after backing it up.")
            if newName
            else translate(
                "MainWindow",
                "with this version, keeping its name, priority, enabled state and settings, after backing it up.",
            )
        )
        text = (
            f"'{mod.name}' "
            + translate("MainWindow", "looks like a mod that's already installed:")
            + "\n\n"
            + "\n".join(f"  {match.mod.name} {translate('MainWindow', match.reason)}" for match in matches)
            + "\n\n"
            + (version + "\n\n" if version else "")
            + translate("MainWindow", "Update replaces")
            + f" '{first.mod.name}' "
            + keeping
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(translate("MainWindow", "Already installed?"))
        box.setText(text)
        update = box.addButton(
            translate("MainWindow", "Update") + f" '{first.mod.name}'", QMessageBox.ButtonRole.AcceptRole
        )
        separate = box.addButton(translate("MainWindow", "Install separately"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(translate("MainWindow", "Skip"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(update)
        box.exec()
        clicked = box.clickedButton()
        if clicked == separate:
            if mod.name in self.model.modList:
                # installing under the same name would replace that entry and leave its files untracked
                self.output(
                    translate("MainWindow", "An entry named")
                    + f" '{mod.name}' "
                    + translate("MainWindow", "already exists, so this one is installed as")
                    + f" '{self.uniqueName(mod.name)}'"
                )
                mod._name = self.uniqueName(mod.name)
            return None
        if clicked != update:
            self.output(translate("MainWindow", "Skipped") + f" {mod.name}")
            return True, 0, 0
        existing = first.mod
        nexus = mod.nexus
        if nexus is None and existing.nexus:
            # a new version from an archive that doesn't say which Nexus file it is
            nexus = NexusInfo(existing.nexus.modId, game=existing.nexus.game)
        oldName = existing.name
        result = updater.update(existing, source, nexus, "install", newName)
        self.updateChecks.pop(oldName, None)
        if newName:
            self.updateChecks.pop(newName, None)
        return result.success, int(result.success), int(result.incomplete)

    def uniqueName(self, name: str) -> str:
        number = 2
        candidate = f"{name} ({number})"
        while candidate in self.model.modList:
            number += 1
            candidate = f"{name} ({number})"
        return candidate

    def reportDuplicates(self) -> None:
        '''Lists entries that clash with each other'''
        for name, reasons in sorted(duplicateEntries(self.model).items(), key=lambda item: item[0].lower()):
            for reason in reasons:
                self.output(translate("MainWindow", "Installed twice?") + f" {name} {reason}")

    def keepArchive(self, archive: str) -> None:
        '''Copies an installed archive into the download folder, so its original file is kept'''
        if data.getConfig().keeparchives != "1" or not isArchive(archive) or not path.isfile(archive):
            return
        folder = data.getConfig().archives
        try:
            if path.normcase(path.realpath(path.dirname(archive))) == path.normcase(path.realpath(folder)):
                return
            target = path.join(folder, path.basename(archive))
            if path.isfile(target) and path.getsize(target) == path.getsize(archive):
                return
            os.makedirs(folder, exist_ok=True)
            target = uniquePath(folder, path.basename(archive))
            shutil.copy2(archive, target)
            self.output(translate("MainWindow", "Kept a copy of the archive in") + f" {target}")
        except OSError as error:
            self.output(translate("MainWindow", "Could not keep a copy of the archive:") + f" {error}")

    def keepArchivesChanged(self) -> None:
        data.getConfig().keeparchives = "1" if self.actionKeepArchives.isChecked() else "0"

    # nxm:// links

    def nxmRegistration(self) -> NxmRegistration:
        return NxmRegistration(WindowsRegistry(), data.getConfig())

    def handleNxmChanged(self) -> None:
        '''Makes this program the handler for Nexus "Mod Manager Download" links, or gives them back'''
        registration = self.nxmRegistration()
        try:
            if self.actionHandleNxm.isChecked():
                registration.register()
                data.getConfig().nxmwanted = "1"
                data.getConfig().nxmnoask = ""
                previous = registration.previous()
                self.output(
                    translate("MainWindow", "Nexus links (nxm://) now open here.")
                    + (
                        " " + translate("MainWindow", "Links for other games are passed to") + f" {previous}"
                        if previous
                        else ""
                    )
                )
            elif registration.unregister():
                data.getConfig().nxmwanted = "0"
                self.output(translate("MainWindow", "Nexus links open with the previous program again."))
            else:
                self.output(
                    translate(
                        "MainWindow", "No previous program was recorded for Nexus links, so they still open here."
                    )
                )
                self.actionHandleNxm.setChecked(True)
        except OSError as error:
            self.output(translate("MainWindow", "Could not change the Nexus link handler:") + f" {error}")
            self.actionHandleNxm.setChecked(registration.isRegistered())

    def showNxmState(self) -> None:
        """Ticks Handle Nexus Links only while the links really open here (another program may have taken them)"""
        if platform != "win32":
            return
        try:
            self.actionHandleNxm.setChecked(self.nxmRegistration().isRegistered())
        except OSError:
            pass

    def makeDefaultNxmHandler(self) -> None:
        """Opens Nexus links here again, whichever program has them now"""
        registration = self.nxmRegistration()
        try:
            current = registration.registry.get()
            if registration.isRegistered():
                self.output(translate("MainWindow", "Nexus links already open in W3MM."))
                return
            registration.register()
        except OSError as error:
            self.output(translate("MainWindow", "Could not change the Nexus link handler:") + f" {error}")
            return
        data.getConfig().nxmwanted = "1"
        data.getConfig().nxmnoask = ""
        self.actionHandleNxm.setChecked(True)
        previous = registration.previous()
        self.output(
            translate("MainWindow", "Nexus links open in W3MM again")
            + (f" ({translate('MainWindow', 'they were going to')} {handlerName(current)})" if current else "")
            + ". "
            + (
                translate("MainWindow", "Links for other games are passed to") + f" {handlerName(previous)}."
                if previous
                else ""
            )
        )

    def checkNxmHandler(self) -> None:
        """At startup: if the user chose W3MM for Nexus links and another program has taken them since, offers to
        take them back (with an option to stop asking)"""
        config = data.getConfig()
        if platform != "win32" or config.nxmwanted != "1" or config.nxmnoask == "1":
            return
        registration = self.nxmRegistration()
        try:
            if registration.isRegistered():
                return
            current = registration.registry.get()
        except OSError:
            return
        other = handlerName(current)
        self.actionHandleNxm.setChecked(False)
        self.output(translate("MainWindow", "Nexus links now open with") + f" {other}.")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(translate("MainWindow", "Nexus links"))
        box.setText(
            translate("MainWindow", "Nexus \"Mod Manager Download\" links now open with")
            + f" {other}.\n\n"
            + translate("MainWindow", "Open them in W3MM again? Links for other games will still go to")
            + f" {other}."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        noAsk = QCheckBox(translate("MainWindow", "Don't ask again"), box)
        box.setCheckBox(noAsk)
        answer = box.exec()
        if noAsk.isChecked():
            config.nxmnoask = "1"
        if answer == QMessageBox.StandardButton.Yes:
            self.makeDefaultNxmHandler()
        elif noAsk.isChecked():
            self.output(
                translate("MainWindow", "Nexus > Make W3MM the Default for Nexus Links takes them back any time.")
            )

    def showGameSettings(self, tab: int = USER_TAB) -> None:
        config = data.getConfig()
        GameSettingsDialog(
            self,
            config.settings,
            config.usersettings,
            config.menu or "",
            list(self.model.all()),
            config.backups,
            tab,
            self.output,
        ).exec()

    def showReShade(self) -> None:
        config = data.getConfig()
        if not config.game:
            self.output(translate("MainWindow", "Set the game path first (Settings > Change Setting)."))
            return
        ReShadeDialog(self, config.game, config.backups, self.output).exec()

    # Updating the manager itself

    def checkManagerUpdate(self, automatic: bool = False) -> None:
        '''Looks for a newer release of the fork on GitHub. The automatic check (at startup, once a day) is silent
        unless there is one.'''
        if self.nexusWorker is not None and self.nexusWorker.isRunning():
            if not automatic:
                self.nexusBusy()
            return
        config = data.getConfig()
        if automatic:
            recordCheck(config)
        else:
            self.output(translate("MainWindow", "Checking GitHub for a newer manager..."))
        self.runNexus(lambda progress: checkManagerRelease(), lambda result: self.managerChecked(result, automatic))

    def managerChecked(self, result: UpdateCheckResult, automatic: bool) -> None:
        if result.errors and not automatic:
            self.output(result.errors[0])
            return
        if not result.available:
            if not automatic:
                latest = result.latest.version if result.latest else "?"
                self.output(
                    translate("MainWindow", "The manager is up to date")
                    + f" ({translate('MainWindow', 'you have')} {result.current}, "
                    + f"{translate('MainWindow', 'newest release')} {latest})."
                )
            return
        release = result.latest
        assert release is not None
        config = data.getConfig()
        installable = isInstalledBuild() and release.installable
        dialog = ManagerUpdateDialog(
            self, release, result.current, installable, config.get("SETTINGS", "checkmanagerupdates", "1") == "1"
        )
        dialog.exec()
        config.set("SETTINGS", "checkmanagerupdates", "1" if dialog.autoCheck.isChecked() else "0")
        if dialog.action == OPEN_PAGE:
            openUrl(release.url or RELEASES_URL)
        elif dialog.action == INSTALL:
            self.installManagerUpdate(release)
        else:
            self.output(
                translate("MainWindow", "FMTweak Form") + f" {release.version} "
                + translate("MainWindow", "is available (Help > Update Manager...).")
            )  # fmt: skip

    def installManagerUpdate(self, release) -> None:
        '''Downloads the release's installer and hands over to it'''
        if self.nexusBusy():
            return
        folder = data.getConfig().archives
        self.output(translate("MainWindow", "Downloading") + f" {release.installerName}...")
        self.runNexus(
            lambda progress: downloadInstaller(release, folder, progress=progress),
            lambda installer: self.managerInstallerReady(release, installer),
        )

    def managerInstallerReady(self, release, installer: str) -> None:
        self.output(translate("MainWindow", "Installing") + f" {release.version}. " +
                    translate("MainWindow", "The manager closes now and starts again when the update is done."))  # fmt: skip
        try:
            launchInstaller(installer)
        except OSError as error:
            self.output(translate("MainWindow", "Could not start the installer:") + f" {error}")
            return
        QTimer.singleShot(500, self.mainWindow.close)

    def showSetup(self) -> None:
        config = data.getConfig()
        before = (config.game, config.scriptmerger, config.archives, bool(config.nexusapikey))
        if not SetupDialog(self, config).exec():
            return
        self.output(translate("MainWindow", "Setup saved."))
        if config.game != before[0]:
            self.refreshList()
            self.translateUi()
            self.configureToolbar()
        if config.scriptmerger != before[1]:
            self.output(translate("MainWindow", "Script Merger:") + f" {config.scriptmerger or '-'}")
        if config.archives != before[2]:
            self.output(translate("MainWindow", "Downloads go to") + f" {config.archives}")
        if bool(config.nexusapikey) != before[3]:
            self.refreshNexusDetails(silent=True, onlyMissing=True)
            self.refreshEndorsements()

    def showLoadOrderPanel(self) -> None:
        """mods.settings holds what the load order panel shows, so the button goes there"""
        self.showPage("mods")
        self.showSideView("loadorder")
        self.loadOrder.setFocus()
        if self.loadOrder.topLevelItemCount() and self.loadOrder.currentItem() is None:
            self.loadOrder.setCurrentItem(self.loadOrder.topLevelItem(0))
        self.output(
            translate("MainWindow", "The load order on the right is mods.settings: drag mods to change it.")
            + " "
            + translate("MainWindow", "Mods > Game Settings... shows the game's other settings.")
        )

    def openNexusDownload(self, modId: int, fileId: int, name: str) -> None:
        '''Opens the website's download for a file, for accounts that can't download directly'''
        openUrl(f"https://www.nexusmods.com/{GAME_DOMAIN}/mods/{modId}?tab=files&file_id={fileId}&nmm=1")
        handled = self.actionHandleNxm.isChecked()
        self.output(
            f"{name}: "
            + translate("MainWindow", "Nexus only allows direct downloads for Premium members.")
            + " "
            + translate("MainWindow", "Its download page is open in your browser; click 'Slow download'.")
            + " "
            + (
                translate("MainWindow", "The download comes back here.")
                if handled
                else translate("MainWindow", "Turn on Nexus > Handle Nexus Links first, so the download comes here.")
            )
        )

    def raiseWindow(self) -> None:
        if self.mainWindow.isMinimized():
            self.mainWindow.showNormal()
        self.mainWindow.raise_()
        self.mainWindow.activateWindow()

    def handleNxm(self, url: str) -> None:
        '''Queues a nxm:// link sent by the website (through Windows or another start of the program)'''
        self.nxmQueue.append(url)
        self.raiseWindow()
        self.processNxmQueue()
        self.showTransfers()

    def processNxmQueue(self) -> None:
        while self.nxmQueue and not (self.nexusWorker is not None and self.nexusWorker.isRunning()):
            url = self.nxmQueue.pop(0)
            link = parseNxm(url)
            if link is None:
                self.output(translate("MainWindow", "Not a Nexus download link:") + f" {url}")
                continue
            if link.game != GAME_DOMAIN:
                previous = data.getConfig().previousnxm
                if previous and forward(previous, url):
                    self.output(
                        translate("MainWindow", "Passed the") + f" {link.game} " + translate("MainWindow", "link on")
                    )
                else:
                    self.output(translate("MainWindow", "No program is set up for") + f" {link.game} " + "links")
                continue
            client = self.nexusClient()
            if client is None:
                self.nxmQueue.clear()
                return
            self.downloadNxm(client, link)
            return

    def downloadNxm(self, client: NexusClient, link: NxmLink) -> None:
        archives = data.getConfig().archives

        def work(progress):
            file = None
            try:
                file = client.modFiles(link.modId).files.get(link.fileId)
            except NexusError as error:
                if error.status in (401, 429):
                    raise
            try:
                url = client.downloadLinks(link.modId, link.fileId, link.key, link.expires)[0]
            except NexusError as error:
                if error.status == 403 and not link.key:
                    # a free account without a website link: the download has to start on the website
                    return link, file, ''
                raise
            name = file.fileName if file and file.fileName else f"{link.modId}-{link.fileId}.zip"
            archive = client.download(url, archives, name, file.size if file else 0, progress=progress)
            return link, file, archive

        self.output(translate("MainWindow", "Downloading from Nexus..."))
        self.runNexus(work, self.nxmDownloaded, download=self.nexusLabel(link.modId))

    def installFromNexus(self, details) -> None:
        '''Downloads and installs a mod picked in the Browse Nexus tab, asking which file if there are several'''
        if self.nexusBusy():
            return
        client = self.nexusClient()
        if client is None:
            return

        def work(progress):
            return client.modFiles(details.modId)

        self.runNexus(work, lambda files: self.chooseNexusFile(client, details, files))

    def chooseNexusFile(self, client: NexusClient, details, files: ModFiles) -> None:
        current = files.currentFiles()
        if not current:
            self.output(details.name + ": " + translate("MainWindow", "no files to download on Nexus"))
            return
        main = sorted(current, key=lambda file: (file.category != "MAIN", not file.primary, -file.uploaded))
        chosen = main[0]
        if len(main) > 1:
            # the archive name keeps files with the same name and version apart
            labels = [f"{file.label()}  ({file.fileName})" for file in main]
            label, ok = QInputDialog.getItem(
                self,
                translate("MainWindow", "Download") + f" {details.name}",
                translate("MainWindow", "Which file?"),
                labels,
                0,
                False,
            )
            if not ok:
                return
            chosen = main[labels.index(label)]
        self.downloadNxm(client, NxmLink(GAME_DOMAIN, details.modId, chosen.fileId))

    def nxmDownloaded(self, result: tuple[NxmLink, NexusFile | None, str]) -> None:
        link, file, archive = result
        record = Download(
            0,
            link.modId,
            link.fileId,
            file.name if file else "",
            file.version if file else "",
            archive,
            LINK,
            KEPT if archive else WEBSITE,
        )
        self.downloadHistory.add(record)
        if not archive:
            self.openNexusDownload(link.modId, link.fileId, file.name if file else str(link.modId))
            return
        filename = path.basename(archive)
        record.name = record.name or filename
        self.output(translate("MainWindow", "Downloaded") + f" {filename}")
        self.raiseWindow()
        label = f"{file.name} {file.version}".strip() if file else filename
        answer = QMessageBox.question(
            self,
            translate("MainWindow", "Nexus download"),
            translate("MainWindow", "Downloaded") + f" {label}.\n\n" + translate("MainWindow", "Install it now?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.output(translate("MainWindow", "Kept in") + f" {path.dirname(archive)}")
            return
        installed = self.installModFiles([archive])
        entries = [mod.name for mod in self.model.all() if mod.nexus and mod.nexus.archive == filename]
        if installed:
            self.downloadHistory.finish(record, INSTALLED, mod=", ".join(entries))
        else:
            self.downloadHistory.finish(
                record, DOWNLOAD_FAILED, translate("MainWindow", "Couldn't install it, see the log")
            )
        self.browse.markInstalled()
        if file is None:
            return
        # the website said exactly which file this is, so remember it on the entry it was installed into
        changed = False
        for mod in self.model.all():
            if mod.nexus and mod.nexus.modId == link.modId and mod.nexus.archive == filename:
                if mod.nexus.fileId != file.fileId:
                    mod.nexus = NexusInfo.fromFile(link.modId, file, filename, link.game)
                    self.updateChecks.pop(mod.name, None)
                    changed = True
        if changed:
            self.model.write()
            self.refreshList()

    # Nexus details: pictures, categories, summaries

    def linkedModIds(self) -> list[int]:
        return sorted({mod.nexus.modId for mod in self.model.all() if mod.nexus and mod.nexus.modId})

    def modDetails(self, mod: Mod):
        return self.nexusDetails.get(mod.nexus.modId) if mod.nexus and mod.nexus.modId else None

    def modCategory(self, mod: Mod) -> str:
        details = self.modDetails(mod)
        return details.category if details else ""

    def showPicture(self, row, mod: Mod) -> None:
        '''The mod's Nexus picture and a line about it, shown when the list has comfortable rows'''
        details = self.modDetails(mod)
        row.setData(1, PICTURE_ROLE, self.nexusDetails.image(details))
        parts = [self.modCategory(mod)]
        if details is not None and details.author:
            parts.append(details.author)
        version = mod.nexus.version if mod.nexus and mod.nexus.version else ""
        if version:
            parts.append(version if version[:1] in "vV" else "v" + version)
        row.setData(1, SUBTITLE_ROLE, " · ".join(part for part in parts if part))

    def updatePreview(self) -> None:
        selected = self.getSelectedMods()
        mod = self.model.modList.get(selected[0]) if selected else None
        details = self.modDetails(mod) if mod is not None else None
        remastered = data.getConfig().gameversion == "re"
        sections = self.previewSections(mod, details) if mod is not None else None
        self.preview.showMod(mod, details, self.nexusDetails.bestImage(details), remastered, sections)
        if mod is not None and self.sideStack.currentWidget() is self.loadOrder:
            self.showModInLoadOrder()
        if details is not None and details.pictureUrl and not self.nexusDetails.picture(details):
            self.loadPicture(details)

    def loadPicture(self, details: ModDetails) -> None:
        '''Downloads the mod's full-size picture for the side panel (the thumbnail shows until then)'''
        if self.pictureWorker is not None and self.pictureWorker.isRunning():
            return
        cache = self.nexusDetails
        worker = NexusWorker(lambda progress: cache.downloadPicture(details.pictureUrl), self)
        worker.succeeded.connect(lambda _: self.pictureLoaded(details))
        worker.failed.connect(lambda message: print("could not download picture:", message))
        self.pictureWorker = worker
        worker.start()

    def pictureLoaded(self, details: ModDetails) -> None:
        '''Shows the picture if its mod is still selected, else fetches the selected mod's'''
        selected = self.getSelectedMods()
        mod = self.model.modList.get(selected[0]) if selected else None
        current = self.modDetails(mod) if mod is not None else None
        if current is not None and current.modId == details.modId:
            self.updatePreview()
        elif current is not None and current.pictureUrl and not self.nexusDetails.picture(current):
            self.loadPicture(current)

    def previewSections(self, mod: Mod, details: ModDetails | None) -> PreviewSections:
        '''What the preview's Details, Files, Changelog and Requirements tabs show for an installed mod'''
        none = translate("MainWindow", "none")
        facts = [
            (translate("MainWindow", "Installed"), str(mod.date or "")),
            (translate("MainWindow", "Version"), mod.nexus.version if mod.nexus and mod.nexus.version else "-"),
            (
                translate("MainWindow", "Priority"),
                str(mod.priority) if priorityNumber(mod.priority) is not None else none,
            ),
            (translate("MainWindow", "Separator"), mod.group or none),
            (translate("MainWindow", "Contents"), contentsTags(mod).replace(" ", ", ") or none),
        ]
        linked = bool(mod.nexus and mod.nexus.modId)
        if linked:
            facts.append((translate("MainWindow", "Nexus ID"), str(mod.nexus.modId)))
        chips = [(self.modCategory(mod), "")]
        version = mod.nexus.version if mod.nexus and mod.nexus.version else ""
        if version:
            chips.append((version if version[:1] in "vV" else "v" + version, ""))
        if linked:
            chips.append((f"Nexus {mod.nexus.modId}", ""))
        if updateKind(self.updateChecks.get(mod.name)) == "update":
            chips.append((translate("MainWindow", "Update available"), "update"))
        chips.append(
            (translate("MainWindow", "Enabled"), "ok")
            if mod.enabled
            else (translate("MainWindow", "Disabled"), "muted")
        )
        return PreviewSections(
            chips=[(text, kind) for text, kind in chips if text],
            linked=linked,
            endorsed=linked and self.endorsements.endorsed(mod.nexus.modId),
            updateAvailable=updateKind(self.updateChecks.get(mod.name)) == "update",
            facts=[(label, value) for label, value in facts if value],
            files=self.filesHtml(mod),
            changelog=self.changelogHtml(mod),
            changelogLoadable=bool(mod.nexus and mod.nexus.modId) and not self.nexusBusy(),
            requirements=self.requirementsHtml(mod, details),
        )

    @staticmethod
    def filesHtml(mod: Mod) -> str:
        '''What the mod put where, grouped by folder'''
        groups = [
            (translate("MainWindow", "Mods folder"), mod.files),
            (translate("MainWindow", "DLC folder"), mod.dlcs),
            (translate("MainWindow", "Menus"), mod.menus),
            (translate("MainWindow", "Game folder (bin)"), mod.bins),
        ]
        parts = []
        for label, names in groups:
            if names:
                items = "".join(f"<li>{html.escape(str(name))}</li>" for name in names)
                parts.append(
                    f"<p style='margin:6px 0 2px'><b>{html.escape(label)}</b></p><ul style='margin:0'>{items}</ul>"
                )
        counts = [
            (
                len(mod.xmlkeys) + len(mod.hidden),
                translate("MainWindow", "menu entry"),
                translate("MainWindow", "menu entries"),
            ),
            (len(mod.inputsettings), translate("MainWindow", "key binding"), translate("MainWindow", "key bindings")),
            (len(mod.usersettings), translate("MainWindow", "setting"), translate("MainWindow", "settings")),
        ]
        added = [plural(count, one, many) for count, one, many in counts if count]
        if added:
            parts.append(
                "<p style='margin:6px 0 2px'>"
                + html.escape(translate("MainWindow", "Adds") + " " + ", ".join(added))
                + "</p>"
            )
        return "".join(parts) or html.escape(translate("MainWindow", "No files recorded for this mod."))

    def changelogHtml(self, mod: Mod) -> str:
        '''The changes up to the newest file when an update check has loaded the files, else how to get them'''
        if not (mod.nexus and mod.nexus.modId):
            return html.escape(translate("MainWindow", "Not linked to Nexus, so there's no changelog to show."))
        check = self.updateChecks.get(mod.name)
        if check is None or check.files is None:
            return html.escape(translate("MainWindow", "The changelog comes from Nexus with the mod's files."))
        target = check.target if check.state == UPDATE_AVAILABLE and check.target else check.installed
        if target is None:
            return html.escape(
                translate("MainWindow", "The installed file isn't known, so its changelog can't be found.")
            )
        installed = check.installed if check.installed is not target else None
        changes = changesBetween(check.files, installed, target)
        parts = []
        if check.state == UPDATE_AVAILABLE:
            parts.append("<p><i>" + html.escape(translate("MainWindow", "Changes in the update:")) + "</i></p>")
        for version, lines in changes:
            heading = version if version.lower().startswith("v") or not version[:1].isdigit() else "v" + version
            items = "".join(f"<li>{linkify(line)}</li>" for line in lines)
            parts.append(
                f"<p style='margin:6px 0 2px'><b>{html.escape(heading)}</b></p><ul style='margin:0'>{items}</ul>"
            )
        if not changes:
            parts.append(html.escape(translate("MainWindow", "The author didn't write a changelog for this file.")))
        parts.append(
            f"<p><a href='{html.escape(mod.nexus.url)}?tab=logs'>"
            + html.escape(translate("MainWindow", "All changes on Nexus"))
            + "</a></p>"
        )
        return "".join(parts)

    def requirementsHtml(self, mod: Mod, details: ModDetails | None) -> str:
        '''What the mod's Nexus page says it needs, and whether each one is there'''
        if details is None:
            if mod.nexus and mod.nexus.modId:
                return html.escape(
                    translate("MainWindow", "Nexus details haven't been loaded yet (Nexus > Refresh Nexus Details).")
                )
            return html.escape(translate("MainWindow", "Not linked to Nexus, so its requirements aren't known."))
        if not details.requirements:
            return html.escape(translate("MainWindow", "Its Nexus page lists no requirements."))
        try:
            problems = missingRequirements(self.model, self.nexusDetails.get, data.getConfig().dlc or "", [mod.name])
        except OSError:
            problems = []
        states = {
            (problem.requirement.name.casefold(), problem.requirement.modId): problem.state for problem in problems
        }
        wording = {
            MISSING: (translate("MainWindow", "not installed"), "problem"),
            DISABLED: (translate("MainWindow", "installed but disabled"), "warning"),
            DLC_MISSING: (translate("MainWindow", "expansion not installed"), "problem"),
            EXTERNAL: (translate("MainWindow", "outside Nexus, not checked"), "muted"),
        }
        items = []
        seen = set()
        for requirement in details.requirements:
            key = (requirement.name.casefold(), requirement.modId)
            if key in seen:
                continue
            seen.add(key)
            text, kind = wording.get(states.get(key, ""), (translate("MainWindow", "installed"), "ok"))
            name = html.escape(html.unescape(requirement.name))
            url = requirement.pageUrl
            if url:
                name = f"<a href='{html.escape(url)}'>{name}</a>"
            note = (
                f"<br><span style='color:{statusColor('muted').name()}'>{html.escape(html.unescape(requirement.notes))}</span>"
                if requirement.notes
                else ""
            )
            items.append(
                f"<li>{name} <span style='color:{statusColor(kind).name()}'>({html.escape(text)})</span>{note}</li>"
            )
        return "<ul style='margin:0'>" + "".join(items) + "</ul>"

    def loadChangelog(self) -> None:
        '''Checks the selected mod on Nexus so its files and their changelogs are known'''
        selected = self.getSelectedMods()
        if selected:
            self.checkForUpdates(names=[selected[0]], showDialog=False)

    def refreshNexusDetails(
        self, silent: bool = False, onlyMissing: bool = False, modIds: list[int] | None = None
    ) -> None:
        '''Fetches pictures, categories and summaries of the linked mods (or of modIds) in the background'''
        if self.detailsWorker is not None and self.detailsWorker.isRunning():
            return
        if modIds is None:
            modIds = self.nexusDetails.missing(self.linkedModIds()) if onlyMissing else self.linkedModIds()
        if not modIds:
            if not silent:
                self.output(translate("MainWindow", "No mods with a Nexus ID."))
            return
        cache = self.nexusDetails

        def work(progress):
            found = NexusGraphQL().details(modIds)
            for index, details in enumerate(found.values()):
                try:
                    cache.downloadImage(details.thumbnailUrl)
                except NexusError as error:
                    print("could not download thumbnail:", error)
                progress(index + 1, len(found))
            return found

        worker = NexusWorker(work, self)
        worker.succeeded.connect(lambda found: self.detailsFetched(found, silent))
        worker.failed.connect(
            lambda message: None if silent else self.output(translate("MainWindow", "Nexus details:") + f" {message}")
        )
        self.detailsWorker = worker
        if not silent:
            self.output(translate("MainWindow", "Loading Nexus details for") + " " + plural(len(modIds), "mod") + "...")
        worker.start()

    def detailsFetched(self, found: dict, silent: bool) -> None:
        self.nexusDetails.update(found)
        try:
            self.nexusDetails.save()
        except OSError as error:
            self.output(translate("MainWindow", "Couldn't save Nexus details:") + f" {error}")
        self.refreshList()
        self.updatePreview()
        if not silent:
            self.output(translate("MainWindow", "Loaded Nexus details for") + " " + plural(len(found), "mod"))

    def cleanUpArchives(self) -> None:
        '''Lists archives in the archive folder that may not be needed and moves the ticked ones away'''
        folder = data.getConfig().archives
        if not folder or not path.isdir(folder):
            self.output(translate("MainWindow", "The archive folder doesn't exist:") + f" {folder}")
            return
        tools = {nexusId(data.getConfig().gameversion): "Script Merger"}
        try:
            archives = scanArchives(folder, self.model, tools)
        except OSError as error:
            self.output(translate("MainWindow", "Couldn't read the archive folder:") + f" {error}")
            return
        if all(archive.state == IN_USE for archive in archives):
            self.output(translate("MainWindow", "Nothing to clean up in") + f" {folder}")
            return
        dialog = ArchiveCleanupDialog(self, folder, archives, OLD_ARCHIVES)
        if not dialog.exec() or not dialog.action:
            return
        chosen = dialog.selection()
        size = sizeText(sum(archive.size for archive in chosen))
        if dialog.action == RECYCLE:
            answer = QMessageBox.question(
                self,
                translate("MainWindow", "Clean Up Archives"),
                translate("MainWindow", "Move")
                + f" {plural(len(chosen), translate('MainWindow', 'file'))} ({size}) "
                + translate("MainWindow", "to the Recycle Bin?")
                + "\n\n"
                + translate("MainWindow", "They can be restored from there."),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            failed = recycle([archive.path for archive in chosen])
            where = translate("MainWindow", "the Recycle Bin")
        else:
            where = path.join(folder, OLD_ARCHIVES)
            failed = moveToFolder([archive.path for archive in chosen], where)
        moved = len(chosen) - len(failed)
        self.output(
            "> "
            + translate("MainWindow", "Moved")
            + f" {plural(moved, translate('MainWindow', 'archive'))} "
            + translate("MainWindow", "to")
            + f" {where}"
        )
        for archivePath, error in failed:
            self.output(f"  {path.basename(archivePath)}: {error}")

    # The Downloads page

    def scanDownloads(self) -> tuple[str, list]:
        folder = data.getConfig().archives or ""
        tools = {nexusId(data.getConfig().gameversion): "Script Merger"}
        return folder, scanArchives(folder, self.model, tools)

    @debounceGui(300)
    def refreshDownloads(self) -> None:
        self.downloads.refresh()

    def downloadsCounted(self, count: int) -> None:
        index = self.tabs.indexOf(self.downloads)
        label = translate("MainWindow", "Downloads")
        self.tabs.setTabText(index, f"{label} ({count})" if count else label)
        self.notInstalledCount = count
        self.showTransfers()

    def downloadDetailsWanted(self, modIds: list[int]) -> None:
        '''Loads the Nexus names and pictures of downloaded mods, once a session for each'''
        wanted = [modId for modId in modIds if modId not in self.detailsAsked]
        if not wanted or (self.detailsWorker is not None and self.detailsWorker.isRunning()):
            return
        self.detailsAsked.update(wanted)
        self.refreshNexusDetails(silent=True, modIds=wanted)

    def showDownloadHistory(self) -> None:
        self.downloadHistory.load()
        dialog = DownloadHistoryDialog(self, self.downloadHistory.entries)
        if dialog.exec() and dialog.chosen is not None:
            self.installModFiles([dialog.chosen.archive])

    # Endorsements: a thumb at the end of each Nexus mod's row, filled when the user endorsed it

    def showEndorsement(self, item, mod: Mod) -> None:
        if not (mod.nexus and mod.nexus.modId):
            return
        if self.endorsements.endorsed(mod.nexus.modId):
            item.setIcon(ENDORSE_COLUMN, icon("thumb-filled", color=statusColor("accent")))
            item.setToolTip(ENDORSE_COLUMN, translate("MainWindow", "You endorsed this mod. Click to take it back."))
        else:
            item.setIcon(ENDORSE_COLUMN, icon("thumb", color=statusColor("muted")))
            item.setToolTip(ENDORSE_COLUMN, translate("MainWindow", "Click to endorse this mod on Nexus"))

    def showEndorsements(self) -> None:
        self.treeWidget.blockSignals(True)
        try:
            for item in self.modItems():
                mod = self.model.modList.get(item.text(1))
                if mod is not None:
                    self.showEndorsement(item, mod)
        finally:
            self.treeWidget.blockSignals(False)

    def refreshEndorsements(self) -> None:
        """Loads which mods the user endorsed, quietly, when an API key is set"""
        key = data.getConfig().nexusapikey
        if not key or (self.endorsementsWorker is not None and self.endorsementsWorker.isRunning()):
            return
        client = NexusClient(key)
        worker = NexusWorker(lambda progress: client.endorsements(), self)
        worker.succeeded.connect(self.endorsementsLoaded)
        worker.failed.connect(lambda message: print("could not load endorsements:", message))
        self.endorsementsWorker = worker
        worker.start()

    def endorsementsLoaded(self, statuses: dict) -> None:
        self.endorsements.replace(statuses)
        self.showEndorsements()

    def modClicked(self, item, column: int) -> None:
        if column == ENDORSE_COLUMN and not isGroupItem(item) and not isModioItem(item):
            self.toggleEndorsement(item.text(1))

    def endorseSelected(self) -> None:
        selected = self.getSelectedMods()
        if selected:
            self.toggleEndorsement(selected[0])

    def toggleEndorsement(self, name: str) -> None:
        """Endorses the mod on Nexus, or takes the endorsement back after asking"""
        mod = self.model.modList.get(name)
        if mod is None or not (mod.nexus and mod.nexus.modId):
            return
        if self.endorseWorker is not None and self.endorseWorker.isRunning():
            return
        modId = mod.nexus.modId
        endorse = not self.endorsements.endorsed(modId)
        if not endorse:
            answer = QMessageBox.question(
                self,
                translate("MainWindow", "Endorsement"),
                translate("MainWindow", "Take back your endorsement of") + f" {name}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        client = self.nexusClient()
        if client is None:
            return
        version = mod.nexus.version
        worker = NexusWorker(lambda progress: client.endorse(modId, version, endorse), self)
        worker.succeeded.connect(lambda status: self.endorsementSent(name, modId, status))
        worker.failed.connect(lambda message: self.output(f"{name}: {message}"))
        self.endorseWorker = worker
        worker.start()

    def endorsementSent(self, name: str, modId: int, status: str) -> None:
        self.endorsements.set(modId, status)
        self.showEndorsements()
        self.updatePreview()
        if status == ENDORSED:
            self.output(translate("MainWindow", "Endorsed") + f" {name}. " + translate("MainWindow", "Thank you!"))
        else:
            self.output(translate("MainWindow", "Took back your endorsement of") + f" {name}")

    def checkRequirementsAfterInstall(self, archives: list[str]) -> None:
        '''After installing Nexus downloads, says if those mods need anything that isn't installed'''
        modIds = set()
        for archive in archives:
            parsed = parseArchiveName(path.basename(str(archive)))
            if parsed is not None:
                modIds.add(parsed[1])
        names = [mod.name for mod in self.model.all() if mod.nexus and mod.nexus.modId in modIds]
        if names:
            self.checkRequirements(names, afterInstall=True)

    def checkRequirements(self, names: list[str] | None = None, afterInstall: bool = False) -> None:
        '''Loads the Nexus requirements of the given mods (default: all linked ones) and lists those not met'''
        if self.requirementsWorker is not None and self.requirementsWorker.isRunning():
            if not afterInstall:
                self.output(translate("MainWindow", "Still checking requirements..."))
            return
        mods = [
            mod for mod in self.model.all() if mod.nexus and mod.nexus.modId and (names is None or mod.name in names)
        ]
        if not mods:
            if not afterInstall:
                self.output(translate("MainWindow", "No mods with a Nexus ID to check."))
            return
        modIds = sorted({mod.nexus.modId for mod in mods if mod.nexus})

        def work(progress):
            return NexusGraphQL().details(modIds)

        worker = NexusWorker(work, self)
        worker.succeeded.connect(lambda found: self.requirementsLoaded(found, names, afterInstall))
        worker.failed.connect(
            lambda message: self.output(translate("MainWindow", "Couldn't load Nexus requirements:") + f" {message}")
        )
        self.requirementsWorker = worker
        if not afterInstall:
            self.output(
                translate("MainWindow", "Checking the requirements of")
                + " "
                + plural(len(mods), "mod")
                + " "
                + translate("MainWindow", "on Nexus...")
            )
        worker.start()

    def requirementsLoaded(self, found: dict, names: list[str] | None, afterInstall: bool) -> None:
        self.detailsFetched(found, True)
        missing = missingRequirements(self.model, self.nexusDetails.get, data.getConfig().dlc or "", names)
        unmet = [entry for entry in missing if entry.state != EXTERNAL]
        if not missing:
            if not afterInstall:
                self.output(translate("MainWindow", "Every requirement listed on Nexus is installed."))
            return
        self.output(
            "> "
            + plural(len(unmet), translate("MainWindow", "requirement"))
            + " "
            + translate("MainWindow", "not met")
            + " (Nexus > Check Requirements...)"
        )
        for entry in unmet:
            self.output(f"  {entry.mod}: {entry.problem}")
        self.raiseWindow()
        heading = translate("MainWindow", "The mod you installed needs more.") if afterInstall else ""
        dialog = RequirementsDialog(self, missing, heading)
        if dialog.exec() and dialog.chosen is not None:
            requirement = dialog.chosen.requirement
            self.installFromNexus(ModDetails(requirement.modId, requirement.name))

    def checkRemastered(self) -> None:
        '''Lists mods not updated on Nexus since Remastered came out, with other versions that were'''
        if self.detailsWorker is not None and self.detailsWorker.isRunning():
            self.output(translate("MainWindow", "Still loading Nexus details..."))
            return
        mods = {mod.nexus.modId: mod for mod in self.model.all() if mod.nexus and mod.nexus.modId}
        if not mods:
            self.output(translate("MainWindow", "No mods with a Nexus ID to check."))
            return

        def work(progress):
            graphql = NexusGraphQL()
            found = graphql.details(list(mods))
            outdated = [details for details in found.values() if not details.updatedForRemastered]
            suggestions = {}
            for index, details in enumerate(outdated):
                suggestions[details.modId] = remasteredSuggestions(graphql, details.name, details.modId)
                progress(index + 1, len(outdated))
            return found, suggestions

        worker = NexusWorker(work, self)
        worker.progressed.connect(lambda current, total: self.setProgress(100 * current / total if total else 0))
        worker.succeeded.connect(lambda result: self.remasteredChecked(mods, *result))
        worker.failed.connect(lambda message: self.output(message))
        worker.finished.connect(lambda: self.setProgress(0))
        self.detailsWorker = worker
        self.output(
            translate("MainWindow", "Checking")
            + " "
            + plural(len(mods), "mod")
            + " "
            + translate("MainWindow", "for Remastered...")
        )
        worker.start()

    def remasteredChecked(self, mods: dict, found: dict, suggestions: dict) -> None:
        self.detailsFetched(found, True)
        issues = []
        for modId, details in sorted(found.items(), key=lambda item: item[1].name.lower()):
            if details.updatedForRemastered:
                continue
            mod = mods.get(modId)
            updated = time.strftime("%Y-%m-%d", time.gmtime(details.updated))
            others = suggestions.get(modId) or []
            detail = translate("MainWindow", "Last updated on Nexus") + f" {updated}."
            if others:
                detail += (
                    " "
                    + translate("MainWindow", "Possible Remastered versions:")
                    + " "
                    + "; ".join(f"{other.name} ({other.modId})" for other in others)
                )
            else:
                detail += " " + translate("MainWindow", "No other version updated for Remastered was found.")
            issues.append(
                Issue(
                    WARNING,
                    mod.name if mod else details.name,
                    translate("MainWindow", "Not updated since Remastered"),
                    detail,
                    others[0].url if others else details.url,
                )
            )
        missing = sorted(set(mods) - set(found))
        for modId in missing:
            issues.append(
                Issue(
                    INFO,
                    mods[modId].name,
                    translate("MainWindow", "Not found on Nexus"),
                    translate("MainWindow", "The mod may be hidden or removed."),
                    mods[modId].nexus.url,
                )
            )
        outdated = len([issue for issue in issues if issue.severity == WARNING])
        self.output(
            "> "
            + f"{outdated} "
            + translate("MainWindow", "of")
            + " "
            + plural(len(mods), translate("MainWindow", "mod"))
            + " "
            + (translate("MainWindow", "hasn't") if outdated == 1 else translate("MainWindow", "haven't"))
            + " "
            + translate("MainWindow", "been updated since Remastered")
        )
        HealthDialog(self, issues, title=translate("MainWindow", "Remastered Versions")).exec()

    def importFromRmmr(self) -> None:
        '''Adds mods and Nexus IDs from Relapse's Mod Manager Redux, after showing what will change'''
        folder = QFileDialog.getExistingDirectory(
            self,
            translate("MainWindow", "Select the Relapse's Mod Manager Redux folder"),
            data.getConfig().rmmr,
        )
        if not folder:
            return
        folder = normalizePath(folder)
        if not isRmmrFolder(folder):
            self.output(translate("MainWindow", "No RMMR installed.xml and mod_metadata.json in") + f" {folder}")
            return
        data.getConfig().rmmr = folder
        try:
            plan = planImport(folder, self.model, [data.getConfig().archives])
        except RmmrError as error:
            self.output(str(error))
            return
        dialog = RmmrImportDialog(self, plan)
        if not dialog.exec():
            return
        added, linked = applyImport(plan, self.model)
        self.refreshList()
        self.reportDuplicates()
        self.refreshNexusDetails(silent=True, onlyMissing=True)
        self.output(
            "> "
            + translate("MainWindow", "Imported from RMMR:")
            + f" {added} "
            + translate("MainWindow", "added")
            + f", {linked} "
            + translate("MainWindow", "linked to Nexus")
        )

    def changeArchiveFolder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, translate("MainWindow", "Folder for downloaded mod archives"), data.getConfig().archives
        )
        if folder:
            data.getConfig().archives = normalizePath(folder)
            self.output(translate("MainWindow", "Downloads go to") + f" {data.getConfig().archives}")

    def changeBackupFolder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, translate("MainWindow", "Folder for mod backups"), data.getConfig().backups
        )
        if folder:
            data.getConfig().backups = normalizePath(folder)
            self.output(translate("MainWindow", "Backups go to") + f" {data.getConfig().backups}")

    def setSearchString(self, searchString):
        self.searchString = searchString
        self.refreshList(False)

    # Helpers

    @throttle(200)
    def refreshList(self, write=True):
        '''Refreshes mod list'''
        try:
            selected = self.getSelectedMods()
            self.treeWidget.clear()
            if data.getConfig().listsort == "custom" and self.treeWidget.sortColumn() != -1:
                self.treeWidget.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
            headers = self.groupHeaders()
            duplicates = duplicateEntries(self.model)
            moddata = []
            for mod in sorted(self.model.all(), key=listKey):
                if len(self.searchString) > 0:
                    if self.searchString.lower() not in mod.name.lower():
                        continue
                if not self.passesFilter(mod.enabled, self.updateChecks.get(mod.name)):
                    continue
                moddata += mod.files
                modsize = 0
                for modfile in mod.files:
                    mods_directory = data.getConfig().mods
                    if mods_directory is None:
                        raise ValueError("No game directory configured for mod files")
                    modsize += getSize(mods_directory + "/" + modfile)
                    modsize += getSize(mods_directory + "/~" + modfile)
                for dlcfile in mod.dlcs:
                    dlc_directory = data.getConfig().dlc
                    if dlc_directory is None:
                        raise ValueError("No game directory configured for DLC files")
                    modsize += getSize(dlc_directory + "/" + dlcfile)
                    modsize += getSize(dlc_directory + "/~" + dlcfile)
                userstr = translate("MainWindow", "No")
                if mod.usersettings:
                    userstr = translate("MainWindow", "Yes")
                row = self.addToList(
                    mod.enabled,
                    mod.name,
                    mod.priority,
                    len(mod.files),
                    len(mod.dlcs),
                    len(mod.menus),
                    len(mod.xmlkeys),
                    len(mod.hidden),
                    len(mod.inputsettings),
                    userstr,
                    modsize,
                    mod.date,
                    mod.nexus.version if mod.nexus else "",
                    statusText(self.updateChecks.get(mod.name)),
                    self.modCategory(mod),
                    parent=headers.get(mod.group if mod.group in headers else ""),
                    contents=contentsTags(mod),
                )
                row.setData(UPDATE_COLUMN, UPDATE_KIND_ROLE, updateKind(self.updateChecks.get(mod.name)))
                self.showPicture(row, mod)
                self.showEndorsement(row, mod)
                if mod.name in duplicates:
                    row.setForeground(1, QBrush(statusColor("warning")))
                    row.setToolTip(
                        1,
                        translate("MainWindow", "Installed twice?")
                        + "\n"
                        + "\n".join(f"{mod.name} {reason}" for reason in duplicates[mod.name]),
                    )
            self.addModioRows(headers.get(""))
            self.finishGroupHeaders(headers)
            self.showCounts()
            for item in selected:
                rows = self.treeWidget.findItems(item, Qt.MatchFlag.MatchEndsWith | Qt.MatchFlag.MatchRecursive, 1)
                if rows:
                    for row in rows:
                        row.setSelected(True)
            if write:
                self.refreshLoadOrder()
                self.model.write()
                self.refreshDownloads()
        except Exception as err:
            self.output(translate("MainWindow", "Couldn't refresh list: ") + f"{formatUserError(err)}")
            return err
        return None

    def passesFilter(self, enabled: bool, check: UpdateCheck | None) -> bool:
        '''Whether a mod shows under the chosen filter button (All, Enabled, Disabled or Updates)'''
        wanted = getattr(self, "listFilter", "all")
        if wanted == "enabled":
            return bool(enabled)
        if wanted == "disabled":
            return not enabled
        if wanted == "updates":
            return updateKind(check) == "update"
        return True

    def showCounts(self) -> None:
        '''Counts on the filter buttons, the Installed tab, the Update button and the status row'''
        mods = self.model.all()
        try:
            modios = installedModioMods(data.getConfig().settings)
        except Exception:
            modios = []
        enabled = sum(1 for mod in mods if mod.enabled) + sum(1 for modio in modios if modio.enabled)
        everything = len(mods) + len(modios)
        updates = sum(1 for mod in mods if updateKind(self.updateChecks.get(mod.name)) == "update")
        for name, label, count in (
            ("all", translate("MainWindow", "All"), everything),
            ("enabled", translate("MainWindow", "Enabled"), enabled),
            ("disabled", translate("MainWindow", "Disabled"), everything - enabled),
            ("updates", translate("MainWindow", "Updates"), updates),
        ):
            self.filterButtons[name].setText(f"{label}  {count}")
        index = self.tabs.indexOf(self.installedTab)
        self.tabs.setTabText(index, translate("MainWindow", "Installed") + f" ({len(mods)})")
        summary = plural(len(mods), translate("MainWindow", "mod")) + " · "
        summary += str(sum(1 for mod in mods if mod.enabled)) + " " + translate("MainWindow", "enabled")
        if modios:
            summary += f" · {len(modios)} " + translate("MainWindow", "from mod.io")
        self.summaryLabel.setText(summary)
        self.showPageTitle()
        if updates != self.updateCount:
            self.updateCount = updates
            self.applyIcons()

    @debounceGui(100)
    def refreshLoadOrder(self):
        '''Refreshes right panel list - load order'''
        try:
            selected = self.getSelectedFiles()
            self.loadOrder.clear()
            data.getConfig().readPriority()
            dirs = []
            for data_ in os.listdir(data.getConfig().mods):
                templist = []
                templist.append(data_)
                prt = data.getConfig().getPriority(data_)
                if prt:
                    temp = int(prt)
                else:
                    temp = 16777215
                templist.append(temp)
                dirs.append(templist)
            dirs = sorted(dirs, key=getKey)
            for directory in dirs:
                if isData(directory[0]):
                    if directory[1] == 16777215:
                        res = ""
                    else:
                        res = str(directory[1])
                    dirlist = [directory[0], res]
                    item = CustomTreeWidgetItem(dirlist)
                    item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
                    LoadOrderTree.draggable(
                        item,
                        not directory[0].startswith("~") and directory[0].casefold() != MERGED_FOLDER.casefold(),
                    )
                    self.loadOrder.addTopLevelItem(item)
            for item in selected:
                rows = self.loadOrder.findItems(item.replace("~", ""), Qt.MatchFlag.MatchEndsWith, 0)
                if rows:
                    for row in rows:
                        row.setSelected(True)
            # a mod just dragged to a new place stays selected there
            if self.loadOrderFocus:
                for row in self.loadOrder.findItems(self.loadOrderFocus, Qt.MatchFlag.MatchExactly, 0):
                    self.loadOrder.setCurrentItem(row)
                    self.loadOrder.scrollToItem(row)
                self.loadOrderFocus = ""
        except Exception as err:
            self.output(translate("MainWindow", "Couldn't read or refresh load order: ") + f"{formatUserError(err)}")
            return err
        return None

    def loadOrderMoved(self, folder: str, row: int) -> None:
        '''A mod was dragged to a new place in the load order: sets priorities so the game loads it there'''
        if folder.startswith("~"):
            return
        config = data.getConfig()
        rows = [self.loadOrder.topLevelItem(i).text(0) for i in range(self.loadOrder.topLevelItemCount())]
        others = [name for name in rows if name != folder]
        index = sum(1 for name in others[:row] if not name.startswith("~"))
        enabled = [name for name in rows if not name.startswith("~")]
        order = moveFolder(enabled, folder, index)
        current = {name: priorityNumber(config.getPriority(name)) for name in enabled}
        wanted = prioritiesFor(order, current, folder)
        changed = [name for name, value in wanted.items() if current.get(name) != value]
        for name in changed:
            config.setPriority(name, str(wanted[name]))
        if not changed:
            return
        config.write_priority()
        # the mod list shows each mod's priority too
        for mod in self.model.all():
            if mod.files and mod.files[0] in wanted:
                mod._priority = str(wanted[mod.files[0]])
        self.output(
            translate("MainWindow", "Load order:")
            + f" {folder} "
            + translate("MainWindow", "now loads")
            + f" {order.index(folder) + 1}. "
            + "("
            + plural(len(changed), translate("MainWindow", "priority"), translate("MainWindow", "priorities"))
            + " "
            + translate("MainWindow", "changed")
            + ")"
        )
        self.loadOrderFocus = folder
        self.refreshList()

    def setProgress(self, currentProgress):
        '''Sets the progress to currentProgress'''
        self.progressBar.setProperty("value", currentProgress)
        self.progressBar.setVisible(0 < currentProgress < 100)

    def addToList(
        self,
        on,
        name,
        prio,
        data_,
        dlc,
        menu,
        keys,
        hidden,
        inputkeys,
        settings,
        size,
        date,
        version="",
        update="",
        category="",
        parent=None,
        contents="",
    ):
        '''Adds mod data to the list, under the given group heading if there is one'''
        if data_ == 0:
            datastr = "-"
        else:
            datastr = str(data_)
        if dlc == 0:
            dlcstr = "-"
        else:
            dlcstr = str(dlc)
        if menu == 0:
            menustr = "-"
        else:
            menustr = str(menu)
        if keys == 0:
            keystr = "-"
        else:
            keystr = str(keys)
        if inputkeys == 0:
            inkeystr = "-"
        else:
            inkeystr = str(inputkeys)
        if hidden == 0:
            hiddenstr = "-"
        else:
            hiddenstr = str(hidden)
        size //= 1024
        if size // 1024 == 0:
            sizestr = str(size) + "KB"
        else:
            size /= 1024
            sizestr = f"{size:.1f}" + "MB"
        proplist = [
            "",
            str(name),
            str(prio),
            datastr,
            dlcstr,
            menustr,
            keystr,
            hiddenstr,
            inkeystr,
            str(settings),
            sizestr,
            str(date),
            str(version),
            str(update),
            str(category),
            "",
            str(contents),
        ]
        item = CustomTreeWidgetItem(proplist)
        item.setTextAlignment(12, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(13, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(3, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(4, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(5, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(6, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(7, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(8, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(9, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(10, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setTextAlignment(11, Qt.AlignmentFlag.AlignCenter)
        if "~" not in name:
            if on:
                item.setCheckState(0, Qt.CheckState.Checked)
            else:
                item.setCheckState(0, Qt.CheckState.Unchecked)
        if not on:
            muted = QBrush(statusColor("muted"))
            for column in range(1, ENDORSE_COLUMN):
                item.setForeground(column, muted)
        if parent is not None:
            parent.addChild(item)
        else:
            self.treeWidget.addTopLevelItem(item)
        return item

    def getSelectedMods(self):
        '''Returns list of mod names of the selected mods'''
        array = []
        getSelected = self.treeWidget.selectedItems()
        if getSelected:
            for selected in getSelected:
                if not isGroupItem(selected) and not isModioItem(selected):
                    array.append(selected.text(1))
        return array

    # Mods the game installed from mod.io: shown so the list is complete, but the game manages them

    def addModioRows(self, parent) -> None:
        for modio in installedModioMods(data.getConfig().settings):
            if self.searchString and self.searchString.lower() not in modio.label.lower():
                continue
            if not self.passesFilter(modio.enabled, None):
                continue
            row = self.addToList(
                modio.enabled,
                modio.label,
                "-",
                len(modio.folders),
                0,
                0,
                0,
                0,
                0,
                "-",
                modio.size,
                modio.date,
                modio.version,
                parent=parent,
            )
            self.showModio(row, modio)
            row.setData(1, SUBTITLE_ROLE, " · ".join(part for part in ("mod.io", modio.author, modio.version) if part))

    def showModio(self, row, modio: ModioMod) -> None:
        # marked first, so the changes below (each one signals itemChanged) are never taken for a mod being toggled
        row.setData(0, MODIO_ROLE, modio.modId)
        # the tick shows whether the game has it on, but only the game's mod.io menu can change that
        row.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        row.setData(1, Qt.ItemDataRole.UserRole, modio.url)
        row.setData(1, Qt.ItemDataRole.UserRole + 1, modio.path)
        font = row.font(1)
        font.setBold(True)
        row.setFont(1, font)
        for column in range(row.columnCount()):
            row.setForeground(column, QBrush(statusColor("modio")))
        lines = [modio.title]
        if modio.author:
            lines[0] += " " + translate("MainWindow", "by") + f" {modio.author}"
        if modio.summary:
            lines.append(modio.summary)
        state = translate("MainWindow", "on") if modio.enabled else translate("MainWindow", "off")
        if modio.priority is not None:
            state += f", {translate('MainWindow', 'mod.io order')} {modio.priority}"
        lines.append(translate("MainWindow", "Installed from mod.io in the game's own mod menu") + f" ({state}).")
        lines.append(translate("MainWindow", "Turn it on or off, update or remove it there."))
        if modio.folders:
            lines.append(translate("MainWindow", "Folders:") + " " + ", ".join(modio.folders))
        tooltip = "\n".join(lines)
        for column in range(row.columnCount()):
            row.setToolTip(column, tooltip)

    def fillModioMenu(self, menu: QMenu, item) -> None:
        url = item.data(1, Qt.ItemDataRole.UserRole)
        path = item.data(1, Qt.ItemDataRole.UserRole + 1)
        page = menu.addAction(translate("MainWindow", "Open mod.io Page"), lambda: openUrl(url))
        page.setEnabled(bool(url))
        folder = menu.addAction(translate("MainWindow", "Open Folder"), lambda: openFolder(path))
        folder.setEnabled(bool(path) and os.path.isdir(path))

    # Groups in the mod list

    def modItems(self) -> list:
        """Every mod row in the list, also those under group headings"""
        items = []
        for index in range(self.treeWidget.topLevelItemCount()):
            item = self.treeWidget.topLevelItem(index)
            if isGroupItem(item):
                items += [item.child(child) for child in range(item.childCount())]
            elif item is not None:
                items.append(item)
        return [item for item in items if not isModioItem(item)]

    def groupHeaders(self) -> dict:
        """Headings for the mod list's groups (and one for mods without a group), or none when no group exists"""
        order = orderedGroups(data.getConfig().groups, self.model.all())
        self.treeWidget.setRootIsDecorated(bool(order))
        headers = {}
        if not order:
            return headers
        for rank, name in enumerate(order + [""]):
            header = GroupItem(name, rank)
            self.treeWidget.addTopLevelItem(header)
            header.setFirstColumnSpanned(True)
            headers[name] = header
        return headers

    def finishGroupHeaders(self, headers: dict) -> None:
        collapsed = set(data.getConfig().collapsedgroups)
        self.treeWidget.blockSignals(True)
        try:
            for name, header in headers.items():
                count = header.childCount()
                label = name or translate("MainWindow", "Other mods")
                header.setText(0, f"{label}  ({count})")
                header.setData(0, GROUP_COUNT_ROLE, count)
                header.setToolTip(0, translate("MainWindow", "Right-click to rename, move or remove the separator"))
                # an empty group stays visible so mods can be put in it, unless a search is hiding things
                header.setHidden(count == 0 and (not name or bool(self.searchString) or self.listFilter != "all"))
                header.setExpanded(name not in collapsed)
        finally:
            self.treeWidget.blockSignals(False)

    def groupFolded(self, item, folded: bool) -> None:
        if not isGroupItem(item):
            return
        name = item.data(0, GROUP_ROLE)
        collapsed = set(data.getConfig().collapsedgroups)
        if folded:
            collapsed.add(name)
        else:
            collapsed.discard(name)
        data.getConfig().collapsedgroups = sorted(collapsed)

    def moveToGroupMenu(self, parent) -> QMenu:
        """The "Group" submenu of the mod list's right-click menu"""
        menu = QMenu(translate("MainWindow", "Separator"), parent)
        selected = [self.model.modList[name] for name in self.getSelectedMods() if name in self.model.modList]
        current = {mod.group for mod in selected}
        for name in orderedGroups(data.getConfig().groups, self.model.all()):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(current == {name})
            action.setEnabled(bool(selected))
            action.triggered.connect(lambda _=False, group=name: self.setModsGroup(group))
        if menu.actions():
            menu.addSeparator()
        menu.addAction(translate("MainWindow", "New Separator..."), self.newGroup)
        remove = menu.addAction(translate("MainWindow", "Remove from Separator"), lambda: self.setModsGroup(""))
        remove.setEnabled(any(mod.group for mod in selected))
        return menu

    def fillGroupMenu(self, menu: QMenu, name: str) -> None:
        """Right-click menu of a group heading"""
        if not name:
            menu.addAction(translate("MainWindow", "Add Separator"), self.addSeparator)
            menu.addAction(translate("MainWindow", "Sort by My Order"), self.sortByMyOrder)
            self.addFoldActions(menu)
            return
        order = orderedGroups(data.getConfig().groups, self.model.all())
        menu.addAction(translate("MainWindow", "Rename Separator..."), lambda: self.renameModGroup(name))
        up = menu.addAction(translate("MainWindow", "Move Separator Up"), lambda: self.moveModGroup(name, -1))
        down = menu.addAction(translate("MainWindow", "Move Separator Down"), lambda: self.moveModGroup(name, 1))
        up.setEnabled(order.index(name) > 0)
        down.setEnabled(order.index(name) < len(order) - 1)
        menu.addSeparator()
        menu.addAction(translate("MainWindow", "Add Separator"), self.addSeparator)
        menu.addAction(translate("MainWindow", "Sort by My Order"), self.sortByMyOrder)
        self.addFoldActions(menu)
        menu.addAction(translate("MainWindow", "Remove Separator (keeps its mods)"), lambda: self.removeModGroup(name))

    def askGroupName(self, title: str, current: str = "") -> str:
        name, ok = QInputDialog.getText(self, title, translate("MainWindow", "Separator name:"), text=current)
        return cleanName(name) if ok else ""

    def saveGroups(self, order: list[str]) -> None:
        data.getConfig().groups = order
        self.model.write()
        self.refreshList()

    def setModsGroup(self, name: str) -> None:
        """Puts the selected mods in the named group ("" takes them out of their group)"""
        selected = [self.model.modList[n] for n in self.getSelectedMods() if n in self.model.modList]
        for mod in selected:
            if mod.group != name:
                mod.listorder = 0
            mod.group = name
        order = orderedGroups(data.getConfig().groups, self.model.all())
        self.saveGroups(addGroup(order, name) if name else order)

    def modsMoved(self, names: list[str], group: str, position: int) -> None:
        """Mods were dragged to a place in the list: under a separator ("" is Other mods), at position among the
        mods already there. Only the list's own order changes; the game's load order stays as it is."""
        names = [name for name in names if name in self.model.modList]
        if not names:
            return
        order = arrange(self.treeWidget.namesIn(group), names, position)
        for index, name in enumerate(order):
            mod = self.model.modList.get(name)
            if mod is not None:
                mod.group = group
                mod.listorder = index + 1
        data.getConfig().listsort = "custom"
        self.saveGroups(orderedGroups(data.getConfig().groups, self.model.all()))

    def addFoldActions(self, menu: QMenu) -> None:
        """Collapse All / Expand All Separators, while the list has separators"""
        if not self.treeWidget.headings():
            return
        menu.addSeparator()
        menu.addAction(translate("MainWindow", "Collapse All Separators"), lambda: self.setAllFolded(True))
        menu.addAction(translate("MainWindow", "Expand All Separators"), lambda: self.setAllFolded(False))

    def setAllFolded(self, folded: bool) -> None:
        """Folds (or unfolds) every separator in the mod list, and remembers it"""
        headings = self.treeWidget.headings()
        self.treeWidget.blockSignals(True)
        try:
            for heading in headings:
                heading.setExpanded(not folded)
        finally:
            self.treeWidget.blockSignals(False)
        data.getConfig().collapsedgroups = [heading.data(0, GROUP_ROLE) for heading in headings] if folded else []

    def sortByMyOrder(self) -> None:
        """Shows the mod list in the order the mods were dragged into, instead of sorted by a column"""
        data.getConfig().listsort = "custom"
        self.refreshList()

    def listSortChanged(self, column: int, _order) -> None:
        data.getConfig().listsort = "custom" if column < 0 else ""

    def separatorsMoved(self, names: list[str], position: int) -> None:
        """Separators were dragged to a new place among the others (-1: last, just above Other mods)"""
        order = orderedGroups(data.getConfig().groups, self.model.all())
        names = [name for name in order if name in names]
        if not names:
            return
        arranged = arrange(order, names, position)
        if arranged != order:
            self.saveGroups(arranged)

    def addSeparator(self) -> None:
        """Adds an empty separator with a plain name; it can be renamed from its right-click menu"""
        order = orderedGroups(data.getConfig().groups, self.model.all())
        self.saveGroups(addGroup(order, uniqueName(translate("MainWindow", "Separator"), order)))

    def newGroup(self) -> None:
        name = self.askGroupName(translate("MainWindow", "New Separator"))
        if not name:
            return
        data.getConfig().groups = addGroup(orderedGroups(data.getConfig().groups, self.model.all()), name)
        if self.getSelectedMods():
            self.setModsGroup(name)
        else:
            self.refreshList()

    def renameModGroup(self, old: str) -> None:
        new = self.askGroupName(translate("MainWindow", "Rename Separator"), old)
        if not new or new == old:
            return
        order = renameGroup(self.model.all(), orderedGroups(data.getConfig().groups, self.model.all()), old, new)
        collapsed = set(data.getConfig().collapsedgroups)
        if old in collapsed:
            collapsed.discard(old)
            collapsed.add(new)
            data.getConfig().collapsedgroups = sorted(collapsed)
        self.saveGroups(order)

    def moveModGroup(self, name: str, offset: int) -> None:
        self.saveGroups(moveGroup(orderedGroups(data.getConfig().groups, self.model.all()), name, offset))

    def removeModGroup(self, name: str) -> None:
        order = removeGroup(self.model.all(), orderedGroups(data.getConfig().groups, self.model.all()), name)
        self.output(
            translate("MainWindow", "Removed the separator")
            + f" {name}; "
            + translate("MainWindow", "its mods are now under Other mods")
        )
        self.saveGroups(order)

    def showModInLoadOrder(self) -> None:
        '''Selects the selected mods' folders in the load order and scrolls to the first'''
        folders = set()
        for name in self.getSelectedMods():
            mod = self.model.modList.get(name)
            if mod is not None:
                folders.update(folder.lstrip("~").casefold() for folder in mod.files)
        if not folders:
            return
        first = None
        self.loadOrder.clearSelection()
        for index in range(self.loadOrder.topLevelItemCount()):
            item = self.loadOrder.topLevelItem(index)
            if item.text(0).lstrip("~").casefold() in folders:
                item.setSelected(True)
                first = first or item
        if first is not None:
            self.loadOrder.scrollToItem(first)

    def getSelectedFiles(self):
        array = []
        getSelected = self.loadOrder.selectedItems()
        if getSelected:
            for selected in getSelected:
                baseNode = selected
                array.append(baseNode.text(0))
        return array

    def makeTempAction(self, action):
        '''Temp function for bypassing actions with same names problem'''
        temp = QAction(action)
        temp.setText(action.text())
        temp.setIcon(action.icon())
        temp.triggered.connect(lambda: self.removeFromToolbar(action))
        return temp

    def makeLangAction(self, ts):
        name, _ = path.splitext(ts)
        action = QAction()
        action.setText(name)
        action.setToolTip(name)
        action.setCheckable(True)
        action.setChecked(False)
        action.triggered.connect(lambda: self.changeLanguage(ts))
        return action

    @throttle(2000)
    def alertRunScriptMerger(self):
        '''Asks to run Script Merger when scripts need merging (if alerts are on), at most every 2 seconds'''
        self.promptScriptMerger()

    def promptScriptMerger(self) -> None:
        if data.getConfig().allowpopups != "1":
            return
        report = self.scriptConflictReport()
        if report is None:
            return
        pending = report.pending
        if not pending:
            if report.conflicts:
                self.output(translate("MainWindow", "Script conflicts are all merged."))
            return
        found = bool(data.getConfig().scriptmerger)
        if MessageAlertScriptConflicts(pending, found) == QMessageBox.StandardButton.Yes:
            self.runScriptMerger()

    def scriptConflictReport(self) -> ConflictReport | None:
        mods = data.getConfig().mods
        if not mods:
            return None
        scriptmerger = data.getConfig().scriptmerger
        try:
            return analyse(mods, path.dirname(scriptmerger) if scriptmerger else '')
        except OSError as err:
            self.output(translate("MainWindow", "Couldn't check script conflicts:") + f" {err}")
            return None

    def fileConflicts(self) -> ConflictScan | None:
        config = data.getConfig()
        if not config.mods:
            return None
        try:
            return findConflicts(config.mods, loadOrder(config.mods, config.getPriority))
        except OSError as err:
            self.output(translate("MainWindow", "Couldn't check file conflicts:") + f" {err}")
            return None

    def showFileConflicts(self) -> None:
        scan = self.fileConflicts()
        if scan is None:
            return
        names = {}
        for mod in self.model.all():
            for folder in mod.files:
                names[folder.lstrip("~").casefold()] = mod.name
        FileConflictsDialog(self, scan, names).exec()

    def detectScriptMerger(self) -> bool:
        '''Looks for Script Merger when no path is set'''
        config = data.getConfig()
        if config.scriptmerger:
            return True
        found = findScriptMerger(config.game or '', config.archives, config.rmmr)
        if found:
            config.scriptmerger = found
            self.output(translate("MainWindow", "Found Script Merger:") + f" {found}")
        return bool(found)

    def showScriptConflicts(self) -> None:
        report = self.scriptConflictReport()
        if report is None:
            return
        scriptmerger = data.getConfig().scriptmerger
        dialog = ScriptConflictsDialog(self, report, scriptmerger, installedVersion(scriptmerger))
        if dialog.exec():
            self.runScriptMerger()

    def healthIssues(self) -> list:
        try:
            return checkHealth(self.model, data.getConfig(), self.scriptConflictReport(), self.nexusDetails.get)
        except OSError as err:
            self.output(translate("MainWindow", "Couldn't run the health check:") + f" {err}")
            return []

    def showHealthCheck(self) -> None:
        HealthDialog(self, self.healthIssues(), self.healthIssues, fixed=self.showPriorities).exec()
        self.showHealth([issue for issue in self.healthIssues() if issue.severity in (ERROR, WARNING)])

    def showHealth(self, issues: list) -> None:
        '''The number of problems and warnings on the Health Check button and in the status row'''
        self.healthCount = len(issues)
        self.healthChip.setText(
            plural(
                len(issues),
                translate("MainWindow", "problem or warning"),
                translate("MainWindow", "problems or warnings"),
            )
        )
        self.healthChip.setToolTip(translate("MainWindow", "Open the health check (F9)"))
        self.healthChip.setVisible(bool(issues))
        self.rail.setBadge("health", len(issues), statusColor("warning"))
        self.applyIcons()

    def modVersions(self) -> dict[str, tuple[int, str]]:
        '''Which entry and install time each mod has, to tell afterwards which ones were installed'''
        return {mod.name: (id(mod), mod.date) for mod in self.model.all()}

    def checkSharedutilsAfterInstall(self, before: dict[str, tuple[int, str]]) -> None:
        '''After installing or updating mods, warns if Remastered can't compile the ones built on Sharedutils'''
        installed = {name for name, version in self.modVersions().items() if before.get(name) != version}
        if not installed:
            return
        try:
            issues = sharedutilsIssues(self.model, data.getConfig(), installed)
        except OSError as err:
            self.output(translate("MainWindow", "Couldn't check the mods built on Sharedutils:") + f" {err}")
            return
        if not issues:
            return
        self.output("> " + translate("MainWindow", "Scripts may not compile (Mods > Health Check...):"))
        for issue in issues:
            self.output(f"  {issue.mod}: {issue.problem}")
        self.raiseWindow()
        HealthDialog(
            self,
            issues,
            lambda: sharedutilsIssues(self.model, data.getConfig()),
            translate("MainWindow", "Sharedutils Load Order"),
            fixed=self.showPriorities,
        ).exec()

    def reportHealth(self) -> None:
        '''Mentions problems at startup, without opening anything'''
        issues = [issue for issue in self.healthIssues() if issue.severity in (ERROR, WARNING)]
        self.showHealth(issues)
        if issues:
            self.output(
                translate("MainWindow", "Health check:")
                + " "
                + plural(
                    len(issues),
                    translate("MainWindow", "problem or warning"),
                    translate("MainWindow", "problems or warnings"),
                )
                + " "
                + translate("MainWindow", "(Health, on the left)")
            )

    def changeTheme(self, theme):
        data.getConfig().theme = theme
        if theme == "Dark":
            data.getApp().setPalette(get_dark_palette())
        elif theme == "Light":
            data.getApp().setPalette(get_light_palette())
        else:
            data.getApp().setPalette(get_system_palette())
        data.getApp().setStyleSheet(styleSheet(theme))
        data.getConfig().write_config()
        # icons and status colours are drawn in the theme's colours, so draw them again
        self.configureToolbar()
        self.searchAction.setIcon(icon("search"))
        self.forkLabel.setStyleSheet(f"color: {statusColor('muted').name()};")
        self.rail.applyIcons()
        self.refreshList()

    def changeAccent(self, name: str) -> None:
        '''Red or ice blue for Play, ticks, the selection and the active tab'''
        setAccent(name)
        data.getConfig().set("SETTINGS", "accent", accentName())
        self.changeTheme(data.getConfig().theme)

    def changeTicks(self, name: str) -> None:
        '''Tick boxes or on/off switches; the style measures them, so it is set again'''
        setTickStyle(name)
        data.getConfig().set("SETTINGS", "ticks", tickStyle())
        app = data.getApp()
        app.setStyle(ManagerStyle())
        self.changeTheme(data.getConfig().theme)
        self.fitTickColumn()

    def fitTickColumn(self) -> None:
        '''The Enabled column wide enough for a switch in a row under a separator (indented twice)'''
        tick = ManagerStyle.SWITCH_WIDTH if tickStyle() == "switch" else 16
        wanted = self.treeWidget.indentation() * 2 + tick + 14
        if self.treeWidget.header().sectionSize(0) < wanted:
            self.treeWidget.header().resizeSection(0, wanted)

    def changeRowSize(self, name: str) -> None:
        '''Compact rows, or taller ones with each mod's picture and a second line'''
        data.getConfig().set("WINDOW", "rowsize", name)
        self.applyRowSize()
        self.refreshList(False)

    def applyRowSize(self) -> None:
        comfortable = data.getConfig().get("WINDOW", "rowsize") == "comfortable"
        self.listDelegate.setComfortable(comfortable)
        # separators stay short when the rows are tall
        self.treeWidget.setUniformRowHeights(not comfortable)
        self.treeWidget.doItemsLayout()

    def checkTheme(self):
        theme = data.getConfig().theme
        for action in self.menuSelect_Theme.actions():
            if action.text() == theme:
                action.setChecked(True)
                break
        chosen = {
            "accent": accentName(),
            "ticks": tickStyle(),
            "rowsize": data.getConfig().get("WINDOW", "rowsize") or "compact",
        }
        for key, group in self.appearanceGroups.items():
            for action in group.actions():
                action.setChecked(action.data() == chosen[key])

    def createActions(self):
        '''Create all action objects'''
        self.actionInstall_Mods = QAction(self.mainWindow)
        self.actionInstall_Mods.setIconVisibleInMenu(False)
        self.actionInstall_Mods.setObjectName("actionInstall_Mods")
        self.actionInstall_Mods.setIconText(translate("MainWindow", "Add"))

        self.actionRestoreColumns = QAction(self.mainWindow)
        self.actionRestoreColumns.setIconVisibleInMenu(False)
        self.actionRestoreColumns.setObjectName("actionRestoreColumns")

        self.actionUninstall_Mods = QAction(self.mainWindow)
        self.actionUninstall_Mods.setIconVisibleInMenu(False)
        self.actionUninstall_Mods.setObjectName("actionUninstall_Mods")
        self.actionUninstall_Mods.setIconText(translate("MainWindow", "Remove"))

        self.actionEnable_Disable_Mods = QAction(self.mainWindow)
        self.actionEnable_Disable_Mods.setIconVisibleInMenu(False)
        self.actionEnable_Disable_Mods.setObjectName("actionEnable_Disable_Mods")
        self.actionEnable_Disable_Mods.setIconText(translate("MainWindow", "Toggle"))

        self.actionReinstall_Mods = QAction(self.mainWindow)
        self.actionReinstall_Mods.setObjectName("actionReinstall_Mods")

        self.actionReinstallFiles = QAction(self.mainWindow)
        self.actionReinstallFiles.setObjectName("actionReinstallFiles")

        self.actionRefresh_Mod_List = QAction(self.mainWindow)
        self.actionRefresh_Mod_List.setObjectName("actionRefresh_Mod_List")

        self.actionRefresh_Load_Order = QAction(self.mainWindow)
        self.actionRefresh_Load_Order.setObjectName("actionRefresh_Load_Order")

        self.actionScriptConflicts = QAction(self.mainWindow)
        self.actionScriptConflicts.setObjectName("actionScriptConflicts")

        self.actionFileConflicts = QAction(self.mainWindow)
        self.actionFileConflicts.setObjectName("actionFileConflicts")

        self.actionGameSettings = QAction(self.mainWindow)
        self.actionGameSettings.setObjectName("actionGameSettings")

        self.actionReShade = QAction(self.mainWindow)
        self.actionReShade.setObjectName("actionReShade")

        self.actionSetup = QAction(self.mainWindow)
        self.actionSetup.setObjectName("actionSetup")

        self.actionHealthCheck = QAction(self.mainWindow)
        self.actionHealthCheck.setObjectName("actionHealthCheck")

        self.actionSelect_All_Mods = QAction(self.mainWindow)
        self.actionSelect_All_Mods.setObjectName("actionSelect_All_Mods")

        self.actionEnableAll = QAction(self.mainWindow)
        self.actionEnableAll.setObjectName("actionEnableAll")

        self.actionDisableAll = QAction(self.mainWindow)
        self.actionDisableAll.setObjectName("actionDisableAll")

        self.actionSetPriority = QAction(self.mainWindow)
        self.actionSetPriority.setObjectName("actionSetPriority")

        self.actionUnsetPriority = QAction(self.mainWindow)
        self.actionUnsetPriority.setObjectName("actionUnsetPriority")

        self.actionRun_The_Game = QAction(self.mainWindow)
        self.actionRun_The_Game.setObjectName("actionRun_The_Game")

        self.actionRun_Script_Merger = QAction(self.mainWindow)
        self.actionRun_Script_Merger.setObjectName("actionRun_Script_Merger")

        self.actionAbout = QAction(self.mainWindow)
        self.actionAbout.setObjectName("actionAbout")

        self.actionManagerUpdate = QAction(self.mainWindow)
        self.actionManagerUpdate.setObjectName("actionManagerUpdate")

        self.actionRename = QAction(self.mainWindow)
        self.actionRename.setObjectName("actionRename")

        self.actionDetails = QAction(self.mainWindow)
        self.actionDetails.setObjectName("actionDetails")

        self.actionOpenFolder = QAction(self.mainWindow)
        self.actionOpenFolder.setObjectName("actionOpenFolder")

        self.actionIncreasePriority = QAction(self.mainWindow)
        self.actionIncreasePriority.setObjectName("actionIncreasePriority")

        self.actionDecreasePriority = QAction(self.mainWindow)
        self.actionDecreasePriority.setObjectName("actionDecreasePriority")

        self.actionIncreaseLoadOrderPriority = QAction(self.mainWindow)
        self.actionIncreaseLoadOrderPriority.setObjectName("actionIncreaseLoadOrderPriority")

        self.actionDecreaseLoadOrderPriority = QAction(self.mainWindow)
        self.actionDecreaseLoadOrderPriority.setObjectName("actionDecreaseLoadOrderPriority")

        self.actionMain_Web_Page = QAction(self.mainWindow)
        self.actionMain_Web_Page.setObjectName("actionMain_Web_Page")

        self.actionGitHub = QAction(self.mainWindow)
        self.actionGitHub.setObjectName("acitionGitHub")

        self.actionAlert_to_run_Script_Merger = QAction(self.mainWindow)
        self.actionAlert_to_run_Script_Merger.setCheckable(True)
        self.actionAlert_to_run_Script_Merger.setObjectName("actionAlert_to_run_Script_Merger")

        self.actionUseNativeFileDialogs = QAction(self.mainWindow)
        self.actionUseNativeFileDialogs.setCheckable(True)
        self.actionUseNativeFileDialogs.setObjectName("actionUseNativeFileDialogs")

        self.actionChange_Game_Path = QAction(self.mainWindow)
        self.actionChange_Game_Path.setObjectName("actionChange_Game_Path")

        # Add the missing action
        self.actionChange_Script_Merger_Path = QAction(self.mainWindow)
        self.actionChange_Script_Merger_Path.setObjectName("actionChange_Script_Merger_Path")

        self.actionClearOutput = QAction(self.mainWindow)
        self.actionClearOutput.setObjectName("actionClearOutput")

        # Nexus actions
        self.actionCheckUpdates = QAction(self.mainWindow)
        self.actionCheckUpdates.setIconVisibleInMenu(False)
        self.actionCheckUpdates.setObjectName("actionCheckUpdates")

        self.actionUpdateMods = QAction(self.mainWindow)
        self.actionUpdateMods.setObjectName("actionUpdateMods")

        self.actionUpdateScriptMerger = QAction(self.mainWindow)
        self.actionUpdateScriptMerger.setObjectName("actionUpdateScriptMerger")

        self.actionSetNexusMod = QAction(self.mainWindow)
        self.actionSetNexusMod.setObjectName("actionSetNexusMod")

        self.actionChooseFile = QAction(self.mainWindow)
        self.actionChooseFile.setObjectName("actionChooseFile")

        self.actionOpenNexusPage = QAction(self.mainWindow)
        self.actionOpenNexusPage.setObjectName("actionOpenNexusPage")

        self.actionDownloadHistory = QAction(self.mainWindow)
        self.actionDownloadHistory.setObjectName("actionDownloadHistory")

        self.actionCleanUpArchives = QAction(self.mainWindow)
        self.actionCleanUpArchives.setObjectName("actionCleanUpArchives")

        self.actionRestoreBackup = QAction(self.mainWindow)
        self.actionRestoreBackup.setObjectName("actionRestoreBackup")

        self.actionImportRmmr = QAction(self.mainWindow)
        self.actionImportRmmr.setObjectName("actionImportRmmr")

        self.actionRefreshDetails = QAction(self.mainWindow)
        self.actionRefreshDetails.setObjectName("actionRefreshDetails")

        self.actionCheckRemastered = QAction(self.mainWindow)
        self.actionCheckRemastered.setObjectName("actionCheckRemastered")

        self.actionCheckRequirements = QAction(self.mainWindow)
        self.actionCheckRequirements.setObjectName("actionCheckRequirements")

        self.actionKeepArchives = QAction(self.mainWindow)
        self.actionKeepArchives.setCheckable(True)
        self.actionKeepArchives.setObjectName("actionKeepArchives")

        self.actionBackupSaves = QAction(self.mainWindow)
        self.actionBackupSaves.setCheckable(True)
        self.actionBackupSaves.setObjectName("actionBackupSaves")

        self.actionSaveBackups = QAction(self.mainWindow)
        self.actionSaveBackups.setObjectName("actionSaveBackups")

        self.actionHandleNxm = QAction(self.mainWindow)
        self.actionHandleNxm.setCheckable(True)
        self.actionHandleNxm.setObjectName("actionHandleNxm")

        self.actionDefaultNxm = QAction(self.mainWindow)
        self.actionDefaultNxm.setObjectName("actionDefaultNxm")

        self.actionSetApiKey = QAction(self.mainWindow)
        self.actionSetApiKey.setObjectName("actionSetApiKey")

        self.actionChangeArchiveFolder = QAction(self.mainWindow)
        self.actionChangeArchiveFolder.setObjectName("actionChangeArchiveFolder")

        self.actionChangeBackupFolder = QAction(self.mainWindow)
        self.actionChangeBackupFolder.setObjectName("actionChangeBackupFolder")

        # Create language actions
        self.languageActionGroup = QActionGroup(self.mainWindow)
        for lang in os.listdir(getProgramRootFolder() + "/translations/"):
            temp = self.makeLangAction(lang)
            self.languageActionGroup.addAction(temp)
            self.menuSelect_Language.addAction(temp)

        # Create theme actions
        self.themeActionGroup = QActionGroup(self.mainWindow)
        for theme in ["Follow System", "Light", "Dark"]:
            action = QAction(theme, self.mainWindow, checkable=True)
            action.triggered.connect(lambda _, t=theme: self.changeTheme(t))
            self.themeActionGroup.addAction(action)
            self.menuSelect_Theme.addAction(action)

        # appearance choices: (menu, config key, [(value, label)], what to do)
        self.appearanceGroups: dict[str, QActionGroup] = {}
        for menu, key, choices, apply in (
            (
                self.menuAccent,
                "accent",
                [("red", translate("MainWindow", "Red")), ("blue", translate("MainWindow", "Ice Blue"))],
                self.changeAccent,
            ),
            (
                self.menuTicks,
                "ticks",
                [("box", translate("MainWindow", "Tick Boxes")), ("switch", translate("MainWindow", "Switches"))],
                self.changeTicks,
            ),
            (
                self.menuRowSize,
                "rowsize",
                [
                    ("compact", translate("MainWindow", "Compact")),
                    ("comfortable", translate("MainWindow", "Comfortable, with Pictures")),
                ],
                self.changeRowSize,
            ),
        ):
            group = QActionGroup(self.mainWindow)
            for value, label in choices:
                action = QAction(label, self.mainWindow, checkable=True)
                action.setData(value)
                action.triggered.connect(lambda _, v=value, a=apply: a(v))
                group.addAction(action)
                menu.addAction(action)
            self.appearanceGroups[key] = group

    def createMenus(self):
        '''Create menu objects'''

        self.menubar.clear()

        # Create main menus

        self.menuFile = QMenu(self.menubar)
        self.menuFile.setObjectName("menuFile")
        self.menuFile.setTitle(translate("MainWindow", "Mods"))

        self.menuEdit = QMenu(self.menubar)
        self.menuEdit.setObjectName("menuEdit")
        self.menuEdit.setTitle(translate("MainWindow", "Edit"))

        self.menuNexus = QMenu(self.menubar)
        self.menuNexus.setObjectName("menuNexus")
        self.menuNexus.setTitle(translate("MainWindow", "Nexus"))

        self.menuSettings = QMenu(self.menubar)
        self.menuSettings.setObjectName("menuSettings")
        self.menuSettings.setTitle(translate("MainWindow", "Settings"))

        self.menuHelp = QMenu(self.menubar)
        self.menuHelp.setObjectName("menuHelp")
        self.menuHelp.setTitle(translate("MainWindow", "Help"))

        # Create Settings submenus

        self.menuSelect_Theme = QMenu(self.menuSettings)
        self.menuSelect_Theme.setObjectName("menuSelect_Theme")
        self.menuSelect_Theme.setTitle(translate("MainWindow", "Select Theme"))

        self.menuAccent = QMenu(self.menuSettings)
        self.menuAccent.setObjectName("menuAccent")
        self.menuAccent.setTitle(translate("MainWindow", "Accent Colour"))

        self.menuTicks = QMenu(self.menuSettings)
        self.menuTicks.setObjectName("menuTicks")
        self.menuTicks.setTitle(translate("MainWindow", "Tick Style"))

        self.menuRowSize = QMenu(self.menuSettings)
        self.menuRowSize.setObjectName("menuRowSize")
        self.menuRowSize.setTitle(translate("MainWindow", "Mod List Rows"))

        self.menuSelect_Language = QMenu(self.menuSettings)
        self.menuSelect_Language.setObjectName("menuSelect_Language")
        self.menuSelect_Language.setTitle(translate("MainWindow", "Select Language"))

        self.menuConfigure_Settings = QMenu(self.menuSettings)
        self.menuConfigure_Settings.setObjectName("menuConfigure_Settings")
        self.menuConfigure_Settings.setTitle(translate("MainWindow", "Change Setting"))

        # Add main menus to menubar

        self.menubar.addAction(self.menuFile.menuAction())
        self.menubar.addAction(self.menuEdit.menuAction())
        self.menubar.addAction(self.menuNexus.menuAction())
        self.menubar.addAction(self.menuSettings.menuAction())
        self.menubar.addAction(self.menuHelp.menuAction())

    def populateMenu(self):
        '''Populate the menu and submenus'''

        self.menuFile.clear()
        self.menuEdit.clear()
        self.menuNexus.clear()
        self.menuSettings.clear()
        self.menuHelp.clear()
        self.menuConfigure_Settings.clear()

        # --- Build File/Mods menu ---
        self.menuFile.addAction(self.actionInstall_Mods)
        self.menuFile.addAction(self.actionUninstall_Mods)
        self.menuFile.addAction(self.actionEnable_Disable_Mods)
        self.menuFile.addAction(self.actionEnableAll)
        self.menuFile.addAction(self.actionDisableAll)
        self.menuFile.addSeparator()
        self.menuFile.addAction(self.actionOpenFolder)
        self.menuFile.addSeparator()
        self.menuFile.addAction(self.actionReinstallFiles)
        self.menuFile.addAction(self.actionReinstall_Mods)
        self.menuFile.addAction(self.actionRefresh_Mod_List)
        self.menuFile.addAction(self.actionRefresh_Load_Order)
        self.menuFile.addAction(self.actionScriptConflicts)
        self.menuFile.addAction(self.actionFileConflicts)
        self.menuFile.addAction(self.actionHealthCheck)
        self.menuFile.addAction(self.actionGameSettings)
        self.menuFile.addAction(self.actionReShade)
        self.menuFile.addAction(self.actionSelect_All_Mods)

        # --- Build Edit menu ---
        self.menuEdit.addAction(self.actionDetails)
        self.menuEdit.addAction(self.actionRename)
        self.menuEdit.addSeparator()
        self.menuEdit.addAction(self.actionSetPriority)
        self.menuEdit.addAction(self.actionUnsetPriority)
        self.menuEdit.addSeparator()
        self.menuEdit.addAction(self.actionIncreasePriority)
        self.menuEdit.addAction(self.actionDecreasePriority)
        self.menuEdit.addAction(self.actionIncreaseLoadOrderPriority)
        self.menuEdit.addAction(self.actionDecreaseLoadOrderPriority)

        # --- Build Nexus menu ---
        self.menuNexus.addAction(self.actionCheckUpdates)
        self.menuNexus.addAction(self.actionUpdateMods)
        self.menuNexus.addAction(self.actionUpdateScriptMerger)
        self.menuNexus.addSeparator()
        self.menuNexus.addAction(self.actionSetNexusMod)
        self.menuNexus.addAction(self.actionChooseFile)
        self.menuNexus.addAction(self.actionOpenNexusPage)
        self.menuNexus.addAction(self.actionDownloadHistory)
        self.menuNexus.addAction(self.actionCleanUpArchives)
        self.menuNexus.addAction(self.actionRestoreBackup)
        self.menuNexus.addAction(self.actionSaveBackups)
        self.menuNexus.addAction(self.actionImportRmmr)
        self.menuNexus.addSeparator()
        self.menuNexus.addAction(self.actionRefreshDetails)
        self.menuNexus.addAction(self.actionCheckRemastered)
        self.menuNexus.addAction(self.actionCheckRequirements)
        self.menuNexus.addSeparator()
        self.menuNexus.addAction(self.actionSetApiKey)
        self.menuNexus.addAction(self.actionChangeArchiveFolder)
        self.menuNexus.addAction(self.actionKeepArchives)
        self.menuNexus.addAction(self.actionHandleNxm)
        self.menuNexus.addAction(self.actionDefaultNxm)
        self.menuNexus.addAction(self.actionChangeBackupFolder)
        self.menuNexus.addAction(self.actionBackupSaves)

        # --- Build Help menu ---
        self.menuHelp.addAction(self.actionManagerUpdate)
        self.menuHelp.addAction(self.actionAbout)
        self.menuHelp.addAction(self.actionMain_Web_Page)
        self.menuHelp.addAction(self.actionGitHub)

        # --- Build Settings submenus ---

        # Configure Settings submenu
        self.menuConfigure_Settings.addAction(self.actionChange_Game_Path)
        self.menuConfigure_Settings.addAction(self.actionChange_Script_Merger_Path)
        self.menuConfigure_Settings.addSeparator()
        self.menuConfigure_Settings.addAction(self.actionRestoreColumns)
        self.menuConfigure_Settings.addSeparator()
        self.menuConfigure_Settings.addAction(self.actionAlert_to_run_Script_Merger)
        self.menuConfigure_Settings.addAction(self.actionUseNativeFileDialogs)

        # Add submenus to Settings menu
        self.menuSettings.addAction(self.actionSetup)
        self.menuSettings.addSeparator()
        self.menuSettings.addAction(self.menuSelect_Theme.menuAction())
        self.menuSettings.addAction(self.menuAccent.menuAction())
        self.menuSettings.addAction(self.menuTicks.menuAction())
        self.menuSettings.addAction(self.menuRowSize.menuAction())
        self.menuSettings.addAction(self.menuSelect_Language.menuAction())
        self.menuSettings.addAction(self.menuConfigure_Settings.menuAction())

    def translateUi(self):
        self.mainWindow.setWindowTitle(translate("MainWindow", TITLE) + f" ({forkLabel()})")

        self.treeWidget.headerItem().setText(0, translate("MainWindow", "Enabled"))
        self.treeWidget.headerItem().setText(1, translate("MainWindow", "Mod Name"))
        self.treeWidget.headerItem().setText(2, translate("MainWindow", "Priority"))
        self.treeWidget.headerItem().setText(3, translate("MainWindow", "Data"))
        self.treeWidget.headerItem().setText(4, translate("MainWindow", "DLC"))
        self.treeWidget.headerItem().setText(5, translate("MainWindow", "Menu"))
        self.treeWidget.headerItem().setText(6, translate("MainWindow", "Var"))
        self.treeWidget.headerItem().setText(7, translate("MainWindow", "Hidden"))
        self.treeWidget.headerItem().setText(8, translate("MainWindow", "Key"))
        self.treeWidget.headerItem().setText(9, translate("MainWindow", "Settings"))
        self.treeWidget.headerItem().setText(10, translate("MainWindow", "Size"))
        self.treeWidget.headerItem().setText(11, translate("MainWindow", "Date Installed"))
        self.treeWidget.headerItem().setText(12, translate("MainWindow", "Version"))
        self.treeWidget.headerItem().setText(13, translate("MainWindow", "Update"))
        self.treeWidget.headerItem().setText(14, translate("MainWindow", "Category"))
        self.treeWidget.headerItem().setText(CONTENTS_COLUMN, translate("MainWindow", "Contents"))
        self.treeWidget.headerItem().setToolTip(
            CONTENTS_COLUMN,
            translate(
                "MainWindow", "What the mod adds besides its mod folder: DLC, menus, key bindings, settings, bin files"
            ),
        )
        self.treeWidget.headerItem().setText(ENDORSE_COLUMN, "")
        self.treeWidget.headerItem().setIcon(ENDORSE_COLUMN, icon("thumb"))
        self.treeWidget.headerItem().setToolTip(
            ENDORSE_COLUMN, translate("MainWindow", "Endorsed on Nexus. Click a thumb to endorse the mod.")
        )

        self.loadOrder.headerItem().setText(0, translate("MainWindow", "Load Order"))
        self.loadOrder.headerItem().setToolTip(
            0,
            translate(
                "MainWindow",
                "The order the game loads mods in; the first one wins when mods change the same file. "
                "Drag a mod to move it.",
            ),
        )
        self.loadOrder.headerItem().setText(1, translate("MainWindow", "Priority"))

        self.textEdit.setPlaceholderText(translate("MainWindow", "Output"))
        self.textEdit.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

        self.scriptMergerButton.setText(translate("MainWindow", "Script Merger"))
        self.scriptMergerButton.setToolTip(translate("MainWindow", "Run Script Merger (Ctrl+S)"))
        self.checkButton.setText(translate("MainWindow", "Check for Updates"))
        self.checkButton.setToolTip(translate("MainWindow", "Check every mod with a Nexus ID for updates (F7)"))
        self.updateButton.setToolTip(
            translate("MainWindow", "Update the selected mods, or every mod with an update if none are selected")
        )
        self.addButton.setText(translate("MainWindow", "Add Mod"))
        self.addButton.setToolTip(translate("MainWindow", "Install mods from archives or folders (Ctrl+E)"))
        launchLabel = self.gameLaunchLabel()
        self.runGameButton.setText(translate("MainWindow", "Play"))
        self.runGameButton.setMode(launchLabel)
        self.runGameButton.setToolTip(translate("MainWindow", "Run the Game") + f" ({launchLabel}, Ctrl+R)")
        self.searchWidget.setPlaceholderText(translate("MainWindow", "Search mods"))
        self.logButton.setText(translate("MainWindow", "Log"))
        self.logButton.setToolTip(translate("MainWindow", "Show or hide everything the manager has reported"))
        self.toolBar.setWindowTitle(translate("MainWindow", "toolBar"))

        self.actionInstall_Mods.setText(translate("MainWindow", "Install Mods"))
        self.actionInstall_Mods.setToolTip(translate("MainWindow", "Install one or more Mods from folders or archives"))
        self.actionInstall_Mods.setShortcut("Ctrl+E")
        self.actionRestoreColumns.setText(translate("MainWindow", "Restore default column widths"))
        self.actionRestoreColumns.setToolTip(translate("MainWindow", "Restore default column widths"))
        self.actionUninstall_Mods.setText(translate("MainWindow", "Uninstall"))
        self.actionUninstall_Mods.setToolTip(translate("MainWindow", "Uninstall one or more selected Mods"))
        self.actionUninstall_Mods.setShortcut("Del")
        self.actionEnable_Disable_Mods.setText(translate("MainWindow", "Enable/Disable"))
        self.actionEnable_Disable_Mods.setToolTip(translate("MainWindow", "Enable or disable selected Mods"))
        self.actionEnable_Disable_Mods.setShortcut("Ctrl+Q")
        self.actionRefresh_Mod_List.setText(translate("MainWindow", "Refresh Mod List"))
        self.actionRefresh_Mod_List.setShortcut("F5")
        self.actionReinstallFiles.setText(translate("MainWindow", "Reinstall"))
        self.actionReinstallFiles.setToolTip(
            translate(
                "MainWindow",
                "Install the selected mods again from their archives, or download the same file from Nexus again",
            )
        )
        self.actionReinstall_Mods.setText(translate("MainWindow", "Restore Settings"))
        self.actionReinstall_Mods.setToolTip(
            translate("MainWindow", "Restore stored mod settings and key bindings to their defaults")
        )
        self.actionRefresh_Load_Order.setText(translate("MainWindow", "Refresh Load Order"))
        self.actionRefresh_Load_Order.setShortcut("F6")
        self.actionFileConflicts.setText(translate("MainWindow", "File Conflicts..."))
        self.actionFileConflicts.setToolTip(
            translate("MainWindow", "Which mod the game takes each file from when several mods pack the same file")
        )
        self.actionScriptConflicts.setText(translate("MainWindow", "Script Conflicts..."))
        self.actionScriptConflicts.setToolTip(
            translate("MainWindow", "Scripts changed by more than one mod, and whether they are merged")
        )
        self.actionScriptConflicts.setShortcut("F8")
        self.actionHealthCheck.setText(translate("MainWindow", "Health Check..."))
        self.actionGameSettings.setText(translate("MainWindow", "Game Settings..."))
        self.actionGameSettings.setToolTip(
            translate("MainWindow", "The game's settings and key bindings, with what your mods added")
        )
        self.actionReShade.setText(translate("MainWindow", "ReShade..."))
        self.actionSetup.setText(translate("MainWindow", "Setup..."))
        self.actionSetup.setToolTip(
            translate("MainWindow", "The game, Script Merger, the download folder and the Nexus API key")
        )
        self.actionReShade.setToolTip(
            translate("MainWindow", "Whether ReShade is installed, turn it off and on, and update it from reshade.me")
        )
        self.actionHealthCheck.setIconText(translate("MainWindow", "Health Check"))
        self.actionHealthCheck.setToolTip(translate("MainWindow", "Look for problems with the installed mods"))
        self.actionHealthCheck.setShortcut("F9")
        self.actionSelect_All_Mods.setText(translate("MainWindow", "Select All Mods"))
        self.actionSelect_All_Mods.setShortcut("Ctrl+A")
        self.actionEnableAll.setText(translate("MainWindow", "Enable All"))
        self.actionEnableAll.setToolTip(translate("MainWindow", "Enable every mod shown in the list"))
        self.actionDisableAll.setText(translate("MainWindow", "Disable All"))
        self.actionDisableAll.setToolTip(translate("MainWindow", "Disable every mod shown in the list"))
        self.actionRun_The_Game.setText(translate("MainWindow", "Run the Game"))
        self.actionRun_The_Game.setShortcut("Ctrl+R")
        self.actionRun_Script_Merger.setText(translate("MainWindow", "Run Script Merger"))
        self.actionRun_Script_Merger.setShortcut("Ctrl+S")
        self.actionRun_Script_Merger.setIconText(translate("MainWindow", "Script Merger"))
        self.actionRun_Script_Merger.setToolTip(translate("MainWindow", "Run Script Merger (Ctrl+S)"))
        self.actionAbout.setText(translate("MainWindow", "About"))
        self.actionManagerUpdate.setText(translate("MainWindow", "Update Manager..."))
        self.actionManagerUpdate.setToolTip(
            translate("MainWindow", "Checks the fork's GitHub releases for a newer version and installs it")
        )
        self.actionAbout.setShortcut("F1")
        self.actionMain_Web_Page.setText(translate("MainWindow", "Main Web Page"))
        self.actionGitHub.setText(translate("MainWindow", "GitHub"))
        self.actionMain_Web_Page.setShortcut("Ctrl+F1")
        self.actionGitHub.setShortcut("Ctrl+F2")
        self.actionAlert_to_run_Script_Merger.setText(translate("MainWindow", "Alert to run Script Merger"))
        self.actionUseNativeFileDialogs.setText(translate("MainWindow", "Use Native File Dialogs"))
        self.actionChange_Game_Path.setText(translate("MainWindow", "Change Game Path"))
        self.actionChange_Script_Merger_Path.setText(translate("MainWindow", "Change Script Merger Path"))
        self.actionClearOutput.setText(translate("MainWindow", "Clear Output"))
        self.actionRename.setText(translate("MainWindow", "Rename"))
        self.actionRename.setShortcut("F2")
        self.actionDetails.setShortcut("F3")
        self.actionDetails.setText(translate("MainWindow", "Details"))
        self.actionOpenFolder.setShortcut("Ctrl+L")
        self.actionOpenFolder.setText(translate("MainWindow", "Open Folder"))
        self.actionIncreasePriority.setShortcut("Ctrl+Up")
        self.actionIncreasePriority.setText(translate("MainWindow", "Increase Priority"))
        self.actionDecreasePriority.setShortcut("Ctrl+Down")
        self.actionDecreasePriority.setText(translate("MainWindow", "Decrease Priority"))
        self.actionIncreaseLoadOrderPriority.setShortcut("Ctrl+Shift+Up")
        self.actionIncreaseLoadOrderPriority.setText(translate("MainWindow", "Increase Load Order Priority"))
        self.actionDecreaseLoadOrderPriority.setShortcut("Ctrl+Shift+Down")
        self.actionDecreaseLoadOrderPriority.setText(translate("MainWindow", "Decrease Load Order Priority"))
        self.actionSetPriority.setShortcut("Ctrl+P")
        self.actionSetPriority.setText(translate("MainWindow", "Set Priority"))
        self.actionUnsetPriority.setShortcut("Ctrl+U")
        self.actionUnsetPriority.setText(translate("MainWindow", "Remove Priority"))
        self.actionCheckUpdates.setText(translate("MainWindow", "Check for Updates"))
        self.actionCheckUpdates.setToolTip(
            translate("MainWindow", "Check all mods with a Nexus ID for updates and show them in the Update column")
        )
        self.actionCheckUpdates.setShortcut("F7")
        self.actionCheckUpdates.setIconText(translate("MainWindow", "Check for Updates"))
        self.actionUpdateMods.setText(translate("MainWindow", "Update Mods..."))
        self.actionUpdateMods.setIconText(translate("MainWindow", "Update"))
        self.actionUpdateMods.setToolTip(
            translate("MainWindow", "Update the selected mods, or every mod with an update if none are selected")
        )
        self.actionUpdateMods.setShortcut("Ctrl+F7")
        self.actionUpdateScriptMerger.setText(translate("MainWindow", "Update Script Merger..."))
        self.actionUpdateScriptMerger.setToolTip(
            translate("MainWindow", "Check Nexus for a newer Script Merger and install it over the current one")
        )
        self.actionSetNexusMod.setText(translate("MainWindow", "Set Nexus Mod..."))
        self.actionChooseFile.setText(translate("MainWindow", "Choose Nexus File..."))
        self.actionChooseFile.setToolTip(
            translate("MainWindow", "Record which file of the Nexus page is installed, or switch to another file")
        )
        self.actionOpenNexusPage.setText(translate("MainWindow", "Open Nexus Page"))
        self.actionDownloadHistory.setText(translate("MainWindow", "Download History..."))
        self.actionDownloadHistory.setIconText(translate("MainWindow", "History"))
        self.actionSaveBackups.setText(translate("MainWindow", "Game Save Backups..."))
        self.actionSaveBackups.setToolTip(
            translate("MainWindow", "Copies of your newest saves, made before updates; back up or restore them")
        )
        self.actionBackupSaves.setText(translate("MainWindow", "Back Up Saves Before Updating"))
        self.actionBackupSaves.setToolTip(
            translate("MainWindow", "Copy your newest saves to the backup folder before updating mods")
        )
        self.actionCleanUpArchives.setText(translate("MainWindow", "Clean Up Archives..."))
        self.actionCleanUpArchives.setToolTip(
            translate("MainWindow", "Find old versions and other archives you may not need in the archive folder")
        )
        self.actionDownloadHistory.setToolTip(
            translate("MainWindow", "Files downloaded from Nexus, what became of them, and installing them again")
        )
        self.actionOpenNexusPage.setShortcut("Ctrl+N")
        self.actionRestoreBackup.setText(translate("MainWindow", "Restore Previous Version..."))
        self.actionImportRmmr.setText(translate("MainWindow", "Import from RMMR..."))
        self.actionRefreshDetails.setText(translate("MainWindow", "Refresh Nexus Details"))
        self.actionRefreshDetails.setToolTip(
            translate("MainWindow", "Load pictures, categories and summaries of linked mods")
        )
        self.actionCheckRequirements.setText(translate("MainWindow", "Check Requirements..."))
        self.actionCheckRequirements.setToolTip(
            translate("MainWindow", "Find mods, expansions and tools that the Nexus pages of your mods say they need")
        )
        self.actionCheckRemastered.setText(translate("MainWindow", "Check Remastered Versions..."))
        self.actionCheckRemastered.setToolTip(
            translate("MainWindow", "Find mods not updated since Remastered, and other versions that were")
        )
        self.actionImportRmmr.setToolTip(
            translate("MainWindow", "Add mods and Nexus IDs from Relapse's Mod Manager Redux")
        )
        self.actionSetApiKey.setText(translate("MainWindow", "Set API Key..."))
        self.actionChangeArchiveFolder.setText(translate("MainWindow", "Change Download Folder..."))
        self.actionKeepArchives.setText(translate("MainWindow", "Keep Copies of Installed Archives"))
        self.actionDefaultNxm.setText(translate("MainWindow", "Make W3MM the Default for Nexus Links"))
        self.actionDefaultNxm.setToolTip(
            translate(
                "MainWindow",
                "Take Nexus links back from another mod manager; links for other games are passed on to it",
            )
        )
        self.actionHandleNxm.setText(translate("MainWindow", "Handle Nexus Links (Mod Manager Download)"))
        self.actionHandleNxm.setToolTip(
            translate(
                "MainWindow", "Open nxm:// links from the Nexus website here; other games go to the previous program"
            )
        )
        self.actionKeepArchives.setToolTip(
            translate("MainWindow", "Copy archives installed from other folders into the download folder")
        )
        self.actionChangeBackupFolder.setText(translate("MainWindow", "Change Backup Folder..."))
