'''Dialog for a newer release of the manager: what's new, and the choice to install it now'''

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.core.selfupdate import ManagerRelease
from src.globals.constants import translate

INSTALL = "install"
OPEN_PAGE = "page"
LATER = "later"


class ManagerUpdateDialog(QDialog):
    '''Shows the release and sets self.action to INSTALL, OPEN_PAGE or LATER'''

    def __init__(
        self, parent: QWidget | None, release: ManagerRelease, current: str, installable: bool, autoCheck: bool
    ):
        super().__init__(parent)
        self.action = LATER
        self.setWindowTitle(translate("ManagerUpdate", "Manager Update"))
        self.resize(640, 480)
        layout = QVBoxLayout(self)

        heading = QLabel(
            translate("ManagerUpdate", "casual fork") + f" {release.version} "
            + translate("ManagerUpdate", "is available. You have") + f" {current}.",
            self,
        )  # fmt: skip
        heading.setWordWrap(True)
        layout.addWidget(heading)

        self.notes = QTextBrowser(self)
        self.notes.setOpenExternalLinks(True)
        if release.notes.strip():
            self.notes.setMarkdown(release.notes)
        else:
            self.notes.setPlainText(translate("ManagerUpdate", "No release notes."))
        layout.addWidget(self.notes, 1)

        how = QLabel(self)
        how.setWordWrap(True)
        how.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if installable:
            how.setText(
                translate(
                    "ManagerUpdate",
                    "Install downloads the installer, checks it against the hash in the release notes, closes the "
                    "manager and runs the update. The manager starts again when it's done. Your settings and mod "
                    "list are kept.",
                )
            )
        else:
            how.setText(
                translate(
                    "ManagerUpdate",
                    "This copy runs from source, so it can't update itself: pull the repository, or install the "
                    "release from its page.",
                )
            )
        layout.addWidget(how)

        self.autoCheck = QCheckBox(
            translate("ManagerUpdate", "Check for manager updates at startup (once a day)"), self
        )
        self.autoCheck.setChecked(autoCheck)
        layout.addWidget(self.autoCheck)

        buttons = QDialogButtonBox(self)
        if installable:
            self.installButton = self.button(buttons, translate("ManagerUpdate", "Install"), INSTALL)
            self.installButton.setDefault(True)
        self.pageButton = self.button(buttons, translate("ManagerUpdate", "Open Release Page"), OPEN_PAGE)
        self.laterButton = self.button(buttons, translate("ManagerUpdate", "Later"), LATER)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def button(self, buttons: QDialogButtonBox, text: str, action: str) -> QPushButton:
        button = buttons.addButton(text, QDialogButtonBox.ButtonRole.ActionRole)
        button.setAutoDefault(False)
        button.clicked.connect(lambda: self.choose(action))
        return button

    def choose(self, action: str) -> None:
        self.action = action
        if action == LATER:
            self.reject()
        else:
            self.accept()
