'''Missing requirements dialog'''

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
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

from src.core.requirements import DISABLED, DLC_MISSING, EXTERNAL, MISSING, MissingRequirement
from src.globals.constants import translate
from src.gui.themes import statusColor
from src.util.util import openUrl, plural

STATES = {
    MISSING: ("Not installed", "warning"),
    DISABLED: ("Disabled", "warning"),
    DLC_MISSING: ("Expansion missing", "warning"),
    EXTERNAL: ("Check yourself", "muted"),
}


class RequirementsDialog(QDialog):
    '''Lists requirements from Nexus mod pages that aren't met, and offers to install the missing Nexus mods'''

    def __init__(self, parent: QWidget | None, missing: list[MissingRequirement], heading: str = ''):
        super().__init__(parent)
        self.setWindowTitle(translate("Requirements", "Mod Requirements"))
        self.resize(1050, 420)
        self.missing = missing
        self.chosen: MissingRequirement | None = None

        layout = QVBoxLayout(self)
        checkable = sum(1 for entry in missing if entry.state != EXTERNAL)
        outside = len(missing) - checkable
        text = plural(checkable, translate("Requirements", "requirement")) + " " + translate("Requirements", "not met")
        if outside:
            text += (
                ", "
                + plural(outside, translate("Requirements", "tool"))
                + " "
                + translate("Requirements", "from outside Nexus to check yourself")
            )
        self.heading = QLabel(
            (heading + " " if heading else "")
            + text
            + ".\n"
            + translate(
                "Requirements",
                "These come from each mod's Nexus page. Double-click a row to open the required mod's page.",
            ),
            self,
        )
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(
            [
                translate("Requirements", "Mod"),
                translate("Requirements", "Needs"),
                translate("Requirements", "Status"),
                translate("Requirements", "Notes"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.header().resizeSection(0, 260)
        self.tree.header().resizeSection(1, 260)
        for index, entry in enumerate(missing):
            label, color = STATES[entry.state]
            requirement = entry.requirement
            item = QTreeWidgetItem(
                [entry.mod, requirement.name, translate("Requirements", label), requirement.notes.strip()]
            )
            item.setForeground(2, QBrush(statusColor(color)))
            item.setToolTip(1, requirement.pageUrl)
            item.setToolTip(3, requirement.notes.strip())
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            self.tree.addTopLevelItem(item)
        self.tree.itemDoubleClicked.connect(lambda item: self.openPage(self.entry(item)))
        self.tree.currentItemChanged.connect(lambda *_: self.updateButtons())
        layout.addWidget(self.tree)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.installButton = buttons.addButton(
            translate("Requirements", "Install from Nexus"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.pageButton = buttons.addButton(
            translate("Requirements", "Open Page"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.installButton.clicked.connect(self.install)
        self.pageButton.clicked.connect(lambda: self.openPage(self.entry(self.tree.currentItem())))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        first = next((i for i, entry in enumerate(missing) if installable(entry)), 0)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(first))
        self.updateButtons()

    def entry(self, item: QTreeWidgetItem | None) -> MissingRequirement | None:
        if item is None:
            return None
        return self.missing[int(item.data(0, Qt.ItemDataRole.UserRole))]

    def updateButtons(self) -> None:
        entry = self.entry(self.tree.currentItem())
        self.installButton.setEnabled(entry is not None and installable(entry))
        self.pageButton.setEnabled(entry is not None and bool(entry.requirement.pageUrl))

    def openPage(self, entry: MissingRequirement | None) -> None:
        if entry is not None and entry.requirement.pageUrl:
            openUrl(entry.requirement.pageUrl)

    def install(self) -> None:
        entry = self.entry(self.tree.currentItem())
        if entry is not None and installable(entry):
            self.chosen = entry
            self.accept()


def installable(entry: MissingRequirement) -> bool:
    '''A missing Witcher 3 mod from Nexus can be downloaded and installed from here'''
    return entry.state == MISSING and bool(entry.requirement.modId)
