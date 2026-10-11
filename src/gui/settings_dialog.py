'''Game Settings window: the game's user settings (editable) and key bindings, with what comes from which mod'''

import os
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.gamesettings import (
    MenuOption,
    SettingsFile,
    backupSettingsFile,
    gameRunning,
    modActions,
    modMenuOptions,
    readBindings,
    settingOwners,
)
from src.globals.constants import translate
from src.gui.themes import statusColor
from src.util.util import openTextFile, plural

USER_TAB = 0
KEYS_TAB = 1
ROW = Qt.ItemDataRole.UserRole


class GameSettingsDialog(QDialog):
    '''Shows user.settings as a table that can be searched and edited, and input.settings as a table of key
    bindings. Changes are saved only on Save, after a backup copy of the file.'''

    def __init__(
        self,
        parent: QWidget | None,
        settingsFolder: str,
        userFile: str,
        menuFolder: str,
        mods: list,
        backups: str,
        tab: int = USER_TAB,
        output: Callable[[str], None] = lambda text: None,
        running: Callable[[], bool] = gameRunning,
    ):
        super().__init__(parent)
        self.setWindowTitle(translate("GameSettings", "Game Settings"))
        self.resize(1150, 680)
        self.folder = settingsFolder
        self.backups = backups
        self.output = output
        self.running = running
        self.userPath = os.path.join(settingsFolder, userFile)
        # mods write their settings to both files; the game reads the one for its graphics mode
        other = 'user.settings' if userFile.casefold() == 'dx12user.settings' else 'dx12user.settings'
        self.otherPath = os.path.join(settingsFolder, other)
        self.inputPath = os.path.join(settingsFolder, 'input.settings')
        self.options = modMenuOptions(menuFolder, mods)
        self.keyOwners, self.sectionOwners = settingOwners(mods, self.options)
        self.actions = modActions(mods)
        self.changes: dict[int, str] = {}  # line in the file -> new value
        self.filling = False

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.userTab(), translate("GameSettings", "User Settings"))
        self.tabs.addTab(self.keysTab(), translate("GameSettings", "Key Bindings"))
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.loadUser()
        self.loadKeys()
        self.tabs.setCurrentIndex(tab)

    # ------------------------------------------------------------ user settings

    def userTab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self.userHeading = QLabel(page)
        self.userHeading.setWordWrap(True)
        layout.addWidget(self.userHeading)
        row = QHBoxLayout()
        self.userSearch = QLineEdit(page)
        self.userSearch.setPlaceholderText(translate("GameSettings", "Search sections, settings, values or mods"))
        self.userSearch.setClearButtonEnabled(True)
        self.onlyModSettings = QCheckBox(translate("GameSettings", "Only settings from my mods"), page)
        row.addWidget(self.userSearch, 1)
        row.addWidget(self.onlyModSettings)
        layout.addLayout(row)
        self.userTree = QTreeWidget(page)
        self.userTree.setColumnCount(5)
        self.userTree.setHeaderLabels(
            [
                translate("GameSettings", "Section"),
                translate("GameSettings", "Setting"),
                translate("GameSettings", "Value"),
                translate("GameSettings", "Allowed"),
                translate("GameSettings", "Mod"),
            ]
        )
        self.userTree.setRootIsDecorated(False)
        self.userTree.setSortingEnabled(True)
        # only the value can be changed, whichever column is double-clicked
        self.userTree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.userTree.itemDoubleClicked.connect(lambda item, column: self.userTree.editItem(item, 2))
        header = self.userTree.header()
        for column, width in ((0, 230), (1, 260), (2, 120), (3, 220)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(column, width)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.userTree.itemChanged.connect(self.valueEdited)
        layout.addWidget(self.userTree)
        bottom = QHBoxLayout()
        self.userStatus = QLabel(page)
        self.saveButton = QPushButton(translate("GameSettings", "Save"), page)
        self.undoButton = QPushButton(translate("GameSettings", "Undo Changes"), page)
        notepad = QPushButton(translate("GameSettings", "Open in Notepad"), page)
        self.saveButton.clicked.connect(self.save)
        self.undoButton.clicked.connect(self.undo)
        notepad.clicked.connect(lambda: openTextFile(self.userPath))
        bottom.addWidget(self.userStatus, 1)
        for button in (notepad, self.undoButton, self.saveButton):
            bottom.addWidget(button)
        layout.addLayout(bottom)
        self.userTimer = QTimer(self)
        self.userTimer.setSingleShot(True)
        self.userTimer.timeout.connect(self.filterUser)
        self.userSearch.textChanged.connect(lambda: self.userTimer.start(150))
        self.onlyModSettings.toggled.connect(self.filterUser)
        return page

    def owner(self, section: str, key: str) -> str:
        return self.keyOwners.get((section.casefold(), key.casefold())) or self.sectionOwners.get(
            section.casefold(), ''
        )

    def option(self, section: str, key: str) -> MenuOption | None:
        return self.options.get((section.casefold(), key.casefold()))

    def loadUser(self) -> None:
        self.user = SettingsFile(self.userPath)
        self.changes = {}
        self.filling = True
        self.userTree.setSortingEnabled(False)
        self.userTree.clear()
        fromMods = 0
        for setting in self.user.settings:
            option = self.option(setting.section, setting.key)
            mod = self.owner(setting.section, setting.key)
            fromMods += bool(mod)
            item = QTreeWidgetItem(
                [setting.section, setting.key, setting.value, option.allowed() if option else '', mod]
            )
            item.setData(0, ROW, setting.line)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            item.setToolTip(2, translate("GameSettings", "Double-click to change"))
            self.userTree.addTopLevelItem(item)
        self.userTree.setSortingEnabled(True)
        self.userTree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.filling = False
        exists = os.path.isfile(self.userPath)
        self.userHeading.setText(
            (
                f"{self.userPath}: "
                + plural(len(self.user.settings), translate("GameSettings", "setting"))
                + f", {fromMods} "
                + translate("GameSettings", "from your mods")
                + ". "
                + translate(
                    "GameSettings", "Double-click a value to change it; nothing is written until you click Save."
                )
            )
            if exists
            else translate("GameSettings", "The game hasn't written its settings file yet:") + f" {self.userPath}"
        )
        self.filterUser()
        self.updateUserStatus()

    def filterUser(self) -> None:
        words = self.userSearch.text().casefold().split()
        onlyMods = self.onlyModSettings.isChecked()
        for index in range(self.userTree.topLevelItemCount()):
            item = self.userTree.topLevelItem(index)
            text = ' '.join(item.text(column) for column in range(5)).casefold()
            hidden = (onlyMods and not item.text(4)) or not all(word in text for word in words)
            item.setHidden(hidden)

    def valueEdited(self, item: QTreeWidgetItem, column: int) -> None:
        if self.filling or column != 2:
            return
        line = int(item.data(0, ROW))
        setting = next(setting for setting in self.user.settings if setting.line == line)
        value = item.text(2).strip()
        self.filling = True
        try:
            if value == setting.value:
                self.changes.pop(line, None)
            else:
                self.changes[line] = value
            font = item.font(2)
            font.setBold(line in self.changes)
            item.setFont(2, font)
            option = self.option(setting.section, setting.key)
            problem = option.problem(value) if option is not None and line in self.changes else ''
            item.setForeground(2, QBrush(statusColor("problem")) if problem else QBrush())
            item.setToolTip(2, problem or translate("GameSettings", "Double-click to change"))
        finally:
            self.filling = False
        self.updateUserStatus()

    def problems(self) -> list[str]:
        found = []
        for setting in self.user.settings:
            if setting.line in self.changes:
                option = self.option(setting.section, setting.key)
                problem = option.problem(self.changes[setting.line]) if option else ''
                if problem:
                    found.append(f"[{setting.section}] {setting.key}={self.changes[setting.line]}: {problem}")
        return found

    def updateUserStatus(self) -> None:
        count = len(self.changes)
        self.userStatus.setText(
            plural(count, translate("GameSettings", "unsaved change"))
            if count
            else translate("GameSettings", "No changes")
        )
        self.saveButton.setEnabled(bool(count))
        self.undoButton.setEnabled(bool(count))

    def undo(self) -> None:
        self.loadUser()

    def save(self) -> bool:
        '''Writes the changed values (after a backup copy), also into the other user settings file where it has
        the same setting. Returns False if nothing was saved.'''
        if not self.changes:
            return True
        problems = self.problems()
        if problems:
            answer = QMessageBox.question(
                self,
                translate("GameSettings", "Save settings"),
                translate("GameSettings", "These values aren't ones the mod's menu offers:")
                + "\n\n"
                + "\n".join(problems)
                + "\n\n"
                + translate("GameSettings", "Save them anyway?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        if self.running():
            answer = QMessageBox.warning(
                self,
                translate("GameSettings", "The Witcher 3 is running"),
                translate(
                    "GameSettings",
                    "The game writes its settings when it closes, which would undo these changes. "
                    "Close the game first, then save.\n\nSave anyway?",
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        try:
            backup = backupSettingsFile(self.userPath, self.backups)
            other = SettingsFile(self.otherPath) if os.path.isfile(self.otherPath) else None
            mirrored = 0
            for setting in list(self.user.settings):
                if setting.line not in self.changes:
                    continue
                value = self.changes[setting.line]
                self.user.change(setting, value)
                twin = other.find(setting.section, setting.key) if other is not None else None
                if twin is not None and twin.value != value:
                    other.change(twin, value)
                    mirrored += 1
            self.user.save()
            if other is not None and mirrored:
                backupSettingsFile(self.otherPath, self.backups)
                other.save()
        except OSError as error:
            QMessageBox.warning(self, translate("GameSettings", "Save settings"), str(error))
            return False
        self.output(
            translate("GameSettings", "Saved")
            + f" {plural(len(self.changes), translate('GameSettings', 'setting'))} "
            + translate("GameSettings", "to")
            + f" {os.path.basename(self.userPath)}"
            + (f" ({translate('GameSettings', 'also in')} {os.path.basename(self.otherPath)})" if mirrored else "")
            + f"; {translate('GameSettings', 'the old file is in')} {backup}"
        )
        self.loadUser()
        return True

    # ------------------------------------------------------------ key bindings

    def keysTab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self.keysHeading = QLabel(page)
        self.keysHeading.setWordWrap(True)
        layout.addWidget(self.keysHeading)
        row = QHBoxLayout()
        self.keySearch = QLineEdit(page)
        self.keySearch.setPlaceholderText(translate("GameSettings", "Search keys, actions, contexts or mods"))
        self.keySearch.setClearButtonEnabled(True)
        self.onlyModKeys = QCheckBox(translate("GameSettings", "Only keys from my mods"), page)
        self.onlyClashes = QCheckBox(translate("GameSettings", "Only clashes between mods"), page)
        row.addWidget(self.keySearch, 1)
        row.addWidget(self.onlyModKeys)
        row.addWidget(self.onlyClashes)
        layout.addLayout(row)
        self.keyTree = QTreeWidget(page)
        self.keyTree.setColumnCount(5)
        self.keyTree.setHeaderLabels(
            [
                translate("GameSettings", "Context"),
                translate("GameSettings", "Key"),
                translate("GameSettings", "Action"),
                translate("GameSettings", "Also on this key"),
                translate("GameSettings", "Mod"),
            ]
        )
        self.keyTree.setRootIsDecorated(False)
        self.keyTree.setSortingEnabled(True)
        header = self.keyTree.header()
        for column, width in ((0, 200), (1, 140), (2, 250), (3, 260)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(column, width)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.keyTree)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        notepad = QPushButton(translate("GameSettings", "Open in Notepad"), page)
        notepad.clicked.connect(lambda: openTextFile(self.inputPath))
        bottom.addWidget(notepad)
        layout.addLayout(bottom)
        self.keyTimer = QTimer(self)
        self.keyTimer.setSingleShot(True)
        self.keyTimer.timeout.connect(self.filterKeys)
        self.keySearch.textChanged.connect(lambda: self.keyTimer.start(150))
        self.onlyModKeys.toggled.connect(self.filterKeys)
        self.onlyClashes.toggled.connect(self.filterKeys)
        return page

    def loadKeys(self) -> None:
        self.bindings = readBindings(SettingsFile(self.inputPath), self.actions)
        self.keyTree.setSortingEnabled(False)
        self.keyTree.clear()
        for index, binding in enumerate(self.bindings):
            item = QTreeWidgetItem(
                [binding.context, binding.key, binding.action, ', '.join(binding.others), binding.mod]
            )
            item.setData(0, ROW, index)
            item.setToolTip(2, binding.details)
            if binding.clash:
                for column in range(5):
                    item.setForeground(column, QBrush(statusColor("warning")))
                item.setToolTip(3, translate("GameSettings", "Another mod uses this key here the same way"))
            self.keyTree.addTopLevelItem(item)
        self.keyTree.setSortingEnabled(True)
        self.keyTree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        clashes = sum(1 for binding in self.bindings if binding.clash)
        fromMods = sum(1 for binding in self.bindings if binding.mod)
        self.keysHeading.setText(
            f"{self.inputPath}: "
            + plural(len(self.bindings), translate("GameSettings", "binding"))
            + f", {fromMods} "
            + translate("GameSettings", "from your mods")
            + ", "
            + plural(clashes, translate("GameSettings", "clash"), translate("GameSettings", "clashes"))
            + " "
            + translate("GameSettings", "between mods")
            + ". "
            + translate(
                "GameSettings",
                "Change keys in the game's own Key Bindings menu; mods often share a key with the game on purpose "
                "(press for one thing, hold for another).",
            )
        )
        self.filterKeys()

    def filterKeys(self) -> None:
        words = self.keySearch.text().casefold().split()
        onlyMods = self.onlyModKeys.isChecked()
        onlyClashes = self.onlyClashes.isChecked()
        for index in range(self.keyTree.topLevelItemCount()):
            item = self.keyTree.topLevelItem(index)
            binding = self.bindings[int(item.data(0, ROW))]
            text = ' '.join(item.text(column) for column in range(5)).casefold()
            hidden = (
                (onlyMods and not binding.mod)
                or (onlyClashes and not binding.clash)
                or not all(word in text for word in words)
            )
            item.setHidden(hidden)

    # ------------------------------------------------------------ closing

    def reject(self) -> None:
        if self.changes:
            answer = QMessageBox.question(
                self,
                translate("GameSettings", "Game Settings"),
                plural(len(self.changes), translate("GameSettings", "unsaved change"))
                + ". "
                + translate("GameSettings", "Save before closing?"),
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Save and not self.save():
                return
        super().reject()
