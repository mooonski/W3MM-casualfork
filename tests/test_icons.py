import unittest

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon, QImage
from PySide6.QtWidgets import QApplication

from src.gui.icons import SHAPES, icon
from src.gui.themes import get_dark_palette, get_light_palette


def inked(image: QImage) -> list[QColor]:
    '''The visible pixels of an icon image'''
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    colors = []
    for y in range(image.height()):
        for x in range(image.width()):
            color = QColor.fromRgba(image.pixel(x, y))
            if color.alpha() > 200:
                colors.append(color)
    return colors


class IconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_every_icon_is_drawn_in_the_theme_text_colour(self):
        for theme, palette, light in (("dark", get_dark_palette(), True), ("light", get_light_palette(), False)):
            for name in SHAPES:
                with self.subTest(theme=theme, icon=name):
                    image = icon(name, palette).pixmap(QSize(24, 24)).toImage()
                    pixels = inked(image)
                    self.assertGreater(len(pixels), 20, "the icon should draw something")
                    lightness = sum(color.lightness() for color in pixels) / len(pixels)
                    self.assertTrue(lightness > 180 if light else lightness < 80, lightness)

    def test_disabled_icons_are_greyed(self):
        palette = get_dark_palette()
        normal = inked(icon("update", palette).pixmap(QSize(24, 24), QIcon.Mode.Normal).toImage())
        disabled = inked(icon("update", palette).pixmap(QSize(24, 24), QIcon.Mode.Disabled).toImage())
        self.assertLess(
            sum(c.lightness() for c in disabled) / len(disabled), sum(c.lightness() for c in normal) / len(normal)
        )

    def test_sizes(self):
        sizes = {size.width() for size in icon("add").availableSizes()}
        self.assertTrue({16, 24, 32, 48} <= sizes, sizes)


if __name__ == "__main__":
    unittest.main()
