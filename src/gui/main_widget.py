'''Main Widget'''

import os
import shlex
import shutil
import time
from collections.abc import Callable
from os import path
from sys import platform

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QFileInfo,
    QMetaObject,
    QObject,
    QRect,
    QSize,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import QAction, QActionGroup, QBrush, QCursor, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFileIconProvider,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)
from watchdog.events import DirModifiedEvent, FileModifiedEvent, PatternMatchingEventHandler
from watchdog.observers import Observer

from src.core.duplicates import duplicateEntries, matchingEntries, versionNote
from src.core.fetcher import isArchive
from src.core.health import ERROR, INFO, WARNING, Issue, checkHealth
from src.core.installer import Installer
from src.core.model import Model
from src.core.nexus import (
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
    checkForUpdate,
    parseModReference,
    uniquePath,
)
from src.core.nexusdetails import DetailsCache, NexusGraphQL, remasteredSuggestions
from src.core.nxm import NxmLink, NxmRegistration, WindowsRegistry, forward, parseNxm
from src.core.rmmr import RmmrError, applyImport, isRmmrFolder, planImport
from src.core.scriptmerger import ConflictReport, analyse, findScriptMerger, installedVersion, isNewer, nexusId
from src.core.updater import ModUpdater
from src.domain.mod import Mod
from src.globals import data
from src.globals.constants import *
from src.gui.alerts import (
    MessageAlertIncompleteInstallation,
    MessageUnsupportedOSAction,
)
from src.gui.browse_widget import BrowseWidget
from src.gui.details_dialog import DetailsDialog
from src.gui.health_dialog import HealthDialog
from src.gui.mod_preview import ModPreview
from src.gui.nexus_dialogs import (
    SWITCH,
    BackupsDialog,
    NexusFileDialog,
    NexusWorker,
    RmmrImportDialog,
    UpdateRow,
    UpdatesDialog,
    askApiKey,
    statusText,
)
from src.gui.scriptmerger_dialog import MessageAlertScriptConflicts, ScriptConflictsDialog
from src.gui.themes import get_dark_palette, get_light_palette, get_system_palette, statusColor
from src.gui.tree_widget import CustomTreeWidgetItem
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
        self.detailsWorker: NexusWorker | None = None

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
        self.restoreWindowState()
        self.checkLanguage()
        self.checkTheme()
        self.refreshList()
        self.reportDuplicates()
        self.detectScriptMerger()
        self.reportHealth()
        self.refreshNexusDetails(silent=True, onlyMissing=True)

        QMetaObject.connectSlotsByName(self.mainWindow)

        self.mainWindow.resizeEvent = lambda e: self.onResize()  # type: ignore
        self.loadOrder.header().sectionResized.connect(lambda: self.onResize())
        self.treeWidget.header().sectionResized.connect(lambda: self.onResize())

        self.actionAlert_to_run_Script_Merger.setChecked(data.getConfig().allowpopups == "1")
        self.actionUseNativeFileDialogs.setChecked(data.getConfig().get("SETTINGS", "usenativedialog", "1") == "1")
        self.actionKeepArchives.setChecked(data.getConfig().keeparchives == "1")
        self.actionHandleNxm.setEnabled(platform == "win32")
        if platform == "win32":
            try:
                self.actionHandleNxm.setChecked(self.nxmRegistration().isRegistered())
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
        self.refreshLoadOrder()

    def restoreWindowState(self):
        '''Restore the toolbar position/layout saved from a previous session'''
        state = data.getConfig().get("WINDOW", "state")
        if state:
            try:
                self.mainWindow.restoreState(QByteArray.fromBase64(state.encode("ascii")))
            except Exception as err:
                print("failed to restore window state:", err)

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

        self.toolBar = QToolBar(self.mainWindow)
        self.toolBar.setObjectName("toolBar")
        self.mainWindow.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolBar)

    def setupUI(self):
        '''Setup all UI components'''
        # Central widget
        self.centralwidget = QWidget(self.mainWindow)
        self.centralwidget.setObjectName("centralwidget")
        self.verticalLayout_2 = QVBoxLayout(self.centralwidget)
        self.verticalLayout_2.setObjectName("verticalLayout_2")

        # Tabs: the installed mods, and browsing Nexus
        self.tabs = QTabWidget(self.centralwidget)
        self.tabs.setObjectName("tabs")
        self.installedTab = QWidget(self.tabs)
        self.installedLayout = QVBoxLayout(self.installedTab)
        self.installedLayout.setContentsMargins(0, 4, 0, 0)

        # Search widget
        self.searchWidget = QLineEdit(self.installedTab)
        self.searchWidget.setObjectName("searchWidget")
        self.searchWidget.setPlaceholderText(translate("MainWindow", "Search"))
        self.installedLayout.addWidget(self.searchWidget)

        # Setup tree widget
        self.setupTreeWidget()
        self.tabs.addTab(self.installedTab, translate("MainWindow", "Installed"))

        self.browse = BrowseWidget(
            self.tabs, self.nexusDetails, lambda: set(self.linkedModIds()), data.getConfig().gameversion == "re"
        )
        self.browse.installRequested.connect(self.installFromNexus)
        self.tabs.addTab(self.browse, translate("MainWindow", "Browse Nexus"))
        self.verticalLayout_2.addWidget(self.tabs)

        # Setup text output and buttons
        self.setupOutputArea()

        # Progress bar
        self.progressBar = QProgressBar(self.centralwidget)
        self.progressBar.setProperty("value", 0)
        self.progressBar.setObjectName("progressBar")
        self.verticalLayout_2.addWidget(self.progressBar)

        # Stretch factors
        self.verticalLayout_2.setStretch(0, 4)
        self.verticalLayout_2.setStretch(1, 1)

        self.mainWindow.setCentralWidget(self.centralwidget)

    def setupTreeWidget(self):
        '''Setup tree widget and load order panel'''
        # Mod list tree widget
        self.treeWidget = QTreeWidget(self.centralwidget)
        self.treeWidget.setMinimumSize(QSize(600, 350))
        self.treeWidget.setUniformRowHeights(True)
        self.treeWidget.setAnimated(True)
        self.treeWidget.setHeaderHidden(False)
        self.treeWidget.setColumnCount(8)
        self.treeWidget.setObjectName("treeWidget")
        self.treeWidget.header().setCascadingSectionResizes(True)
        self.treeWidget.header().setHighlightSections(False)
        self.treeWidget.header().setSortIndicatorShown(True)
        self.treeWidget.setSortingEnabled(True)

        # Splitter for the tree and load order
        self.horizontalSplitter_tree = QSplitter()
        self.horizontalSplitter_tree.setObjectName("horizontalSplitter_tree")
        self.horizontalSplitter_tree.addWidget(self.treeWidget)

        # Load order tree widget
        self.loadOrder = QTreeWidget(self.centralwidget)
        self.loadOrder.setUniformRowHeights(True)
        self.loadOrder.setAnimated(True)
        self.loadOrder.setHeaderHidden(False)
        self.loadOrder.setColumnCount(2)
        self.loadOrder.setObjectName("loadOrder")
        self.loadOrder.setMinimumWidth(200)
        self.loadOrder.setSortingEnabled(False)

        # Configure splitter
        # the preview of the selected mod sits above the load order
        self.preview = ModPreview(self.centralwidget)
        self.verticalSplitter_right = QSplitter(Qt.Orientation.Vertical)
        self.verticalSplitter_right.setObjectName("verticalSplitter_right")
        self.verticalSplitter_right.addWidget(self.preview)
        self.verticalSplitter_right.addWidget(self.loadOrder)
        self.verticalSplitter_right.setStretchFactor(0, 1)
        self.verticalSplitter_right.setStretchFactor(1, 1)
        self.horizontalSplitter_tree.addWidget(self.verticalSplitter_right)
        self.horizontalSplitter_tree.setCollapsible(0, False)
        self.horizontalSplitter_tree.setCollapsible(1, True)
        self.horizontalSplitter_tree.setStretchFactor(0, 3)
        self.horizontalSplitter_tree.setStretchFactor(1, 1)
        self.installedLayout.addWidget(self.horizontalSplitter_tree)

    def setupOutputArea(self):
        '''Setup output text area and action buttons'''
        self.horizontalLayout_2 = QHBoxLayout()
        self.horizontalLayout_2.setObjectName("horizontalLayout_2")

        # Output text widget
        self.textEdit = QTextEdit(self.centralwidget)
        self.textEdit.setMaximumSize(QSize(16777215, 16777215))
        self.textEdit.setReadOnly(True)
        self.textEdit.setObjectName("textEdit")
        self.horizontalLayout_2.addWidget(self.textEdit)

        # Button layout
        self.verticalLayout = QVBoxLayout()
        self.verticalLayout.setObjectName("verticalLayout")

        # Script merger button
        self.scriptMergerButton = QPushButton(self.centralwidget)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.scriptMergerButton.sizePolicy().hasHeightForWidth())
        self.scriptMergerButton.setSizePolicy(sizePolicy)
        self.scriptMergerButton.setMinimumSize(QSize(100, 50))
        self.scriptMergerButton.setObjectName("scriptMergerButton")
        self.verticalLayout.addWidget(self.scriptMergerButton)

        # Run game button
        self.runGameButton = QPushButton(self.centralwidget)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.runGameButton.sizePolicy().hasHeightForWidth())
        self.runGameButton.setSizePolicy(sizePolicy)
        self.runGameButton.setMinimumSize(QSize(100, 50))
        self.runGameButton.setObjectName("runGameButton")
        self.verticalLayout.addWidget(self.runGameButton)

        # Add button layout to horizontal layout
        self.horizontalLayout_2.addLayout(self.verticalLayout)
        self.horizontalLayout_2.setStretch(0, 3)
        self.horizontalLayout_2.setStretch(1, 1)
        self.verticalLayout_2.addLayout(self.horizontalLayout_2)

    def configureUi(self):
        for i in range(self.treeWidget.header().count()):
            if not data.getConfig().getWindowSection(i):
                data.getConfig().setDefaultWindow()
                break

        self.treeWidget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.treeWidget.header().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.treeWidget.sortByColumn(1, Qt.SortOrder.AscendingOrder)

        self.loadOrder.header().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loadOrder.header().setStretchLastSection(False)
        self.loadOrder.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.loadOrder.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        self.resizeColumns()

        self.actionInstall_Mods.triggered.connect(self.installMods)
        self.actionUninstall_Mods.triggered.connect(self.uninstallMods)
        self.actionReinstall_Mods.triggered.connect(self.reinstallMods)
        self.actionAbout.triggered.connect(showAboutWindow)
        self.actionEnable_Disable_Mods.triggered.connect(self.enableDisableMods)
        self.actionRefresh_Mod_List.triggered.connect(lambda e: self.refreshList())
        self.actionRefresh_Load_Order.triggered.connect(lambda e: self.refreshLoadOrder())
        self.actionScriptConflicts.triggered.connect(self.showScriptConflicts)
        self.actionHealthCheck.triggered.connect(self.showHealthCheck)
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
        self.actionCheckUpdates.triggered.connect(lambda: self.checkForUpdates())
        self.actionUpdateMods.triggered.connect(self.updateSelectedMods)
        self.actionSetNexusMod.triggered.connect(self.setNexusMod)
        self.actionChooseFile.triggered.connect(self.chooseInstalledFile)
        self.actionOpenNexusPage.triggered.connect(self.openNexusPage)
        self.actionRestoreBackup.triggered.connect(self.restoreBackup)
        self.actionImportRmmr.triggered.connect(self.importFromRmmr)
        self.actionRefreshDetails.triggered.connect(lambda: self.refreshNexusDetails())
        self.actionCheckRemastered.triggered.connect(self.checkRemastered)
        self.actionKeepArchives.triggered.connect(self.keepArchivesChanged)
        self.actionHandleNxm.triggered.connect(self.handleNxmChanged)
        self.actionSetApiKey.triggered.connect(lambda: askApiKey(self))
        self.actionChangeArchiveFolder.triggered.connect(self.changeArchiveFolder)
        self.actionChangeBackupFolder.triggered.connect(self.changeBackupFolder)

        self.scriptMergerButton.clicked.connect(self.runScriptMerger)
        self.runGameButton.clicked.connect(self.runTheGame)

        self.treeWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.treeWidget.customContextMenuRequested.connect(self.openMenu)

        self.toolBar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.toolBar.customContextMenuRequested.connect(self.toolbarMenu)

        self.textEdit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.textEdit.customContextMenuRequested.connect(self.openEditMenu)

        self.treeWidget.itemChanged.connect(self.modToggled)
        self.treeWidget.installEventFilter(self)
        self.treeWidget.itemDoubleClicked.connect(self.modDoubleClicked)
        self.treeWidget.itemSelectionChanged.connect(self.updatePreview)
        self.treeWidget.header().setStretchLastSection(False)

        self.loadOrder.itemDoubleClicked.connect(self.loadOrderDoubleClicked)

        self.searchWidget.textChanged.connect(self.setSearchString)

    def openByConfigKey(self, option):
        '''Open or run any kind of folder/file or executable by configuration key'''
        openFile(getattr(data.getConfig(), option))

    def configureToolbar(self):
        '''Creates and configures toolbar'''
        self.toolBar.clear()

        self.toolBar.addAction(self.actionInstall_Mods)
        self.toolBar.addAction(self.actionUninstall_Mods)
        self.toolBar.addAction(self.actionEnable_Disable_Mods)
        self.toolBar.addAction(self.actionCheckUpdates)
        self.toolBar.setIconSize(QSize(32, 32))
        self.toolBar.addSeparator()

        actionTemp = QAction(self.mainWindow)
        actionTemp.triggered.connect(lambda: self.openByConfigKey("mods"))
        actionTemp.setText("M")
        actionTemp.setIconText(translate("MainWindow", "Mods"))
        actionTemp.setIcon(getIcon("mods.ico"))
        actionTemp.setToolTip(translate("MainWindow", "Open Mods folder"))
        self.toolBar.addAction(actionTemp)

        actionTemp = QAction(self.mainWindow)
        actionTemp.triggered.connect(lambda: self.openByConfigKey("dlc"))
        actionTemp.setText("D")
        actionTemp.setIconText(translate("MainWindow", "DLC"))
        actionTemp.setIcon(getIcon("dlc.ico"))
        actionTemp.setToolTip(translate("MainWindow", "Open DLC folder"))
        self.toolBar.addAction(actionTemp)

        actionTemp = QAction(self.mainWindow)
        actionTemp.triggered.connect(lambda: self.openByConfigKey("menu"))
        actionTemp.setText("I")
        actionTemp.setIconText(translate("MainWindow", "Menus"))
        actionTemp.setIcon(getIcon("menu.ico"))
        actionTemp.setToolTip(translate("MainWindow", "Open Menus folder"))
        self.toolBar.addAction(actionTemp)

        actionTemp = QAction(self.mainWindow)
        actionTemp.triggered.connect(lambda: self.openByConfigKey("settings"))
        actionTemp.setText("S")
        actionTemp.setIconText(translate("MainWindow", "Settings"))
        actionTemp.setIcon(getIcon("settings.ico"))
        actionTemp.setToolTip(translate("MainWindow", "Open Settings folder"))
        self.toolBar.addAction(actionTemp)

        self.toolBar.addSeparator()

        actionTemp = QAction(self.mainWindow)
        actionTemp.triggered.connect(lambda: openFile(data.getConfig().menu + "/input.xml"))
        actionTemp.setText(translate("MainWindow", "Input Xml"))
        actionTemp.setIcon(getIcon("xml.ico"))
        actionTemp.setToolTip(translate("MainWindow", "Open input.xml file"))
        self.toolBar.addAction(actionTemp)

        actionTemp = QAction(self.mainWindow)
        actionTemp.triggered.connect(lambda: openFile(data.getConfig().settings + "/input.settings"))
        actionTemp.setText(translate("MainWindow", "Input Settings"))
        actionTemp.setIcon(getIcon("input.ico"))
        actionTemp.setToolTip(translate("MainWindow", "Open input.settings file"))
        self.toolBar.addAction(actionTemp)

        actionTemp = QAction(self.mainWindow)
        actionTemp.triggered.connect(lambda: openFile(data.getConfig().settings + "/" + data.getConfig().usersettings))
        actionTemp.setText(translate("MainWindow", "User Settings"))
        actionTemp.setIcon(getIcon("user.ico"))
        actionTemp.setToolTip(translate("MainWindow", "Open user.settings file"))
        self.toolBar.addAction(actionTemp)

        actionTemp = QAction(self.mainWindow)
        actionTemp.triggered.connect(lambda: openFile(data.getConfig().settings + "/mods.settings"))
        actionTemp.setText(translate("MainWindow", "Mods Settings"))
        actionTemp.setIcon(getIcon("modset.ico"))
        actionTemp.setToolTip(translate("MainWindow", "Open mods.settings file"))
        self.toolBar.addAction(actionTemp)

        self.toolBar.addSeparator()

        for custom in data.getConfig().getOptions("TOOLBAR"):
            self.addToToolbar(custom)
        self.actionAddToToolbar = QAction(self.mainWindow)
        self.actionAddToToolbar.triggered.connect(self.addToToolbar)
        self.actionAddToToolbar.setText(translate("MainWindow", "Add New.."))

    def openMenu(self, position):
        '''Right click menu on mod list (Left panel)'''
        menu = QMenu()
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
        menu.addAction(self.actionRename)
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
        actions = self.toolBar.actions()[15:]
        for action in actions:
            temp = self.makeTempAction(action)
            rem.addAction(temp)
            del action
        menu.addAction(rem.menuAction())
        menu.exec(self.toolBar.mapToGlobal(position))

    def removeFromToolbar(self, action):
        '''Creates menu for removing actions from toolbar'''
        self.toolBar.removeAction(action)
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
                self.horizontalSplitter_tree.setSizes([int(hsplit0), int(hsplit1)])
            vsplit0 = data.getConfig().get("WINDOW", "vsplit0")
            vsplit1 = data.getConfig().get("WINDOW", "vsplit1")
            self.verticalSplitter_right.setSizes([int(vsplit0), int(vsplit1)] if vsplit0 and vsplit1 else [420, 260])
        except Exception as e:
            print(f"couldn't restore split: {e}")

    def output(self, appendation):
        '''Prints appendation to the output text field'''
        self.textEdit.append(appendation)

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
                installer = Installer(self.model, output=self.output, duplicate=self.handleDuplicate)
                for mod in file:
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
            directory, _ = path.split(scriptmergerpath)
            if platform == "win32" or platform == "cygwin":
                subprocess.Popen([scriptmergerpath], cwd=directory)
            elif platform == "linux" or platform == "darwin":
                command = data.getConfig().mergerlaunchcommand
                if command:
                    subprocess.Popen(command, cwd=directory, shell=True)
                else:
                    subprocess.Popen(["wine", scriptmergerpath], cwd=directory)
            else:
                MessageUnsupportedOSAction("")
        except Exception as err:
            self.output(formatUserError(err))

    def selectAllMods(self):
        '''Selects all mods in the list'''
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
        names = []
        for index in range(self.treeWidget.topLevelItemCount()):
            item = self.treeWidget.topLevelItem(index)
            if item is not None:
                names.append(item.text(1))
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
            for index in range(self.treeWidget.topLevelItemCount()):
                item = self.treeWidget.topLevelItem(index)
                mod = self.model.modList.get(item.text(1)) if item is not None else None
                if item is not None and mod is not None:
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
            selected = self.treeWidget.selectedItems()
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

    def runNexus(self, work, done) -> None:
        '''Runs work(progress) in the background and done(result) on the GUI thread'''
        worker = NexusWorker(work, self)
        worker.progressed.connect(lambda current, total: self.setProgress(100 * current / total if total else 0))
        worker.succeeded.connect(done)
        worker.failed.connect(lambda message: self.output(message))
        worker.finished.connect(lambda: self.setProgress(0))
        worker.finished.connect(lambda: self.nexusFinished(worker))
        self.nexusWorker = worker
        worker.start()

    def nexusFinished(self, worker: NexusWorker) -> None:
        '''Starts the next queued Nexus link once the previous request is done'''
        if self.nexusWorker is worker:
            self.nexusWorker = None
        self.processNxmQueue()

    def checkForUpdates(self, names: list[str] | None = None) -> None:
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
        self.runNexus(work, lambda results: self.updatesChecked(checked, results))

    def scriptMergerChecked(self, files: ModFiles | str) -> None:
        '''Reports whether a newer Script Merger is on Nexus'''
        installed = installedVersion(data.getConfig().scriptmerger)
        if isinstance(files, str):
            self.output(translate("MainWindow", "Couldn't check Script Merger:") + f" {files}")
            return
        current = files.currentFiles()
        main = [file for file in current if file.category == "MAIN"] or current
        if not main or not installed:
            return
        latest = main[0]
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
        answer = QMessageBox.question(
            self,
            translate("MainWindow", "Script Merger update"),
            translate("MainWindow", "Script Merger")
            + f" {latest.version} "
            + translate("MainWindow", "is available, you have")
            + f" {installed}.\n\n"
            + translate("MainWindow", "Open its Nexus page?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            openUrl(url)

    def updatesChecked(self, names: list[str], results: dict) -> None:
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
            rows.append(UpdateRow(name, mod.nexus.version, check))
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
        if not rows:
            return
        dialog = UpdatesDialog(self, rows)
        if dialog.exec() and dialog.selection():
            self.downloadAndUpdate(dialog.selection())

    def updateSelectedMods(self) -> None:
        selected = self.getSelectedMods()
        if not selected:
            self.output(translate("MainWindow", "Select the mods to update first."))
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
        self.runNexus(work, self.applyUpdates)

    def applyUpdates(self, results: list[tuple[str, int, NexusFile, str, str, int]]) -> None:
        updated = failed = incomplete = 0
        updater = ModUpdater(self.model, output=self.output)
        for index, (name, modId, file, archive, error, status) in enumerate(results):
            mod = self.model.modList.get(name)
            if status == 403 and mod is not None:
                # free accounts can only download through the website's "Mod Manager Download" button
                self.openNexusDownload(modId, file.fileId, name)
                continue
            if error or mod is None:
                self.output(f"{name}: {error or translate('MainWindow', 'no longer installed')}")
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
            else:
                failed += 1
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
        text = (
            f"'{mod.name}' "
            + translate("MainWindow", "looks like a mod that's already installed:")
            + "\n\n"
            + "\n".join(f"  {match.mod.name} {translate('MainWindow', match.reason)}" for match in matches)
            + "\n\n"
            + (version + "\n\n" if version else "")
            + translate("MainWindow", "Update replaces")
            + f" '{first.mod.name}' "
            + translate(
                "MainWindow",
                "with this version, keeping its name, priority, enabled state and settings, after backing it up.",
            )
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
        result = ModUpdater(self.model, output=self.output).update(existing, source, nexus, "install")
        self.updateChecks.pop(existing.name, None)
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
        self.runNexus(work, self.nxmDownloaded)

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
        if not archive:
            self.openNexusDownload(link.modId, link.fileId, file.name if file else str(link.modId))
            return
        filename = path.basename(archive)
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
        self.installModFiles([archive])
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

    def updatePreview(self) -> None:
        selected = self.getSelectedMods()
        mod = self.model.modList.get(selected[0]) if selected else None
        details = self.modDetails(mod) if mod is not None else None
        remastered = data.getConfig().gameversion == "re"
        self.preview.showMod(mod, details, self.nexusDetails.image(details), remastered)

    def refreshNexusDetails(self, silent: bool = False, onlyMissing: bool = False) -> None:
        '''Fetches pictures, categories and summaries of the linked mods in the background'''
        if self.detailsWorker is not None and self.detailsWorker.isRunning():
            return
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
            duplicates = duplicateEntries(self.model)
            moddata = []
            for mod in self.model.all():
                if len(self.searchString) > 0:
                    if self.searchString.lower() not in mod.name.lower():
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
                )
                if mod.name in duplicates:
                    row.setForeground(1, QBrush(statusColor("warning")))
                    row.setToolTip(
                        1,
                        translate("MainWindow", "Installed twice?")
                        + "\n"
                        + "\n".join(f"{mod.name} {reason}" for reason in duplicates[mod.name]),
                    )
            for item in selected:
                rows = self.treeWidget.findItems(item, Qt.MatchFlag.MatchEndsWith, 1)
                if rows:
                    for row in rows:
                        row.setSelected(True)
            if write:
                self.refreshLoadOrder()
                self.model.write()
        except Exception as err:
            self.output(translate("MainWindow", "Couldn't refresh list: ") + f"{formatUserError(err)}")
            return err
        return None

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
                    self.loadOrder.addTopLevelItem(item)
            for item in selected:
                rows = self.loadOrder.findItems(item.replace("~", ""), Qt.MatchFlag.MatchEndsWith, 0)
                if rows:
                    for row in rows:
                        row.setSelected(True)
        except Exception as err:
            self.output(translate("MainWindow", "Couldn't read or refresh load order: ") + f"{formatUserError(err)}")
            return err
        return None

    def setProgress(self, currentProgress):
        '''Sets the progress to currentProgress'''
        self.progressBar.setProperty("value", currentProgress)

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
    ):
        '''Adds mod data to the list'''
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
        item.setTextAlignment(10, Qt.AlignmentFlag.AlignRight)
        item.setTextAlignment(11, Qt.AlignmentFlag.AlignCenter)
        if "~" not in name:
            if on:
                item.setCheckState(0, Qt.CheckState.Checked)
            else:
                item.setCheckState(0, Qt.CheckState.Unchecked)
        self.treeWidget.addTopLevelItem(item)
        return item

    def getSelectedMods(self):
        '''Returns list of mod names of the selected mods'''
        array = []
        getSelected = self.treeWidget.selectedItems()
        if getSelected:
            for selected in getSelected:
                baseNode = selected
                array.append(baseNode.text(1))
        return array

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
        HealthDialog(self, self.healthIssues(), self.healthIssues).exec()

    def reportHealth(self) -> None:
        '''Mentions problems at startup, without opening anything'''
        issues = [issue for issue in self.healthIssues() if issue.severity in (ERROR, WARNING)]
        if issues:
            self.output(
                translate("MainWindow", "Health check:")
                + " "
                + plural(
                    len(issues),
                    translate("MainWindow", "problem or warning"),
                    translate("MainWindow", "problems or warnings"),
                )
                + " (Mods > Health Check...)"
            )

    def changeTheme(self, theme):
        data.getConfig().theme = theme
        if theme == "Dark":
            data.getApp().setPalette(get_dark_palette())
        elif theme == "Light":
            data.getApp().setPalette(get_light_palette())
        else:
            data.getApp().setPalette(get_system_palette())
        data.getConfig().write_config()

    def checkTheme(self):
        theme = data.getConfig().theme
        for action in self.menuSelect_Theme.actions():
            if action.text() == theme:
                action.setChecked(True)
                break

    def createActions(self):
        '''Create all action objects'''
        self.actionInstall_Mods = QAction(self.mainWindow)
        self.actionInstall_Mods.setIcon(getIcon("Add.ico"))
        self.actionInstall_Mods.setIconVisibleInMenu(False)
        self.actionInstall_Mods.setObjectName("actionInstall_Mods")
        self.actionInstall_Mods.setIconText(translate("MainWindow", "Add"))

        self.actionRestoreColumns = QAction(self.mainWindow)
        self.actionRestoreColumns.setIconVisibleInMenu(False)
        self.actionRestoreColumns.setObjectName("actionRestoreColumns")

        self.actionUninstall_Mods = QAction(self.mainWindow)
        self.actionUninstall_Mods.setIcon(getIcon("rem.ico"))
        self.actionUninstall_Mods.setIconVisibleInMenu(False)
        self.actionUninstall_Mods.setObjectName("actionUninstall_Mods")
        self.actionUninstall_Mods.setIconText(translate("MainWindow", "Remove"))

        self.actionEnable_Disable_Mods = QAction(self.mainWindow)
        self.actionEnable_Disable_Mods.setIcon(getIcon("check.ico"))
        self.actionEnable_Disable_Mods.setIconVisibleInMenu(False)
        self.actionEnable_Disable_Mods.setObjectName("actionEnable_Disable_Mods")
        self.actionEnable_Disable_Mods.setIconText(translate("MainWindow", "Toggle"))

        self.actionReinstall_Mods = QAction(self.mainWindow)
        self.actionReinstall_Mods.setObjectName("actionReinstall_Mods")

        self.actionRefresh_Mod_List = QAction(self.mainWindow)
        self.actionRefresh_Mod_List.setObjectName("actionRefresh_Mod_List")

        self.actionRefresh_Load_Order = QAction(self.mainWindow)
        self.actionRefresh_Load_Order.setObjectName("actionRefresh_Load_Order")

        self.actionScriptConflicts = QAction(self.mainWindow)
        self.actionScriptConflicts.setObjectName("actionScriptConflicts")

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
        self.actionCheckUpdates.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.actionCheckUpdates.setIconVisibleInMenu(False)
        self.actionCheckUpdates.setObjectName("actionCheckUpdates")
        self.actionCheckUpdates.setIconText(translate("MainWindow", "Updates"))

        self.actionUpdateMods = QAction(self.mainWindow)
        self.actionUpdateMods.setObjectName("actionUpdateMods")

        self.actionSetNexusMod = QAction(self.mainWindow)
        self.actionSetNexusMod.setObjectName("actionSetNexusMod")

        self.actionChooseFile = QAction(self.mainWindow)
        self.actionChooseFile.setObjectName("actionChooseFile")

        self.actionOpenNexusPage = QAction(self.mainWindow)
        self.actionOpenNexusPage.setObjectName("actionOpenNexusPage")

        self.actionRestoreBackup = QAction(self.mainWindow)
        self.actionRestoreBackup.setObjectName("actionRestoreBackup")

        self.actionImportRmmr = QAction(self.mainWindow)
        self.actionImportRmmr.setObjectName("actionImportRmmr")

        self.actionRefreshDetails = QAction(self.mainWindow)
        self.actionRefreshDetails.setObjectName("actionRefreshDetails")

        self.actionCheckRemastered = QAction(self.mainWindow)
        self.actionCheckRemastered.setObjectName("actionCheckRemastered")

        self.actionKeepArchives = QAction(self.mainWindow)
        self.actionKeepArchives.setCheckable(True)
        self.actionKeepArchives.setObjectName("actionKeepArchives")

        self.actionHandleNxm = QAction(self.mainWindow)
        self.actionHandleNxm.setCheckable(True)
        self.actionHandleNxm.setObjectName("actionHandleNxm")

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
        self.menuFile.addAction(self.actionReinstall_Mods)
        self.menuFile.addAction(self.actionRefresh_Mod_List)
        self.menuFile.addAction(self.actionRefresh_Load_Order)
        self.menuFile.addAction(self.actionScriptConflicts)
        self.menuFile.addAction(self.actionHealthCheck)
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
        self.menuNexus.addSeparator()
        self.menuNexus.addAction(self.actionSetNexusMod)
        self.menuNexus.addAction(self.actionChooseFile)
        self.menuNexus.addAction(self.actionOpenNexusPage)
        self.menuNexus.addAction(self.actionRestoreBackup)
        self.menuNexus.addAction(self.actionImportRmmr)
        self.menuNexus.addSeparator()
        self.menuNexus.addAction(self.actionRefreshDetails)
        self.menuNexus.addAction(self.actionCheckRemastered)
        self.menuNexus.addSeparator()
        self.menuNexus.addAction(self.actionSetApiKey)
        self.menuNexus.addAction(self.actionChangeArchiveFolder)
        self.menuNexus.addAction(self.actionKeepArchives)
        self.menuNexus.addAction(self.actionHandleNxm)
        self.menuNexus.addAction(self.actionChangeBackupFolder)

        # --- Build Help menu ---
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
        self.menuSettings.addAction(self.menuSelect_Theme.menuAction())
        self.menuSettings.addAction(self.menuSelect_Language.menuAction())
        self.menuSettings.addAction(self.menuConfigure_Settings.menuAction())

    def translateUi(self):
        self.mainWindow.setWindowTitle(translate("MainWindow", TITLE))

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

        self.loadOrder.headerItem().setText(0, translate("MainWindow", "Load Order"))
        self.loadOrder.headerItem().setText(1, translate("MainWindow", "Priority"))

        self.textEdit.setPlaceholderText(translate("MainWindow", "Output"))
        self.textEdit.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

        self.scriptMergerButton.setText(translate("MainWindow", "Run Script Merger"))
        launchLabel = self.gameLaunchLabel()
        self.runGameButton.setText(translate("MainWindow", "Run the Game") + " (" + launchLabel + ")")
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
        self.actionReinstall_Mods.setText(translate("MainWindow", "Restore Settings"))
        self.actionReinstall_Mods.setToolTip(
            translate("MainWindow", "Restore stored mod settings and key bindings to their defaults")
        )
        self.actionRefresh_Load_Order.setText(translate("MainWindow", "Refresh Load Order"))
        self.actionRefresh_Load_Order.setShortcut("F6")
        self.actionScriptConflicts.setText(translate("MainWindow", "Script Conflicts..."))
        self.actionScriptConflicts.setToolTip(
            translate("MainWindow", "Scripts changed by more than one mod, and whether they are merged")
        )
        self.actionScriptConflicts.setShortcut("F8")
        self.actionHealthCheck.setText(translate("MainWindow", "Health Check..."))
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
        self.actionAbout.setText(translate("MainWindow", "About"))
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
        self.actionCheckUpdates.setToolTip(translate("MainWindow", "Check all mods with a Nexus ID for updates"))
        self.actionCheckUpdates.setShortcut("F7")
        self.actionUpdateMods.setText(translate("MainWindow", "Update Selected Mods"))
        self.actionUpdateMods.setToolTip(translate("MainWindow", "Check the selected mods and update them"))
        self.actionUpdateMods.setShortcut("Ctrl+F7")
        self.actionSetNexusMod.setText(translate("MainWindow", "Set Nexus Mod..."))
        self.actionChooseFile.setText(translate("MainWindow", "Choose Nexus File..."))
        self.actionChooseFile.setToolTip(
            translate("MainWindow", "Record which file of the Nexus page is installed, or switch to another file")
        )
        self.actionOpenNexusPage.setText(translate("MainWindow", "Open Nexus Page"))
        self.actionOpenNexusPage.setShortcut("Ctrl+N")
        self.actionRestoreBackup.setText(translate("MainWindow", "Restore Previous Version..."))
        self.actionImportRmmr.setText(translate("MainWindow", "Import from RMMR..."))
        self.actionRefreshDetails.setText(translate("MainWindow", "Refresh Nexus Details"))
        self.actionRefreshDetails.setToolTip(
            translate("MainWindow", "Load pictures, categories and summaries of linked mods")
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
