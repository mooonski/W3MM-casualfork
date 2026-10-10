'''Nexus dialogs and background worker'''

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.nexus import FAILED, UP_TO_DATE, UPDATE_AVAILABLE, NexusClient, NexusError, NexusFile, UpdateCheck
from src.core.updater import Backup
from src.globals import data
from src.globals.constants import translate


class NexusWorker(QThread):
    '''Runs a function off the GUI thread. The function receives a progress callback (done, total).'''

    succeeded = Signal(object)
    failed = Signal(str)
    progressed = Signal(int, int)

    def __init__(self, function: Callable[[Callable[[int, int], None]], Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.function = function
        self.cancelled = False

    def run(self):
        try:
            result = self.function(lambda done, total: self.progressed.emit(int(done), int(total)))
        except NexusError as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
        else:
            self.succeeded.emit(result)


def askApiKey(parent: QWidget | None = None) -> str:
    '''Asks for a Nexus API key, checks it and stores it encrypted. Returns the key or "".'''
    current = data.getConfig().nexusapikey
    while True:
        key, ok = QInputDialog.getText(
            parent,
            translate("Nexus", "Nexus API Key"),
            translate("Nexus", "Paste your personal API key from")
            + "\nhttps://next.nexusmods.com/settings/api-keys\n\n"
            + translate("Nexus", "It is stored encrypted for your Windows account only."),
            QLineEdit.EchoMode.Password,
            current,
        )
        if not ok:
            return ''
        key = key.strip()
        if not key:
            data.getConfig().nexusapikey = ''
            return ''
        try:
            user = NexusClient(key).validate()
        except NexusError as error:
            QMessageBox.warning(parent, translate("Nexus", "Nexus API Key"), str(error))
            current = key
            continue
        data.getConfig().nexusapikey = key
        premium = translate("Nexus", "Premium") if user.get('is_premium') else translate("Nexus", "Free")
        QMessageBox.information(
            parent,
            translate("Nexus", "Nexus API Key"),
            translate("Nexus", "Signed in as") + f" {user.get('name', '?')} ({premium}).",
        )
        if not user.get('is_premium'):
            QMessageBox.information(
                parent,
                translate("Nexus", "Nexus API Key"),
                translate(
                    "Nexus",
                    "Checking for updates works with any account, but Nexus only allows direct downloads "
                    "for Premium members.",
                ),
            )
        return key


def statusText(check: UpdateCheck | None) -> str:
    if check is None:
        return ''
    if check.state == UPDATE_AVAILABLE:
        version = check.target.version if check.target else ''
        text = translate("Nexus", "Update") + (f" {version}" if version else '')
        return text + ('?' if check.needsChoice else '')
    if check.state == UP_TO_DATE:
        return translate("Nexus", "Up to date") + ('' if check.confident else '?')
    if check.state == FAILED:
        return translate("Nexus", "Error")
    return ''


@dataclass
class UpdateRow:
    name: str
    installedVersion: str
    check: UpdateCheck


class UpdatesDialog(QDialog):
    '''Lists update check results and lets the user pick what to update, and to which file'''

    def __init__(self, parent: QWidget | None, rows: list[UpdateRow]):
        super().__init__(parent)
        self.setWindowTitle(translate("Nexus", "Mod Updates"))
        self.resize(1200, 520)
        self.rows = rows
        self.combos: dict[str, QComboBox] = {}

        layout = QVBoxLayout(self)
        note = QLabel(
            translate(
                "Nexus",
                "Ticked mods are downloaded and installed into their existing entry. Each one is backed up "
                "first, and its priority, enabled state and your in-game settings are kept.\n"
                "A '?' means the installed file couldn't be identified exactly, so check the file it will update to.",
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(
            [
                translate("Nexus", "Mod"),
                translate("Nexus", "Installed"),
                translate("Nexus", "Update to"),
                translate("Nexus", "Status"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.header().resizeSection(0, 300)
        layout.addWidget(self.tree)

        for row in sorted(rows, key=lambda r: (r.check.state != UPDATE_AVAILABLE, r.name.lower())):
            check = row.check
            installed = row.installedVersion
            if not installed and check.installed:
                installed = check.installed.version
            item = QTreeWidgetItem([row.name, installed or '?', '', ''])
            item.setData(0, Qt.ItemDataRole.UserRole, row.name)
            self.tree.addTopLevelItem(item)
            if check.state == FAILED:
                item.setText(2, check.message)
                item.setText(3, translate("Nexus", "Error"))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                continue
            item.setCheckState(0, Qt.CheckState.Checked if check.state == UPDATE_AVAILABLE else Qt.CheckState.Unchecked)
            combo = QComboBox(self.tree)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            selected = 0
            for index, file in enumerate(check.candidates):
                combo.addItem(file.label(), file)
                if check.target and file.fileId == check.target.fileId:
                    selected = index
            combo.setCurrentIndex(selected)
            combo.currentIndexChanged.connect(lambda _, it=item: it.setCheckState(0, Qt.CheckState.Checked))
            self.tree.setItemWidget(item, 2, combo)
            self.combos[row.name] = combo
            if check.state == UPDATE_AVAILABLE:
                item.setText(
                    3,
                    translate("Nexus", "Check the file")
                    if check.needsChoice
                    else translate("Nexus", "Update available"),
                )
            else:
                item.setText(3, translate("Nexus", "Up to date") + ('' if check.confident else '?'))

        buttons = QDialogButtonBox(self)
        self.updateButton = buttons.addButton(
            translate("Nexus", "Update ticked"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def keyPressEvent(self, event):
        '''Space ticks or unticks all selected rows'''
        if event.key() == Qt.Key.Key_Space and self.tree.hasFocus():
            items = self.tree.selectedItems()
            checkable = [item for item in items if item.flags() & Qt.ItemFlag.ItemIsUserCheckable]
            if checkable:
                state = (
                    Qt.CheckState.Unchecked
                    if all(item.checkState(0) == Qt.CheckState.Checked for item in checkable)
                    else Qt.CheckState.Checked
                )
                for item in checkable:
                    item.setCheckState(0, state)
                return
        super().keyPressEvent(event)

    def selection(self) -> list[tuple[str, NexusFile]]:
        chosen = []
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item is None or item.checkState(0) != Qt.CheckState.Checked:
                continue
            name = item.data(0, Qt.ItemDataRole.UserRole)
            combo = self.combos.get(name)
            file = combo.currentData() if combo else None
            if isinstance(file, NexusFile):
                chosen.append((name, file))
        return chosen


class BackupsDialog(QDialog):
    '''Lets the user pick a backup of a mod to restore'''

    def __init__(self, parent: QWidget | None, name: str, backups: list[Backup]):
        super().__init__(parent)
        self.setWindowTitle(translate("Nexus", "Restore Previous Version") + f" - {name}")
        self.resize(560, 320)
        layout = QVBoxLayout(self)
        label = QLabel(
            translate(
                "Nexus",
                "Choose the version to put back. The version installed now is backed up first, "
                "so this can be undone the same way.",
            )
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        self.list = QListWidget(self)
        for backup in backups:
            item = QListWidgetItem(backup.label())
            item.setData(Qt.ItemDataRole.UserRole, backup)
            self.list.addItem(item)
        if backups:
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        buttons.addButton(translate("Nexus", "Restore"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> Backup | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None
