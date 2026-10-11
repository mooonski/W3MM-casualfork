import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtWidgets import QApplication, QCheckBox, QStyleFactory, QTreeWidget, QTreeWidgetItem

from src.gui.themes import ManagerStyle, get_dark_palette, get_light_palette, isDark, statusColor


def contrast(first: QColor, second: QColor) -> float:
    '''WCAG contrast ratio between two colours (1 = none, 21 = black on white)'''

    def luminance(color: QColor) -> float:
        channels = []
        for value in (color.redF(), color.greenF(), color.blueF()):
            channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


THEMES = {"dark": get_dark_palette, "light": get_light_palette}


class PaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # colours a palette leaves unset are filled in from the running application, so test with one like the app
        cls.app = QApplication.instance() or QApplication([])

    def test_text_is_readable_in_both_themes(self):
        for name, make in THEMES.items():
            palette = make()
            base = palette.color(QPalette.ColorRole.Base)
            window = palette.color(QPalette.ColorRole.Window)
            with self.subTest(theme=name):
                self.assertGreaterEqual(contrast(palette.color(QPalette.ColorRole.Text), base), 7)
                # the search hint used to be white on white in the light theme
                self.assertGreaterEqual(contrast(palette.color(QPalette.ColorRole.PlaceholderText), base), 3)
                disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText)
                self.assertGreaterEqual(contrast(disabled, window), 1.8)
                self.assertNotEqual(disabled, palette.color(QPalette.ColorRole.ButtonText))
                # frame edges: light themes draw them with Dark, dark themes with Light
                edge = QPalette.ColorRole.Light if name == "dark" else QPalette.ColorRole.Dark
                self.assertGreaterEqual(contrast(palette.color(edge), base), 2)
                self.assertGreaterEqual(
                    contrast(
                        palette.color(QPalette.ColorRole.HighlightedText), palette.color(QPalette.ColorRole.Highlight)
                    ),
                    3,
                )

    def test_every_colour_is_set_by_the_theme(self):
        # unset colours come from the platform: on Windows the search hint was white on white that way
        roles = [
            "Window", "WindowText", "Base", "AlternateBase", "ToolTipBase", "ToolTipText", "Text", "Button",
            "ButtonText", "BrightText", "Link", "Highlight", "HighlightedText", "PlaceholderText", "Light",
            "Midlight", "Mid", "Dark", "Shadow",
        ]  # fmt: skip
        disabled = ["WindowText", "Text", "ButtonText", "PlaceholderText"]
        for name, make in THEMES.items():
            palette = make()
            for role in roles:
                with self.subTest(theme=name, role=role):
                    self.assertTrue(palette.isBrushSet(QPalette.ColorGroup.Active, getattr(QPalette.ColorRole, role)))
            for role in disabled:
                with self.subTest(theme=name, disabled=role):
                    self.assertTrue(palette.isBrushSet(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role)))

    def test_status_colours_suit_the_theme(self):
        for name, make in THEMES.items():
            palette = make()
            base = palette.color(QPalette.ColorRole.Base)
            with self.subTest(theme=name):
                self.assertEqual(isDark(palette), name == "dark")
                for kind in ("problem", "warning"):
                    self.assertGreaterEqual(contrast(statusColor(kind, palette), base), 4.5, kind)
                self.assertGreaterEqual(contrast(statusColor("muted", palette), base), 3)
        self.assertNotEqual(statusColor("warning", get_dark_palette()), statusColor("warning", get_light_palette()))


class CheckboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def darkestBorder(self, widget, palette) -> float:
        '''Contrast between the background and the darkest pixel of an unticked checkbox'''
        widget.setPalette(palette)
        widget.resize(200, 60)
        image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        base = palette.color(QPalette.ColorRole.Base)
        darkest = min(
            (QColor(image.pixel(x, y)) for x in range(min(image.width(), 40)) for y in range(min(image.height(), 40))),
            key=lambda color: color.lightness(),
        )
        return contrast(darkest, base)

    def test_unticked_boxes_are_visible_on_light_backgrounds(self):
        palette = get_light_palette()
        style = ManagerStyle()
        box = QCheckBox("")
        box.setStyle(style)
        tree = QTreeWidget()
        tree.setStyle(style)
        tree.setHeaderHidden(True)
        item = QTreeWidgetItem([""])
        item.setCheckState(0, Qt.CheckState.Unchecked)
        tree.addTopLevelItem(item)
        for widget in (box, tree):
            with self.subTest(widget=type(widget).__name__):
                self.assertGreaterEqual(self.darkestBorder(widget, palette), 2.5)
        plain = QCheckBox("")
        fusion = QStyleFactory.create("Fusion")  # Fusion's own drawing, for comparison
        plain.setStyle(fusion)
        self.assertLess(self.darkestBorder(plain, palette), self.darkestBorder(box, palette))


if __name__ == "__main__":
    unittest.main()


class PluralTests(unittest.TestCase):
    def test_plural(self):
        from src.util.util import plural

        self.assertEqual((plural(0, "mod"), plural(1, "mod"), plural(2, "mod")), ("0 mods", "1 mod", "2 mods"))
        self.assertEqual(plural(3, "problem or warning", "problems or warnings"), "3 problems or warnings")
