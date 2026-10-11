'''Builds res/w3mm-fork.ico: the W3MM wolf medallion on a crimson tile, at every size Windows uses.

Run with the project's Python: python tools/make_icon.py'''

import os
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SOURCE = os.path.join(ROOT, 'res', 'w3a.ico')
TARGET = os.path.join(ROOT, 'res', 'w3mm-fork.ico')
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(size: int, wolf: QImage) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    # tile: deep crimson, lighter at the top, with a thin bright edge so it reads on dark and light taskbars
    margin = max(0.5, size * 0.03)
    tile = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    radius = size * 0.2
    path = QPainterPath()
    path.addRoundedRect(tile, radius, radius)
    gradient = QLinearGradient(QPointF(0, tile.top()), QPointF(0, tile.bottom()))
    gradient.setColorAt(0, QColor(176, 38, 34))
    gradient.setColorAt(1, QColor(92, 14, 14))
    painter.fillPath(path, gradient)
    if size >= 24:
        painter.setPen(QColor(232, 150, 120, 150))
        painter.drawPath(path)
    # the wolf, brightened so its silver shows against the red
    inset = size * (0.08 if size <= 24 else 0.12)
    target = tile.adjusted(inset, inset, -inset, -inset)
    bright = wolf.convertToFormat(QImage.Format.Format_ARGB32)
    lift = QPainter(bright)
    lift.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
    lift.fillRect(bright.rect(), QColor(255, 244, 230, 70))
    lift.end()
    painter.drawImage(target, bright.scaled(int(target.width() * 2), int(target.height() * 2),
                                            Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))  # fmt: skip
    painter.end()
    return image


def png(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    return bytes(data.data())


def writeIco(images: list[QImage], target: str) -> None:
    '''An .ico with one PNG-compressed image per size (supported since Windows Vista)'''
    blobs = [png(image) for image in images]
    header = struct.pack('<HHH', 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b''
    for image, blob in zip(images, blobs):
        side = image.width() if image.width() < 256 else 0
        entries += struct.pack('<BBBBHHII', side, side, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(target, 'wb') as file:
        file.write(header + entries + b''.join(blobs))


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    wolf = QImage(SOURCE)
    if wolf.isNull():
        raise SystemExit(f"could not read {SOURCE}")
    writeIco([render(size, wolf) for size in SIZES], TARGET)
    print('wrote', os.path.normpath(TARGET))
    del app


if __name__ == '__main__':
    main()
