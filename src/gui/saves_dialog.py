'''Game save backups dialog'''

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.saves import NEWEST, SAVES_FOLDER, SaveBackup
from src.globals.constants import translate
from src.gui.archives_dialog import sizeText
from src.util.util import openFolder, plural


class SaveBackupsDialog(QDialog):
    '''Lists the copies of game saves made before updates, and can make one now or copy one back'''

    def __init__(self, parent: QWidget | None, backups: list[SaveBackup], folder: str):
        super().__init__(parent)
        self.setWindowTitle(translate("Saves", "Game Save Backups"))
        self.resize(760, 440)
        self.folder = folder
        self.backups: list[SaveBackup] = []
        self.chosen: SaveBackup | None = None
        self.backUpNow = lambda: None  # set by the owner

        layout = QVBoxLayout(self)
        note = QLabel(
            translate("Saves", "Before updating mods, your")
            + f" {NEWEST} "
            + translate("Saves", "newest saves are copied to")
            + f" {folder}/{SAVES_FOLDER}.\n"
            + translate(
                "Saves",
                "Restoring copies a backup's saves back into the game's save folder, next to your other saves; "
                "it never replaces a save that is there.",
            ),
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(
            [
                translate("Saves", "Made"),
                translate("Saves", "Why"),
                translate("Saves", "Saves"),
                translate("Saves", "Size"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.currentItemChanged.connect(lambda *_: self.updateButtons())
        layout.addWidget(self.tree)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.restoreButton = buttons.addButton(
            translate("Saves", "Copy Back to the Game"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.backupButton = buttons.addButton(translate("Saves", "Back Up Now"), QDialogButtonBox.ButtonRole.ActionRole)
        self.folderButton = buttons.addButton(translate("Saves", "Open Folder"), QDialogButtonBox.ButtonRole.ActionRole)
        self.restoreButton.clicked.connect(self.restore)
        self.backupButton.clicked.connect(lambda: self.backUpNow())
        self.folderButton.clicked.connect(self.openSelected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.showBackups(backups)

    def showBackups(self, backups: list[SaveBackup]) -> None:
        self.backups = backups
        self.tree.clear()
        for index, backup in enumerate(backups):
            reason = {
                "before update": translate("Saves", "Before updating mods"),
                "manual": translate("Saves", "Backed up by hand"),
            }.get(backup.reason, backup.reason)
            item = QTreeWidgetItem([backup.created, reason, str(backup.count), sizeText(backup.size)])
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            item.setToolTip(0, backup.directory)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tree.addTopLevelItem(item)
        if backups:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.setWindowTitle(
            translate("Saves", "Game Save Backups") + f" ({plural(len(backups), translate('Saves', 'backup'))})"
        )
        self.updateButtons()

    def current(self) -> SaveBackup | None:
        item = self.tree.currentItem()
        return self.backups[int(item.data(0, Qt.ItemDataRole.UserRole))] if item is not None else None

    def updateButtons(self) -> None:
        self.restoreButton.setEnabled(self.current() is not None)

    def openSelected(self) -> None:
        backup = self.current()
        openFolder(backup.directory if backup is not None else f"{self.folder}/{SAVES_FOLDER}")

    def restore(self) -> None:
        backup = self.current()
        if backup is not None:
            self.chosen = backup
            self.accept()
