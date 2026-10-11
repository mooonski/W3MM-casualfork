'''Health check dialog'''

from collections.abc import Callable

from PySide6.QtCore import Qt
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

from src.core.health import ERROR, INFO, WARNING, Issue
from src.globals.constants import translate
from src.gui.themes import statusColor
from src.util.util import openUrl, plural

SEVERITIES = {ERROR: ("Problem", "problem"), WARNING: ("Warning", "warning"), INFO: ("Note", "muted")}


class HealthDialog(QDialog):
    '''Lists problems found with the installed mods'''

    def __init__(
        self,
        parent: QWidget | None,
        issues: list[Issue],
        refresh: Callable[[], list[Issue]] | None = None,
        title: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(title or translate("Health", "Health Check"))
        self.resize(1100, 520)
        self.refreshIssues = refresh
        layout = QVBoxLayout(self)
        self.heading = QLabel(self)
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(
            [
                translate("Health", "Severity"),
                translate("Health", "Mod"),
                translate("Health", "Problem"),
                translate("Health", "Details"),
            ]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setWordWrap(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.header().resizeSection(1, 260)
        self.tree.itemDoubleClicked.connect(self.openLink)
        layout.addWidget(self.tree)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        if refresh is not None:
            self.refreshButton = buttons.addButton(
                translate("Health", "Check Again"), QDialogButtonBox.ButtonRole.ActionRole
            )
            self.refreshButton.clicked.connect(lambda: self.showIssues(refresh()))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.showIssues(issues)

    def showIssues(self, issues: list[Issue]) -> None:
        self.tree.clear()
        problems = sum(1 for issue in issues if issue.severity == ERROR)
        warnings = sum(1 for issue in issues if issue.severity == WARNING)
        notes = sum(1 for issue in issues if issue.severity == INFO)
        if not issues:
            self.heading.setText(translate("Health", "No problems found."))
        else:
            self.heading.setText(
                plural(problems, translate("Health", "problem")) + ", "
                + plural(warnings, translate("Health", "warning")) + ", "
                + plural(notes, translate("Health", "note")) + ". "
                + translate("Health", "Double-click a row with a link to open it.")
            )  # fmt: skip
        for issue in issues:
            label, color = SEVERITIES[issue.severity]
            item = QTreeWidgetItem([translate("Health", label), issue.mod, issue.problem, issue.detail])
            item.setForeground(0, QBrush(statusColor(color)))
            item.setToolTip(3, issue.detail + (f"\n{issue.url}" if issue.url else ""))
            item.setData(0, Qt.ItemDataRole.UserRole, issue.url)
            self.tree.addTopLevelItem(item)

    def openLink(self, item: QTreeWidgetItem) -> None:
        url = item.data(0, Qt.ItemDataRole.UserRole)
        if url:
            openUrl(url)
