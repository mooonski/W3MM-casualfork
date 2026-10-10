'''Script conflict dialogs'''

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.scriptmerger import LEFTOVER, MERGED, NOT_MERGED, OUTDATED, ConflictReport, ScriptConflict
from src.globals.constants import translate

STATES = {
    NOT_MERGED: ("Not merged", "#e5a050", 0),
    OUTDATED: ("Merge out of date", "#e5a050", 1),
    LEFTOVER: ("Old merge", "#a0a0a0", 2),
    MERGED: ("Merged", "", 3),
}


def summary(conflicts: list[ScriptConflict], limit: int = 6) -> str:
    lines = [
        f"  {conflict.relativePath}  ({translate('ScriptMerger', STATES[conflict.state][0]).lower()})"
        for conflict in conflicts[:limit]
    ]
    if len(conflicts) > limit:
        lines.append(f"  ... {len(conflicts) - limit} " + translate("ScriptMerger", "more"))
    return "\n".join(lines)


def MessageAlertScriptConflicts(pending: list[ScriptConflict], scriptMergerFound: bool) -> QMessageBox.StandardButton:
    '''Asks to run Script Merger because some scripts need (re)merging'''
    text = (
        f"{len(pending)} "
        + translate("ScriptMerger", "script files need Script Merger:")
        + "\n\n"
        + summary(pending)
        + "\n\n"
        + (
            translate("ScriptMerger", "Do you want to run it now?")
            if scriptMergerFound
            else translate("ScriptMerger", "Script Merger wasn't found. Do you want to choose where it is?")
        )
        + "\n\n"
        + translate("ScriptMerger", "Note: You can turn these alerts off in the settings.")
    )
    return QMessageBox.question(
        None,
        translate("ScriptMerger", "Run Script Merger"),
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )


class ScriptConflictsDialog(QDialog):
    '''Lists scripts changed by more than one mod and whether Script Merger has merged them'''

    def __init__(self, parent: QWidget | None, report: ConflictReport, scriptMerger: str, version: str):
        super().__init__(parent)
        self.setWindowTitle(translate("ScriptMerger", "Script Conflicts"))
        self.resize(1100, 520)
        layout = QVBoxLayout(self)

        if scriptMerger:
            about = (
                translate("ScriptMerger", "Script Merger") + (f" {version}" if version else "") + f": {scriptMerger}"
            )
            if not report.inventoryFound:
                about += "\n" + translate(
                    "ScriptMerger", "It hasn't merged anything yet, so every conflict is shown as not merged."
                )
        else:
            about = translate(
                "ScriptMerger",
                "Script Merger wasn't found, so merges can't be checked. Set it under Settings > Change Setting.",
            )
        pending = len(report.pending)
        conflicts = sum(1 for conflict in report.conflicts if conflict.state != LEFTOVER)
        heading = QLabel(
            about
            + "\n\n"
            + (
                f"{conflicts} " + translate("ScriptMerger", "script conflicts") + f", {pending} "
                + translate("ScriptMerger", "files need Script Merger.")
                if report.conflicts
                else translate("ScriptMerger", "No scripts are changed by more than one enabled mod.")
            )
        )  # fmt: skip
        heading.setWordWrap(True)
        layout.addWidget(heading)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(
            [
                translate("ScriptMerger", "Script"),
                translate("ScriptMerger", "Mods"),
                translate("ScriptMerger", "Status"),
                translate("ScriptMerger", "Details"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.header().resizeSection(1, 320)
        layout.addWidget(self.tree)
        for conflict in sorted(report.conflicts, key=lambda c: (STATES[c.state][2], c.relativePath.lower())):
            label, color, _ = STATES[conflict.state]
            item = QTreeWidgetItem(
                [conflict.relativePath, ", ".join(conflict.mods), translate("ScriptMerger", label), conflict.detail]
            )
            item.setToolTip(1, "\n".join(conflict.mods))
            if color:
                item.setForeground(2, QBrush(QColor(color)))
            self.tree.addTopLevelItem(item)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.runButton = buttons.addButton(
            translate("ScriptMerger", "Run Script Merger"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.runButton.setDefault(bool(pending))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.tree.setFocus(Qt.FocusReason.OtherFocusReason)
