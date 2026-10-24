'''Setup dialog: the game, Script Merger, download folder and Nexus API key on one page.
Shown on the first run instead of the bare "select witcher3.exe" picker, and from Settings > Setup... later.'''

from collections.abc import Callable
from os import path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.configuration.config import Configuration
from src.core.gamefinder import GameInstall, findGameInstalls, nexusApiKeyPage
from src.core.nexus import NexusClient, NexusError
from src.core.scriptmerger import findScriptMerger
from src.globals.constants import translate
from src.gui.themes import statusColor
from src.util.util import normalizePath, openUrl

# what the API key field shows while a stored key is kept as it is
KEPT_KEY = "•" * 12


def validateApiKey(key: str) -> dict[str, Any]:
    return NexusClient(key).validate()


class SetupDialog(QDialog):
    '''Edits the paths and the API key, and writes them to the configuration only when accepted'''

    def __init__(
        self,
        parent: QWidget | None,
        config: Configuration,
        firstRun: bool = False,
        installs: Callable[[], list[GameInstall]] | None = None,
        findMerger: Callable[[str, str, str], str] = findScriptMerger,
        validateKey: Callable[[str], dict[str, Any]] = validateApiKey,
    ):
        super().__init__(parent)
        self.config = config
        self.firstRun = firstRun
        self.findMerger = findMerger
        self.validateKey = validateKey
        self.installs = (installs or (lambda: findGameInstalls(valid=Configuration.getCorrectGamePath)))()
        self.keyChecked: dict[str, Any] | None = None
        self.setWindowTitle(translate("Setup", "Setup"))
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        intro = QLabel(
            translate(
                "Setup",
                "Where the game is, and the optional tools the manager works with. "
                "All of this can be changed later from Settings > Setup...",
            )
            if firstRun
            else translate("Setup", "The game, Script Merger, the download folder and the Nexus Mods API key."),
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QGridLayout()
        form.setColumnStretch(1, 1)
        layout.addLayout(form)

        # game
        self.gameBox = QComboBox(self)
        self.gameBox.setEditable(True)
        self.gameBox.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for install in self.installs:
            self.gameBox.addItem(install.label, install.folder)
        current = config.getCorrectGamePath(config.gameexe) if config.gameexe else ''
        if current:
            root = config.getGameRoot(current)
            index = next((i for i, install in enumerate(self.installs) if install.folder == root), -1)
            if index >= 0:
                self.gameBox.setCurrentIndex(index)
            else:
                self.gameBox.setEditText(root)
        elif not self.installs:
            self.gameBox.setEditText('')
        self.gameBox.currentIndexChanged.connect(lambda *_: self.gameChanged())
        self.gameBox.editTextChanged.connect(lambda *_: self.gameChanged())
        self.gameBrowse = QPushButton(translate("Setup", "Browse..."), self)
        self.gameBrowse.clicked.connect(self.browseGame)
        self.addRow(form, translate("Setup", "Game folder"), self.row(self.gameBox, self.gameBrowse))
        self.gameStatus = self.status(form)

        # Script Merger
        self.mergerEdit = QLineEdit(config.scriptmerger or '', self)
        self.mergerEdit.setPlaceholderText(translate("Setup", "WitcherScriptMerger.exe (optional)"))
        self.mergerBrowse = QPushButton(translate("Setup", "Browse..."), self)
        self.mergerBrowse.clicked.connect(self.browseMerger)
        self.addRow(form, translate("Setup", "Script Merger"), self.row(self.mergerEdit, self.mergerBrowse))
        self.mergerStatus = self.status(form)
        self.mergerEdit.textChanged.connect(lambda *_: self.updateMergerStatus())

        # downloads
        self.archivesEdit = QLineEdit(normalizePath(config.archives), self)
        self.archivesBrowse = QPushButton(translate("Setup", "Browse..."), self)
        self.archivesBrowse.clicked.connect(self.browseArchives)
        self.addRow(form, translate("Setup", "Download folder"), self.row(self.archivesEdit, self.archivesBrowse))
        self.status(form).setText(translate("Setup", "Where mod archives downloaded from Nexus are kept."))

        # API key
        self.keyEdit = QLineEdit(self)
        self.keyEdit.setEchoMode(QLineEdit.EchoMode.Password)
        self.keyEdit.setPlaceholderText(translate("Setup", "Optional: needed for update checks and downloads"))
        if config.nexusapikey:
            self.keyEdit.setText(KEPT_KEY)
        self.keyEdit.textChanged.connect(lambda *_: self.keyChangedByUser())
        self.keyTest = QPushButton(translate("Setup", "Check"), self)
        self.keyTest.clicked.connect(self.checkKey)
        self.keyPage = QPushButton(translate("Setup", "Get a Key..."), self)
        self.keyPage.clicked.connect(lambda: openUrl(nexusApiKeyPage()))
        self.addRow(form, translate("Setup", "Nexus API key"), self.row(self.keyEdit, self.keyTest, self.keyPage))
        self.keyStatus = self.status(form)
        self.keyStatus.setText(
            translate(
                "Setup",
                "Your personal key from the Nexus Mods API keys page. It is stored encrypted for your Windows "
                "account and only sent to Nexus. Direct downloads need a Premium account.",
            )
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.okButton = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.gameChanged()
        self.updateMergerStatus()

    # layout helpers

    def row(self, *widgets: QWidget) -> QWidget:
        box = QWidget(self)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        for widget in widgets:
            layout.addWidget(widget, 1 if widget is widgets[0] else 0)
        return box

    def addRow(self, form: QGridLayout, caption: str, field: QWidget) -> None:
        row = form.rowCount()
        form.addWidget(QLabel(caption, self), row, 0)
        form.addWidget(field, row, 1)

    def status(self, form: QGridLayout) -> QLabel:
        '''A wrapping help line under the field in the previous row'''
        label = QLabel(self)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addWidget(label, form.rowCount(), 1)
        form.setRowMinimumHeight(form.rowCount(), 6)
        return label

    def tell(self, label: QLabel, text: str, kind: str = '') -> None:
        label.setText(text)
        label.setStyleSheet(f"color: {statusColor(kind).name()};" if kind else '')

    # game

    def gameFolder(self) -> str:
        index = self.gameBox.currentIndex()
        data = self.gameBox.itemData(index) if index >= 0 else None
        text = self.gameBox.currentText().strip()
        if data and self.gameBox.itemText(index) == text:
            return str(data)
        return normalizePath(text) if text else ''

    def gameExe(self) -> str:
        return self.config.getCorrectGamePath(self.gameFolder()) if self.gameFolder() else ''

    def gameChanged(self) -> None:
        exe = self.gameExe()
        if exe:
            self.tell(self.gameStatus, translate("Setup", "Game found:") + f" {exe}")
        elif self.gameFolder():
            self.tell(
                self.gameStatus,
                translate("Setup", "No witcher3.exe under bin\\x64 or bin\\x64_dx12 in this folder."),
                'problem',
            )
        elif self.installs:
            self.tell(
                self.gameStatus, translate("Setup", "Pick an installation or browse to the game folder."), 'problem'
            )
        else:
            self.tell(
                self.gameStatus,
                translate("Setup", "No Steam or GOG installation found. Browse to the folder containing witcher3.exe."),
                'problem',
            )
        self.okButton.setEnabled(bool(exe))
        if exe and not self.mergerEdit.text().strip():
            found = self.findMerger(self.config.getGameRoot(exe), self.archivesEdit.text().strip(), self.config.rmmr)
            if found:
                self.mergerEdit.setText(found)

    def browseGame(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, translate("Setup", "The Witcher 3 folder"), self.gameFolder())
        if folder:
            self.gameBox.setEditText(normalizePath(folder))

    # Script Merger

    def updateMergerStatus(self) -> None:
        merger = self.mergerEdit.text().strip()
        if not merger:
            self.tell(
                self.mergerStatus,
                translate("Setup", "Needed to merge script mods. Get Script Merger from Nexus and point this at it."),
            )
        elif path.isfile(merger):
            self.tell(self.mergerStatus, translate("Setup", "Script Merger found."))
        else:
            self.tell(self.mergerStatus, translate("Setup", "This file doesn't exist."), 'problem')

    def browseMerger(self) -> None:
        file, _ = QFileDialog.getOpenFileName(
            self, translate("Setup", "Select WitcherScriptMerger.exe"), self.mergerEdit.text(), "*.exe"
        )
        if file:
            self.mergerEdit.setText(normalizePath(file))

    # downloads

    def browseArchives(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, translate("Setup", "Folder for downloaded mod archives"), self.archivesEdit.text()
        )
        if folder:
            self.archivesEdit.setText(normalizePath(folder))

    # API key

    def keyEntered(self) -> str | None:
        '''The key typed in, "" to remove the stored one, or None to keep it'''
        text = self.keyEdit.text().strip()
        return None if text == KEPT_KEY else text

    def keyChangedByUser(self) -> None:
        self.keyChecked = None
        if self.keyEntered() is None:
            self.tell(self.keyStatus, translate("Setup", "The stored key is kept."))
        elif self.keyEntered():
            self.tell(self.keyStatus, translate("Setup", "Click Check to make sure the key works."))
        else:
            self.tell(self.keyStatus, translate("Setup", "No key: the Nexus features stay off."))

    def checkKey(self) -> bool:
        key = self.keyEntered()
        if key is None:
            key = self.config.nexusapikey
        if not key:
            self.tell(self.keyStatus, translate("Setup", "Paste a key first."), 'warning')
            return False
        try:
            user = self.validateKey(key)
        except NexusError as error:
            self.keyChecked = None
            self.tell(self.keyStatus, str(error), 'problem')
            return False
        self.keyChecked = user
        premium = translate("Setup", "Premium") if user.get('is_premium') else translate("Setup", "Free")
        self.tell(self.keyStatus, translate("Setup", "Signed in as") + f" {user.get('name', '?')} ({premium}).")
        return True

    # result

    def accept(self) -> None:
        exe = self.gameExe()
        if not exe:
            return
        key = self.keyEntered()
        if key and self.keyChecked is None and not self.checkKey():
            return
        self.config.gameexe = exe
        self.config.scriptmerger = (
            normalizePath(self.mergerEdit.text().strip()) if self.mergerEdit.text().strip() else ''
        )
        archives = self.archivesEdit.text().strip()
        if archives and normalizePath(archives) != normalizePath(self.config.archives):
            self.config.archives = normalizePath(archives)
        if key is not None:
            self.config.nexusapikey = key
        super().accept()
