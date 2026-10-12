from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle


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
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.black)
    palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(140, 140, 140))
    _shading(
        palette, QColor(90, 90, 90), QColor(70, 70, 70), QColor(40, 40, 40), QColor(35, 35, 35), QColor(20, 20, 20)
    )
    _disabled(palette, QColor(120, 120, 120))
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


# colours for problems, warnings and less important text, readable on the current theme's background
STATUS_COLORS = {
    'problem': (QColor(224, 108, 108), QColor(179, 38, 30)),
    'warning': (QColor(229, 160, 80), QColor(163, 82, 0)),
    'muted': (QColor(160, 160, 160), QColor(110, 110, 110)),
    'ok': (QColor(120, 190, 120), QColor(30, 120, 50)),
}

# rows for mods the game installed from mod.io: bright blue in either theme, with white text that reads on it
MODIO_BACKGROUND = QColor(0, 120, 255)
MODIO_TEXT = QColor(255, 255, 255)


def isDark(palette: QPalette | None = None) -> bool:
    if palette is None:
        app = QApplication.instance()
        palette = app.palette() if isinstance(app, QApplication) else QPalette()
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def statusColor(kind: str, palette: QPalette | None = None) -> QColor:
    '''A colour for "problem", "warning", "muted" or "ok" text that's readable in the current theme'''
    dark, light = STATUS_COLORS[kind]
    return QColor(dark if isDark(palette) else light)


class ManagerStyle(QProxyStyle):
    '''Fusion, with checkbox borders that stay visible on light backgrounds (Fusion draws them very pale there)'''

    CHECKS = (QStyle.PrimitiveElement.PE_IndicatorCheckBox, QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck)

    def __init__(self):
        super().__init__("Fusion")

    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        super().drawPrimitive(element, option, painter, widget)
        if element in self.CHECKS and not isDark(option.palette):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(QPen(option.palette.color(QPalette.ColorRole.Dark)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
            painter.restore()
