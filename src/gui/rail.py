'''The column of section buttons down the left of the window: pages (Mods, Not Installed, Nexus) and places
that open something (Load Order, Health, Saves, Tools, Settings)'''

from collections.abc import Callable

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QButtonGroup, QFrame, QMenu, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from src.gui.icons import badgedIcon

BUTTON_SIZE = QSize(70, 58)


class SectionRail(QFrame):
    pageChosen = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("rail")
        self.setFixedWidth(BUTTON_SIZE.width() + 14)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(7, 10, 7, 10)
        self.layout_.setSpacing(4)
        self.layout_.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.buttons: dict[str, QToolButton] = {}
        self.icons: dict[str, str] = {}
        self.badges: dict[str, tuple[int, QColor]] = {}
        self.pages = QButtonGroup(self)
        self.pages.setExclusive(True)

    def button(self, key: str, label: str, iconName: str, tip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName(f"rail_{key}")
        button.setProperty("rail", True)
        button.setText(label)
        button.setToolTip(tip or label)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setFixedSize(BUTTON_SIZE)
        button.setIconSize(QSize(20, 20))
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.buttons[key] = button
        self.icons[key] = iconName
        self.layout_.addWidget(button)
        self.showIcon(key)
        return button

    def addPage(self, key: str, label: str, iconName: str, tip: str = '') -> QToolButton:
        '''A button that shows one of the window's pages'''
        button = self.button(key, label, iconName, tip)
        button.setCheckable(True)
        self.pages.addButton(button)
        button.clicked.connect(lambda: self.pageChosen.emit(key))
        return button

    def addPlace(self, key: str, label: str, iconName: str, tip: str, opened: Callable[[], None]) -> QToolButton:
        '''A button that opens something, e.g. a dialog'''
        button = self.button(key, label, iconName, tip)
        button.clicked.connect(lambda: opened())
        return button

    def addMenu(self, key: str, label: str, iconName: str, tip: str, menu: QMenu) -> QToolButton:
        '''A button whose menu opens to its right'''
        button = self.button(key, label, iconName, tip)
        button.clicked.connect(lambda: menu.exec(button.mapToGlobal(QPoint(button.width() + 4, 0))))
        return button

    def addStretch(self) -> None:
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.layout_.addWidget(spacer, 1)

    def setCurrent(self, key: str) -> None:
        if key in self.buttons and self.buttons[key].isCheckable():
            self.buttons[key].setChecked(True)

    def current(self) -> str:
        for key, button in self.buttons.items():
            if button.isCheckable() and button.isChecked():
                return key
        return ''

    def setBadge(self, key: str, count: int, color: QColor) -> None:
        '''A count at the corner of a button's icon, e.g. problems found; none for 0'''
        if self.badges.get(key, (0, None))[0] == count and count == 0:
            return
        self.badges[key] = (count, color)
        if key in self.buttons:
            self.showIcon(key)

    def showIcon(self, key: str) -> None:
        count, color = self.badges.get(key, (0, QColor()))
        self.buttons[key].setIcon(badgedIcon(self.icons[key], count, color))

    def applyIcons(self) -> None:
        '''Draws the icons again, in the current theme's colours'''
        for key in self.buttons:
            self.showIcon(key)
