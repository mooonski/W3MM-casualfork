'''Nexus dialogs and background worker'''

import html
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QBrush
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
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.nexus import (
    FAILED,
    UP_TO_DATE,
    UPDATE_AVAILABLE,
    ModFiles,
    NexusClient,
    NexusError,
    NexusFile,
    UpdateCheck,
    changesBetween,
)
from src.core.rmmr import ADD, LINK, SKIP, ImportPlan
from src.core.updater import Backup
from src.globals import data
from src.globals.constants import translate
from src.gui.themes import statusColor
from src.util.util import plural


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
    url: str = ''


def changesHtml(check: UpdateCheck, target: NexusFile | None, url: str = '') -> str:
    '''What changed between the installed file and the target, as shown under the updates list'''
    logs = url + '?tab=logs' if url else ''
    if check.files is None or target is None:
        return ''
    if check.installed is not None and check.installed.fileId == target.fileId:
        return '<p>' + html.escape(translate("Nexus", "This is the installed file.")) + '</p>'
    changes = changesBetween(check.files, check.installed, target)
    parts = []
    if check.installed is None:
        parts.append(
            '<p><i>'
            + html.escape(translate("Nexus", "The installed file isn't known, so only the newest changes are shown."))
            + '</i></p>'
        )
    for version, lines in changes:
        heading = version if version.lower().startswith('v') or not version[:1].isdigit() else 'v' + version
        parts.append(f"<p><b>{html.escape(heading)}</b></p><ul>")
        parts.extend(f"<li>{linkify(line)}</li>" for line in lines)
        parts.append('</ul>')
    if not changes:
        parts.append(
            '<p>' + html.escape(translate("Nexus", "The author didn't write a changelog for this file.")) + '</p>'
        )
    if logs:
        parts.append(
            f'<p><a href="{html.escape(logs)}">' + html.escape(translate("Nexus", "All changes on Nexus")) + '</a></p>'
        )
    return ''.join(parts)


def linkify(line: str) -> str:
    '''Escapes a changelog line and makes web addresses clickable'''
    return re.sub(r'(https?://[^\s<]+)', r'<a href="\1">\1</a>', html.escape(line))


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

        self.changes = QTextBrowser(self)
        self.changes.setOpenExternalLinks(True)
        self.changes.setPlaceholderText(translate("Nexus", "Select a mod to see what changed."))
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.changes)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter)
        self.rowsByName = {row.name: row for row in rows}

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
            combo.currentIndexChanged.connect(lambda _, it=item: self.showChanges(it))
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

        self.tree.currentItemChanged.connect(lambda current, _: self.showChanges(current))
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def showChanges(self, item: QTreeWidgetItem | None) -> None:
        '''Shows the changelog for the row's chosen file, if that row is the current one'''
        if item is None or item is not self.tree.currentItem():
            return
        name = item.data(0, Qt.ItemDataRole.UserRole)
        row = self.rowsByName.get(name)
        combo = self.combos.get(name)
        target = combo.currentData() if combo else None
        if row is None or row.check.state == FAILED:
            self.changes.clear()
            return
        self.changes.setHtml(changesHtml(row.check, target if isinstance(target, NexusFile) else None, row.url))

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


class RmmrImportDialog(QDialog):
    '''Shows what an RMMR import will do and asks for confirmation'''

    ACTIONS = {ADD: ("Add", 0), LINK: ("Link Nexus ID", 1), SKIP: ("Skip", 2)}

    def __init__(self, parent: QWidget | None, plan: ImportPlan):
        super().__init__(parent)
        self.setWindowTitle(translate("Nexus", "Import from RMMR"))
        self.resize(1100, 600)
        layout = QVBoxLayout(self)

        for warning in plan.warnings:
            label = QLabel(warning)
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {statusColor('warning').name()}; font-weight: bold;")
            layout.addWidget(label)
        summary = QLabel(
            translate("Nexus", "RMMR's files are only read, never changed.")
            + "\n"
            + plural(plan.count(ADD), translate("Nexus", "mod"))
            + " "
            + translate("Nexus", "will be added")
            + f", {plan.count(LINK)} "
            + (
                translate("Nexus", "will get its Nexus ID")
                if plan.count(LINK) == 1
                else translate("Nexus", "will get their Nexus IDs")
            )
            + f", {plan.count(SKIP)} "
            + translate("Nexus", "skipped")
            + "."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(
            [
                translate("Nexus", "Mod"),
                translate("Nexus", "Action"),
                translate("Nexus", "Nexus"),
                translate("Nexus", "Details"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setWordWrap(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.header().resizeSection(0, 320)
        layout.addWidget(self.tree)

        for item in sorted(plan.items, key=lambda i: (self.ACTIONS[i.action][1], i.name.lower())):
            nexus = ''
            if item.nexus:
                nexus = str(item.nexus.modId)
                if item.nexus.version:
                    nexus += f"  v{item.nexus.version.lstrip('vV')}"
            details = "; ".join([item.reason] + item.notes)
            row = QTreeWidgetItem([item.name, translate("Nexus", self.ACTIONS[item.action][0]), nexus, details])
            row.setToolTip(3, "\n".join([item.reason] + item.notes))
            if item.nexus and item.nexus.archive:
                row.setToolTip(2, translate("Nexus", "Installed file:") + f" {item.nexus.archive}")
            self.tree.addTopLevelItem(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.importButton = buttons.addButton(translate("Nexus", "Import"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.importButton.setEnabled(bool(plan.count(ADD) or plan.count(LINK)))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


RECORD = "record"
SWITCH = "switch"


class NexusFileDialog(QDialog):
    '''Lets the user say which file of a Nexus mod they have installed, or switch to another one'''

    def __init__(self, parent: QWidget | None, name: str, files: ModFiles, installed: NexusFile | None, recorded: bool):
        super().__init__(parent)
        self.setWindowTitle(translate("Nexus", "Choose Nexus File") + f" - {name}")
        self.resize(1000, 480)
        self.choice = ''
        layout = QVBoxLayout(self)
        if installed is None:
            known = translate("Nexus", "Which file is installed isn't known yet.")
        elif recorded:
            known = translate("Nexus", "Recorded as installed:") + f" {installed.label()}"
        else:
            known = translate("Nexus", "Best guess at what's installed:") + f" {installed.label()}"
        note = QLabel(
            known
            + "\n"
            + translate(
                "Nexus",
                "'This Is What I Have' records the selected file, so update checks follow exactly that file. "
                "'Switch to This File' downloads it and installs it into this mod's entry, after a backup.",
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(
            [
                translate("Nexus", "File"),
                translate("Nexus", "Version"),
                translate("Nexus", "Category"),
                translate("Nexus", "Uploaded"),
                translate("Nexus", "Size"),
                translate("Nexus", "Installed"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 6):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree)

        current = sorted(files.currentFiles(), key=lambda f: f.uploaded, reverse=True)
        others = sorted((f for f in files.files.values() if not f.current), key=lambda f: f.uploaded, reverse=True)
        self.files = current + others
        selected = 0
        for index, file in enumerate(self.files):
            uploaded = time.strftime("%Y-%m-%d", time.gmtime(file.uploaded)) if file.uploaded else ''
            size = f"{file.size / 1048576:.1f} MB" if file.size >= 1048576 else f"{max(1, file.size // 1024)} KB"
            mark = ''
            if installed is not None and file.fileId == installed.fileId:
                mark = translate("Nexus", "Yes") if recorded else translate("Nexus", "Probably")
                selected = index
            item = QTreeWidgetItem([file.name, file.version, file.categoryName, uploaded, size, mark])
            item.setToolTip(0, file.description or file.fileName)
            if not file.current:
                for column in range(6):
                    item.setForeground(column, QBrush(statusColor("muted")))
            self.tree.addTopLevelItem(item)
        if self.files:
            self.tree.setCurrentItem(self.tree.topLevelItem(selected))
        self.tree.itemDoubleClicked.connect(self.recordClicked)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.recordButton = buttons.addButton(
            translate("Nexus", "This Is What I Have"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.switchButton = buttons.addButton(
            translate("Nexus", "Switch to This File"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.recordButton.clicked.connect(lambda: self.finish(RECORD))
        self.switchButton.clicked.connect(lambda: self.finish(SWITCH))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.recordButton.setEnabled(bool(self.files))
        self.switchButton.setEnabled(bool(self.files))

    def selected(self) -> NexusFile | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        return self.files[self.tree.indexOfTopLevelItem(item)]

    def recordClicked(self, item: QTreeWidgetItem) -> None:
        '''Double-clicking a file records it as the installed one'''
        self.tree.setCurrentItem(item)
        self.finish(RECORD)

    def finish(self, choice: str) -> None:
        if self.selected() is not None:
            self.choice = choice
            self.accept()
