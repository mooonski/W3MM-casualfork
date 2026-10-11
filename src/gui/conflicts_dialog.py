'''File conflicts dialog: which mod's files the game uses when several mods pack the same file'''

from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.conflicts import ConflictScan
from src.globals.constants import translate
from src.gui.themes import statusColor
from src.util.util import plural


class FileConflictsDialog(QDialog):
    '''Groups conflicting files by the mod that wins and the mod that loses them'''

    def __init__(self, parent: QWidget | None, scan: ConflictScan, names: dict[str, str]):
        super().__init__(parent)
        self.setWindowTitle(translate("Conflicts", "File Conflicts"))
        self.resize(1000, 540)
        self.names = names

        layout = QVBoxLayout(self)
        pairs = scan.pairs()
        merged = sum(1 for conflict in scan.conflicts if conflict.merged)
        if scan.conflicts:
            text = (
                plural(len(scan.conflicts), translate("Conflicts", "file"))
                + " "
                + translate("Conflicts", "packed by more than one mod")
                + (f", {merged} " + translate("Conflicts", "of them merged by Script Merger") if merged else "")
                + ". "
            )
        else:
            text = (
                translate("Conflicts", "No two mods pack the same file")
                + f" ({plural(scan.bundles, translate('Conflicts', 'bundle'))} "
                + translate("Conflicts", "checked")
                + "). "
            )
        self.heading = QLabel(
            text
            + "\n"
            + translate(
                "Conflicts",
                "The game uses each file from the mod that loads first: the lowest priority number, then A to Z. "
                "Change who wins by changing priorities in the load order on the right. "
                "Scripts are handled by Script Merger (Script Conflicts...).",
            ),
            self,
        )
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(
            [
                translate("Conflicts", "Used from"),
                translate("Conflicts", "Instead of"),
                translate("Conflicts", "Files"),
            ]
        )
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 380)
        header.resizeSection(1, 300)
        for (winner, loser), conflicts in sorted(pairs.items(), key=lambda pair: (-len(pair[1]), pair[0])):
            item = QTreeWidgetItem(
                [self.label(winner), self.label(loser), plural(len(conflicts), translate("Conflicts", "file"))]
            )
            item.setToolTip(0, winner)
            item.setToolTip(1, loser)
            for conflict in conflicts:
                note = ''
                if conflict.merged:
                    note = translate("Conflicts", "merged by Script Merger")
                elif conflict.mergeable:
                    note = translate("Conflicts", "Script Merger can merge this")
                child = QTreeWidgetItem([conflict.path, '', note])
                child.setToolTip(0, conflict.path)
                if note:
                    child.setForeground(2, QBrush(statusColor("muted")))
                item.addChild(child)
            self.tree.addTopLevelItem(item)
        if len(pairs) <= 5:
            self.tree.expandAll()
        layout.addWidget(self.tree)

        if scan.errors:
            errors = QLabel(translate("Conflicts", "Couldn't read:") + " " + "; ".join(scan.errors), self)
            errors.setWordWrap(True)
            palette = errors.palette()
            palette.setColor(errors.foregroundRole(), statusColor("warning"))
            errors.setPalette(palette)
            layout.addWidget(errors)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def label(self, folder: str) -> str:
        '''The mod name with its folder, e.g. "More Blood (modmoreBlood)"'''
        name = self.names.get(folder.casefold())
        return f"{name} ({folder})" if name and name.casefold() != folder.casefold() else folder

    def rows(self) -> list[tuple[str, str, str]]:
        return [
            (item.text(0), item.text(1), item.text(2))
            for item in (self.tree.topLevelItem(index) for index in range(self.tree.topLevelItemCount()))
            if item is not None
        ]
