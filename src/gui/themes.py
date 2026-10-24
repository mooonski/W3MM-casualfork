from string import Template

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

# the "Steel" skin of the dark theme: graphite surfaces and one accent, the red of the claw marks icon
STEEL = {
    'bg': '#111316',  # the window behind the panels
    'panel': '#171a1e',  # lists, cards and the toolbar
    'alt': '#1b1e23',  # every other row
    'raised': '#1f2328',  # buttons, headers
    'hover': '#262b32',
    'line': '#2a2f36',
    'strong': '#3a414a',  # borders that need to show, e.g. a hovered button
    'text': '#e6e8eb',
    'muted': '#9aa3ad',
    'faint': '#7d8691',
    'accent': '#d13b40',
    'accentHover': '#dc4a4f',
    'accentPressed': '#b5323a',
    'accentText': '#ff8080',  # the accent as text on the panels
    'selection': '#3a2328',  # the accent mixed into the panel
    'amber': '#f0a54a',
    'amberSoft': '#3a2f1f',
    'blue': '#6cb2ff',
    'blueSoft': '#1d2e44',
}


def _shading(palette: QPalette, light: QColor, midlight: QColor, mid: QColor, dark: QColor, shadow: QColor) -> None:
    '''The colours Qt draws frames, checkbox borders and separators with; without them it uses unrelated defaults'''
    palette.setColor(QPalette.ColorRole.Light, light)
    palette.setColor(QPalette.ColorRole.Midlight, midlight)
    palette.setColor(QPalette.ColorRole.Mid, mid)
    palette.setColor(QPalette.ColorRole.Dark, dark)
    palette.setColor(QPalette.ColorRole.Shadow, shadow)


def _disabled(palette: QPalette, text: QColor) -> None:
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, text)


def get_dark_palette():
    color = {name: QColor(value) for name, value in STEEL.items()}
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, color['bg'])
    palette.setColor(QPalette.ColorRole.WindowText, color['text'])
    palette.setColor(QPalette.ColorRole.Base, color['panel'])
    palette.setColor(QPalette.ColorRole.AlternateBase, color['alt'])
    palette.setColor(QPalette.ColorRole.ToolTipBase, color['raised'])
    palette.setColor(QPalette.ColorRole.ToolTipText, color['text'])
    palette.setColor(QPalette.ColorRole.Text, color['text'])
    palette.setColor(QPalette.ColorRole.Button, color['raised'])
    palette.setColor(QPalette.ColorRole.ButtonText, color['text'])
    palette.setColor(QPalette.ColorRole.BrightText, color['accentText'])
    palette.setColor(QPalette.ColorRole.Link, color['blue'])
    palette.setColor(QPalette.ColorRole.Highlight, color['selection'])
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.PlaceholderText, color['faint'])
    _shading(palette, QColor(74, 82, 92), QColor(53, 59, 67), color['line'], QColor(28, 31, 36), QColor(11, 12, 14))
    _disabled(palette, QColor(91, 99, 109))
    return palette


def get_light_palette():
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.black)
    palette.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(233, 233, 233))
    palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.black)
    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.black)
    palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.black)
    palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
    palette.setColor(QPalette.ColorRole.Link, QColor(0, 102, 204))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(0, 120, 215))
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(125, 125, 125))
    _shading(
        palette, QColor(255, 255, 255), QColor(227, 227, 227), QColor(160, 160, 160), QColor(130, 130, 130),
        QColor(105, 105, 105),
    )  # fmt: skip
    _disabled(palette, QColor(150, 150, 150))
    return palette


def get_system_palette():
    return QApplication.style().standardPalette()


# the skin on top of the dark palette; the light themes keep Qt's own look for now
STEEL_SHEET = Template('''
QToolTip { background: $raised; color: $text; border: 1px solid $strong; padding: 4px 6px; }
QMenuBar { background: $bg; border-bottom: 1px solid $line; padding: 2px 4px; }
QMenuBar::item { background: transparent; color: $muted; padding: 5px 10px; border-radius: 5px; }
QMenuBar::item:selected, QMenuBar::item:pressed { background: $hover; color: $text; }
QMenu { background: $panel; border: 1px solid $strong; padding: 4px; }
QMenu::item { padding: 6px 26px 6px 24px; border-radius: 5px; }
QMenu::item:selected { background: $selection; color: #ffffff; }
QMenu::item:disabled { color: $faint; }
QMenu::separator { height: 1px; background: $line; margin: 4px 8px; }
QToolBar#toolBar, QToolBar#launchBar { background: $panel; border: none; border-bottom: 1px solid $line; spacing: 2px; }
QToolBar#toolBar { padding: 6px 4px 6px 10px; }
QToolBar#launchBar { padding: 6px 12px 6px 4px; }
QToolBar::separator { width: 1px; background: $line; margin: 12px 7px; }
QToolBar QToolButton { background: transparent; color: $text; border: none; border-radius: 8px; padding: 4px 7px; }
QToolBar QToolButton:hover { background: $hover; }
QToolBar QToolButton:pressed, QToolBar QToolButton:checked { background: $raised; }
QToolBar QToolButton[popupMode="2"] { border: 1px solid $line; padding: 6px 12px; margin: 0 2px; }
QToolBar QToolButton::menu-indicator { image: none; width: 0; }
QPushButton { background: $raised; color: $text; border: 1px solid $line; border-radius: 6px; padding: 5px 12px; }
QPushButton:hover { background: $hover; border-color: $strong; }
QPushButton:pressed { background: $panel; }
QPushButton:disabled { color: $faint; }
QPushButton#scriptMergerButton { padding: 7px 14px; font-weight: 600; }
QPushButton[chip="true"] { background: transparent; color: $muted; border: 1px solid $line; border-radius: 4px; padding: 5px 11px; }
QPushButton[chip="true"]:hover { background: $hover; color: $text; }
QPushButton[chip="true"]:checked { background: $text; color: $panel; border-color: $text; font-weight: 600; }
QPushButton#healthChip { background: $amberSoft; color: $amber; border: none; border-radius: 4px; padding: 3px 9px; font-weight: 600; }
QPushButton#healthChip:hover { background: #4a3b24; }
QPushButton#logToggle { background: transparent; color: $muted; border: none; padding: 3px 8px; }
QPushButton#logToggle:hover, QPushButton#logToggle:checked { color: $text; }
QLineEdit { background: $bg; color: $text; border: 1px solid $line; border-radius: 6px; padding: 5px 8px; selection-background-color: $selection; }
QLineEdit:focus { border-color: $accent; }
QTabWidget::pane { background: $panel; border: 1px solid $line; border-radius: 10px; top: -1px; }
QTabBar::tab { background: transparent; color: $muted; border: none; border-bottom: 2px solid transparent; padding: 8px 2px; margin: 0 10px; }
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected { color: $text; border-bottom-color: $accent; font-weight: 600; }
QHeaderView { background: $raised; }
QHeaderView::section { background: $raised; color: $faint; border: none; border-bottom: 1px solid $line; padding: 5px 6px; font-weight: 600; }
QHeaderView::section:hover { color: $text; }
QTreeView, QListView, QTableView { background: $panel; border: 1px solid $line; border-radius: 8px; padding: 1px; }
QTabWidget QTreeView#treeWidget { border: none; border-radius: 0; padding: 0; }
QTextEdit#textEdit { background: $bg; border: 1px solid $line; border-radius: 8px; padding: 4px; }
QFrame#statusRow { background: $panel; border-top: 1px solid $line; }
QLabel#statusText, QLabel#summaryText { color: $muted; }
QWidget#modPreview { background: $panel; border: 1px solid $line; border-radius: 10px; }
QWidget#modPreview QScrollArea, QWidget#modPreview QScrollArea > QWidget > QWidget { background: transparent; }
QLabel#previewTitle { font-size: 11pt; font-weight: 700; }
QLabel#previewMeta { color: $muted; }
QProgressBar#progressBar { background: $raised; border: none; border-radius: 2px; }
QProgressBar#progressBar::chunk { background: $accent; border-radius: 2px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle { background: #30363e; border-radius: 3px; }
QScrollBar::handle:vertical { min-height: 30px; }
QScrollBar::handle:horizontal { min-width: 30px; }
QScrollBar::handle:hover { background: #3f464f; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QSplitter::handle { background: transparent; }
''')


def styleSheet(theme: str) -> str:
    '''The application style sheet for a theme ("Dark", "Light" or "Follow System")'''
    return STEEL_SHEET.substitute(STEEL) if theme == 'Dark' else ''


# colours for problems, warnings and less important text, readable on the current theme's background
STATUS_COLORS = {
    'problem': (QColor(240, 112, 112), QColor(179, 38, 30)),
    'warning': (QColor(STEEL['amber']), QColor(163, 82, 0)),
    'muted': (QColor(STEEL['muted']), QColor(110, 110, 110)),
    'ok': (QColor(120, 190, 120), QColor(30, 120, 50)),
    # mods the game installed from mod.io
    'modio': (QColor(60, 160, 255), QColor(0, 100, 240)),
    # the accent as text: endorsed thumbs, the selected row's edge
    'accent': (QColor(STEEL['accentText']), QColor(180, 35, 42)),
    'update': (QColor(STEEL['blue']), QColor(11, 99, 201)),
}


def isDark(palette: QPalette | None = None) -> bool:
    if palette is None:
        app = QApplication.instance()
        palette = app.palette() if isinstance(app, QApplication) else QPalette()
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def statusColor(kind: str, palette: QPalette | None = None) -> QColor:
    '''A colour for "problem", "warning", "muted" or "ok" text that's readable in the current theme'''
    dark, light = STATUS_COLORS[kind]
    return QColor(dark if isDark(palette) else light)


def accentColor(palette: QPalette | None = None) -> QColor:
    '''The fill colour of the main buttons and ticked boxes'''
    return QColor(STEEL['accent']) if isDark(palette) else QColor(198, 47, 53)


class ManagerStyle(QProxyStyle):
    '''Fusion, with checkboxes that stay visible on light backgrounds (Fusion draws them very pale there) and
    rounded, accent-filled ones on dark'''

    CHECKS = (QStyle.PrimitiveElement.PE_IndicatorCheckBox, QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck)

    def __init__(self):
        super().__init__("Fusion")

    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        if element in self.CHECKS and isDark(option.palette):
            self.drawDarkCheck(option, painter)
            return
        super().drawPrimitive(element, option, painter, widget)
        if element in self.CHECKS and not isDark(option.palette):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(QPen(option.palette.color(QPalette.ColorRole.Dark)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
            painter.restore()

    @staticmethod
    def drawDarkCheck(option, painter) -> None:
        size = min(option.rect.width(), option.rect.height(), 16) - 1
        box = QRectF(0, 0, size, size)
        box.moveCenter(QRectF(option.rect).center())
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        ticked = bool(option.state & QStyle.StateFlag.State_On)
        partly = bool(option.state & QStyle.StateFlag.State_NoChange)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if ticked or partly:
            fill = accentColor(option.palette)
            if not enabled:
                fill = option.palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(box, 3.5, 3.5)
            pen = QPen(QColor(Qt.GlobalColor.white), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            if partly:
                middle = box.center().y()
                painter.drawLine(QPointF(box.left() + size * 0.28, middle), QPointF(box.right() - size * 0.28, middle))
            else:
                tick = QPainterPath(QPointF(box.left() + size * 0.25, box.top() + size * 0.52))
                tick.lineTo(QPointF(box.left() + size * 0.43, box.top() + size * 0.70))
                tick.lineTo(QPointF(box.left() + size * 0.76, box.top() + size * 0.32))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(tick)
        else:
            painter.setPen(QPen(QColor(STEEL['faint'] if enabled else STEEL['strong']), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(0.75, 0.75, -0.75, -0.75), 3, 3)
        painter.restore()
