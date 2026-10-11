'''Builds res/w3mm-fork.ico from res/w3mm-fork-source.png (the red Witcher 3 claw marks on a light background):
the background is made transparent, the logo cropped and centred, and drawn at every size Windows uses.

Run with the project's Python: python tools/make_icon.py'''

import os
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SOURCE = os.path.join(ROOT, 'res', 'w3mm-fork-source.png')
TARGET = os.path.join(ROOT, 'res', 'w3mm-fork.ico')
PREVIEW = os.path.join(ROOT, 'res', 'w3mm-fork.png')
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def withoutBackground(image: QImage) -> QImage:
    '''Turns the flat light background transparent, keeping soft edges (colour-to-alpha against the corner colour)'''
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    background = QColor(image.pixel(0, 0))
    base = (background.red(), background.green(), background.blue())
    result = QImage(image.size(), QImage.Format.Format_ARGB32)
    for y in range(image.height()):
        for x in range(image.width()):
            color = QColor(image.pixel(x, y))
            channels = (color.red(), color.green(), color.blue())
            # how far each channel is from the background, as a share of how far it could go
            alpha = max(
                (b - c) / b if c < b else (c - b) / (255 - b) if b < 255 else 0.0 for c, b in zip(channels, base)
            )
            alpha = min(1.0, max(0.0, alpha))
            if alpha < 0.02:
                result.setPixelColor(x, y, QColor(0, 0, 0, 0))
                continue
            # the colour that, laid over the background at this alpha, gives the original pixel
            red, green, blue = (min(255, max(0, round((c - b * (1 - alpha)) / alpha))) for c, b in zip(channels, base))
            result.setPixelColor(x, y, QColor(red, green, blue, round(alpha * 255)))
    return result


def content(image: QImage) -> QRect:
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        for x in range(image.width()):
            if QColor.fromRgba(image.pixel(x, y)).alpha() > 12:
                left, top, right, bottom = min(left, x), min(top, y), max(right, x), max(bottom, y)
    return QRect(left, top, right - left + 1, bottom - top + 1)


def square(logo: QImage, margin: float = 0.04) -> QImage:
    '''The cropped logo centred on a transparent square'''
    side = round(max(logo.width(), logo.height()) * (1 + 2 * margin))
    canvas = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawImage((side - logo.width()) // 2, (side - logo.height()) // 2, logo)
    painter.end()
    return canvas


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
    source = QImage(SOURCE)
    if source.isNull():
        raise SystemExit(f"could not read {SOURCE}")
    clear = withoutBackground(source)
    logo = square(clear.copy(content(clear)))
    images = [
        logo.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        for size in SIZES
    ]
    writeIco(images, TARGET)
    images[-1].save(PREVIEW)
    print('wrote', os.path.normpath(TARGET), 'and', os.path.normpath(PREVIEW))
    del app


if __name__ == '__main__':
    main()
