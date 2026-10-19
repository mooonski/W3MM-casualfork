'''ReShade dialog: what's installed, turning it off and on, and updating it from reshade.me'''

import os
from collections.abc import Callable
from tempfile import TemporaryDirectory

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.gamesettings import gameRunning
from src.core.reshade import (
    HOME_PAGE,
    LatestReShade,
    ReShadeError,
    ReShadeInstall,
    download,
    findReShade,
    latestReShade,
    needsUpdate,
    setEnabled,
    updateReShade,
)
from src.globals.constants import translate
from src.gui.nexus_dialogs import NexusWorker
from src.gui.themes import statusColor
from src.util.util import openFolder, openUrl, plural


class ReShadeDialog(QDialog):
    '''Lists the ReShade DLLs in the game's bin folders, with buttons to turn them off and on and to update them'''

    def __init__(
        self,
        parent: QWidget | None,
        game: str,
        backups: str,
        output: Callable[[str], None] = lambda message: None,
        running: Callable[[], bool] = gameRunning,
        find: Callable[[str], list[ReShadeInstall]] = findReShade,
        latest: Callable[[], LatestReShade] = latestReShade,
    ):
        super().__init__(parent)
        self.setWindowTitle(translate("ReShade", "ReShade"))
        self.resize(900, 360)
        self.game, self.backups, self.output, self.running = game, backups, output, running
        self.find, self.fetchLatest = find, latest
        self.installs: list[ReShadeInstall] = []
        self.latest: LatestReShade | None = None
        self.checkError = ""
        self.worker: NexusWorker | None = None

        layout = QVBoxLayout(self)
        self.heading = QLabel(self)
        self.heading.setWordWrap(True)
        self.heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.heading)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(
            [
                translate("ReShade", "File"),
                translate("ReShade", "Version"),
                translate("ReShade", "Build"),
                translate("ReShade", "State"),
                translate("ReShade", "Add-ons that load through it"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        header = self.tree.header()
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tree.currentItemChanged.connect(lambda *_: self.updateButtons())
        layout.addWidget(self.tree)

        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.toggleButton = self.addButton(buttons, translate("ReShade", "Disable"), self.toggle)
        self.checkButton = self.addButton(buttons, translate("ReShade", "Check for Updates"), self.checkUpdates)
        self.updateButton = self.addButton(buttons, translate("ReShade", "Update"), self.update)
        self.pageButton = self.addButton(buttons, translate("ReShade", "Open reshade.me"), self.openPage)
        self.folderButton = self.addButton(buttons, translate("ReShade", "Open Folder"), self.openFolder)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def addButton(self, buttons: QDialogButtonBox, text: str, slot) -> QPushButton:
        button = buttons.addButton(text, QDialogButtonBox.ButtonRole.ActionRole)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        return button

    def selected(self) -> ReShadeInstall | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        index = self.tree.indexOfTopLevelItem(item)
        return self.installs[index] if 0 <= index < len(self.installs) else None

    def refresh(self) -> None:
        current = self.tree.indexOfTopLevelItem(self.tree.currentItem()) if self.tree.currentItem() else 0
        self.installs = self.find(self.game)
        self.tree.clear()
        for install in self.installs:
            state = translate("ReShade", "Enabled") if install.enabled else translate("ReShade", "Disabled")
            if install.duplicate:
                state += " " + translate("ReShade", "(a .disabled copy is left over)")
            item = QTreeWidgetItem(
                [
                    f"bin/{install.binFolder}/{os.path.basename(install.path)}",
                    install.version or "?",
                    translate("ReShade", install.buildName),
                    state,
                    ", ".join(install.addons) or translate("ReShade", "none"),
                ]
            )
            item.setToolTip(0, install.path)
            item.setToolTip(4, "\n".join(install.addons))
            if not install.enabled or install.duplicate:
                item.setForeground(3, QBrush(statusColor("warning" if install.duplicate else "muted")))
            if self.latest is not None and needsUpdate(install, self.latest):
                item.setForeground(1, QBrush(statusColor("warning")))
            self.tree.addTopLevelItem(item)
        if self.installs:
            self.tree.setCurrentItem(self.tree.topLevelItem(min(current, len(self.installs) - 1)))
            self.heading.setText(
                translate("ReShade", "ReShade loads into the game through these files. Disable renames the file to")
                + " .disabled; "
                + translate("ReShade", "its settings, presets and shaders stay where they are.")
            )
        else:
            self.heading.setText(
                translate("ReShade", "ReShade isn't installed in")
                + f" {os.path.join(self.game, 'bin')} "
                + translate("ReShade", "(x64 or x64_dx12). Install it with its setup program from reshade.me.")
            )
        self.showStatus()
        self.updateButtons()

    def showStatus(self) -> None:
        if self.checkError:
            self.status.setText(translate("ReShade", "Couldn't check for updates:") + f" {self.checkError}")
        elif self.latest is None:
            self.status.setText("")
        elif any(needsUpdate(install, self.latest) for install in self.installs):
            self.status.setText(
                translate("ReShade", "ReShade") + f" {self.latest.version} "
                + translate("ReShade", "is available on reshade.me. Update installs its DLL over yours, under "
                                       "the same name and build, after backing up the old one.")
            )  # fmt: skip
        else:
            self.status.setText(
                translate("ReShade", "Up to date: the newest version on reshade.me is") + f" {self.latest.version}."
            )

    def updateButtons(self) -> None:
        install = self.selected()
        busy = self.worker is not None and self.worker.isRunning()
        self.toggleButton.setEnabled(install is not None and not busy)
        self.toggleButton.setText(
            translate("ReShade", "Enable") if install is not None and not install.enabled
            else translate("ReShade", "Disable")
        )  # fmt: skip
        self.checkButton.setEnabled(not busy)
        self.updateButton.setEnabled(
            not busy and install is not None and self.latest is not None and needsUpdate(install, self.latest)
        )
        self.folderButton.setEnabled(install is not None)

    def toggle(self) -> None:
        install = self.selected()
        if install is None:
            return
        if install.enabled and install.addons:
            answer = QMessageBox.question(
                self,
                translate("ReShade", "Disable ReShade"),
                translate("ReShade", "These add-ons only load through ReShade, so they'll be off too until you "
                                     "enable it again:")
                + "\n\n  " + "\n  ".join(install.addons)
                + "\n\n" + translate("ReShade", "Disable it?"),
            )  # fmt: skip
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            setEnabled(install, not install.enabled, self.running)
        except ReShadeError as error:
            QMessageBox.warning(self, translate("ReShade", "ReShade"), str(error))
            return
        self.output(
            translate("ReShade", "ReShade") + f" (bin/{install.binFolder}) "
            + (translate("ReShade", "enabled") if install.enabled else translate("ReShade", "disabled"))
            + (": " + plural(len(install.addons), translate("ReShade", "add-on")) + " "
               + translate("ReShade", "now load again") if install.enabled and install.addons else "")
        )  # fmt: skip
        self.refresh()

    def runWorker(self, work, done) -> None:
        worker = NexusWorker(work, self)
        worker.succeeded.connect(done)
        worker.failed.connect(self.workFailed)
        worker.finished.connect(self.updateButtons)
        self.worker = worker
        worker.start()
        self.updateButtons()

    def workFailed(self, message: str) -> None:
        self.checkError = message
        self.showStatus()

    def checkUpdates(self) -> None:
        self.checkError = ""
        self.status.setText(translate("ReShade", "Checking reshade.me..."))
        self.runWorker(lambda progress: self.fetchLatest(), self.checked)

    def checked(self, latest: LatestReShade) -> None:
        self.latest = latest
        self.output(
            translate("ReShade", "Newest ReShade on reshade.me:") + f" {latest.version}"
            + "".join(f"; bin/{install.binFolder} has {install.version or '?'}" for install in self.installs)
        )  # fmt: skip
        self.refresh()

    def update(self) -> None:
        install, latest = self.selected(), self.latest
        if install is None or latest is None:
            return
        if self.running():
            QMessageBox.warning(self, translate("ReShade", "ReShade"), translate("ReShade", "Close the game first."))
            return
        url = latest.url(install.build)
        backups, running = self.backups, self.running

        def work(progress):
            with TemporaryDirectory(prefix="tw3mm-reshade-") as folder:
                setup = download(url, folder, progress)
                try:
                    return updateReShade(install, setup, backups, running)
                except ReShadeError as error:
                    return str(error)

        self.status.setText(translate("ReShade", "Downloading") + f" {url}...")
        self.runWorker(work, lambda result: self.updated(install, url, result))

    def updated(self, install: ReShadeInstall, url: str, result) -> None:
        if isinstance(result, str):
            self.output(translate("ReShade", "ReShade update failed:") + f" {result}")
            answer = QMessageBox.warning(
                self,
                translate("ReShade", "ReShade update failed"),
                result + "\n\n" + translate("ReShade", "Open the download page to install it with its setup?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                openUrl(HOME_PAGE)
            self.refresh()
            return
        self.output(
            translate("ReShade", "ReShade updated") + f" {result.previous or '?'} -> {result.version or '?'} "
            + f"(bin/{install.binFolder}). " + translate("ReShade", "The old DLL is backed up in") + f" {result.backup}"
        )  # fmt: skip
        self.refresh()

    def openPage(self) -> None:
        openUrl(HOME_PAGE)

    def openFolder(self) -> None:
        install = self.selected()
        if install is not None:
            openFolder(install.folder)

    def done(self, result: int) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(30000)
        super().done(result)
