'''The Steel skin: the style sheet, the Play button, the mod list's tags, badges and separators, and the filters'''

import unittest
from unittest.mock import Mock

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage
from PySide6.QtWidgets import QApplication, QTreeWidget

from src.core.nexus import FAILED, UP_TO_DATE, UPDATE_AVAILABLE, UpdateCheck
from src.domain.mod import Mod
from src.gui.icons import badgedIcon, icon
from src.gui.main_widget import CONTENTS_COLUMN, UPDATE_COLUMN, CustomMainWidget, contentsTags, updateKind
from src.gui.skin import GROUP_COUNT_ROLE, UPDATE_KIND_ROLE, ModListDelegate, PlayButton
from src.gui.themes import STEEL, ManagerStyle, accentColor, get_dark_palette, get_light_palette, styleSheet
from src.gui.tree_widget import CustomTreeWidgetItem, GroupItem


class SkinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_style_sheet_is_only_for_the_dark_theme(self):
        sheet = styleSheet("Dark")
        self.assertIn(STEEL["accent"], sheet)
        self.assertNotIn("$", sheet, "every colour name is filled in")
        self.assertEqual(styleSheet("Light"), "")
        self.assertEqual(styleSheet("Follow System"), "")

    def test_contents_tags(self):
        mod = Mod()
        self.assertEqual(contentsTags(mod), "")
        mod.dlcs = ["dlcExample"]
        mod.hidden = ["hidden.xml"]
        mod.inputsettings = [Mock()]
        mod.usersettings = [Mock()]
        mod.bins = ["x64_dx12/example.dll"]
        self.assertEqual(contentsTags(mod), "DLC Menu Keys Ini Bin")

    def test_update_kind(self):
        self.assertEqual(updateKind(None), "")
        self.assertEqual(updateKind(UpdateCheck(UPDATE_AVAILABLE)), "update")
        self.assertEqual(updateKind(UpdateCheck(FAILED)), "error")
        self.assertEqual(updateKind(UpdateCheck(UP_TO_DATE)), "ok")

    def test_filters(self):
        widget = Mock()
        update = UpdateCheck(UPDATE_AVAILABLE)
        expected = {
            "all": [True, True, True],
            "enabled": [True, False, True],
            "disabled": [False, True, False],
            "updates": [False, False, True],
        }
        for name, shown in expected.items():
            widget.listFilter = name
            with self.subTest(filter=name):
                self.assertEqual(
                    [
                        CustomMainWidget.passesFilter(widget, True, None),
                        CustomMainWidget.passesFilter(widget, False, None),
                        CustomMainWidget.passesFilter(widget, True, update),
                    ],
                    shown,
                )

    def test_play_button_makes_room_for_how_the_game_starts(self):
        button = PlayButton()
        button.setText("Play")
        plain = button.sizeHint().width()
        button.setMode("DX12")
        self.assertGreater(button.sizeHint().width(), plain + 20)
        button.setPalette(get_dark_palette())
        button.resize(button.sizeHint())
        image = button.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        middle = QColor(image.pixel(image.width() // 2, 3))
        self.assertEqual(middle.name(), accentColor(get_dark_palette()).name(), "filled with the accent")

    def test_badge_marks_the_icon_only_when_there_is_a_count(self):
        palette = get_dark_palette()
        plain = icon("update", palette).pixmap(QSize(24, 24)).toImage()
        none = badgedIcon("update", 0, QColor(STEEL["amber"]), palette).pixmap(QSize(24, 24)).toImage()
        some = badgedIcon("update", 3, QColor(STEEL["amber"]), palette).pixmap(QSize(24, 24)).toImage()
        self.assertEqual(none, plain)
        self.assertNotEqual(some, plain)
        disabled = badgedIcon("update", 3, QColor(STEEL["amber"]), palette).pixmap(QSize(24, 24), QIcon.Mode.Disabled)
        self.assertEqual(
            disabled.toImage(), icon("update", palette).pixmap(QSize(24, 24), QIcon.Mode.Disabled).toImage()
        )

    def test_mod_list_rows_draw(self):
        for palette in (get_dark_palette(), get_light_palette()):
            tree = QTreeWidget()
            tree.setStyle(ManagerStyle())
            tree.setPalette(palette)
            tree.setColumnCount(CONTENTS_COLUMN + 1)
            delegate = ModListDelegate(tree, CONTENTS_COLUMN, UPDATE_COLUMN)
            tree.setItemDelegate(delegate)
            heading = GroupItem("Visuals", 0)
            tree.addTopLevelItem(heading)
            heading.setFirstColumnSpanned(True)
            heading.setText(0, "Visuals  (1)")
            heading.setData(0, GROUP_COUNT_ROLE, 1)
            row = CustomTreeWidgetItem([""] * (CONTENTS_COLUMN + 1))
            row.setText(1, "The Living Continent")
            row.setText(UPDATE_COLUMN, "Update 1.5")
            row.setData(UPDATE_COLUMN, UPDATE_KIND_ROLE, "update")
            row.setText(CONTENTS_COLUMN, "Menu Bin")
            row.setCheckState(0, Qt.CheckState.Checked)
            heading.addChild(row)
            heading.setExpanded(True)
            row.setSelected(True)
            tree.resize(1200, 200)
            self.assertFalse(tree.grab().toImage().isNull())
            self.assertGreaterEqual(tree.visualItemRect(row).height(), 28)


if __name__ == "__main__":
    unittest.main()
