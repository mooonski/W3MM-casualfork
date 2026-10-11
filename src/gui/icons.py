'''Toolbar icons: simple line drawings in one style, coloured to match the current theme'''

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

# 24x24 drawings; "currentColor" is replaced by the theme's text colour
SHAPES = {
    'add': '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
    'remove': '<path d="M4 7h16M9 7V4.5h6V7M6 7l1 12.5a1.5 1.5 0 0 0 1.5 1.5h7a1.5 1.5 0 0 0 1.5-1.5L18 7M10 11v6M14 11v6"/>',
    'toggle': '<rect x="2" y="6.5" width="20" height="11" rx="5.5"/><circle cx="16.5" cy="12" r="3"/>',
    'check': '<path d="M20 11.5A8 8 0 0 0 5.6 7.2L4 9M4 4.5V9h4.5M4 12.5a8 8 0 0 0 14.4 4.3L20 15M20 19.5V15h-4.5"/>',
    'update': '<path d="M12 3.5v11M7.5 10l4.5 4.5 4.5-4.5M4.5 15.5v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3"/>',
    'merge': '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="12" r="2"/>'
    '<path d="M6 7v10M6 7a7 5 0 0 0 10 5"/>',
    'mods': '<path d="M3.5 8.5h4.6a3 3 0 1 1 5.8 0h3.6v3.6a3 3 0 1 1 0 5.8v3.6h-14z"/>',
    'dlc': '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
    'menus': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h10M7 13h10M7 17h6"/>',
    'folder': '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    'xml': '<path d="M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8z"/>'
    '<path d="M14 3v5h5M10.5 12l-2.5 2.5 2.5 2.5M13.5 12l2.5 2.5-2.5 2.5"/>',
    'keyboard': '<rect x="2" y="6" width="20" height="12" rx="2"/>'
    '<path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M7.5 14h9"/>',
    'sliders': '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/>'
    '<circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    'order': '<path d="M11 6h9M11 12h9M11 18h9M6 4v16M3.5 6.5 6 4l2.5 2.5M3.5 17.5 6 20l2.5-2.5"/>',
}

SIZES = (16, 20, 24, 32, 48)


def _svg(shape: str, color: QColor) -> QByteArray:
    return QByteArray(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + shape + '</svg>'
        )
        .replace('currentColor', color.name())
        .encode('utf-8')
    )


def _pixmap(shape: str, color: QColor, size: int, ratio: float) -> QPixmap:
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(_svg(shape, color)).render(painter, QRectF(0, 0, pixmap.width(), pixmap.height()))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def icon(name: str, palette: QPalette | None = None) -> QIcon:
    '''The named icon in the palette's button text colour (greyed when disabled)'''
    app = QApplication.instance()
    if palette is None:
        palette = app.palette() if isinstance(app, QApplication) else QPalette()
    ratio = app.devicePixelRatio() if isinstance(app, QApplication) else 1.0
    normal = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText)
    disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText)
    result = QIcon()
    for size in SIZES:
        result.addPixmap(_pixmap(SHAPES[name], normal, size, ratio), QIcon.Mode.Normal)
        result.addPixmap(_pixmap(SHAPES[name], disabled, size, ratio), QIcon.Mode.Disabled)
    return result
