'''The Steel skin: the style sheet, the Play button, the mod list's tags, badges and separators, and the filters'''

import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionButton, QStyleOptionViewItem, QTreeWidget

from src.core.nexus import FAILED, UP_TO_DATE, UPDATE_AVAILABLE, ModFiles, NexusFile, NexusInfo, UpdateCheck
from src.core.nexusdetails import DetailsCache, ModDetails, Requirement
from src.core.requirements import MISSING, MissingRequirement
from src.domain.mod import Mod
from src.gui.downloads_widget import DownloadsWidget, Transfer
from src.gui.icons import badgedIcon, icon
from src.gui.main_widget import CONTENTS_COLUMN, UPDATE_COLUMN, CustomMainWidget, contentsTags, updateKind
from src.gui.skin import COMFORTABLE_HEIGHT, GROUP_COUNT_ROLE, UPDATE_KIND_ROLE, ModListDelegate, PlayButton
from src.gui.themes import (
    ACCENTS,
    STEEL,
    ManagerStyle,
    accentColor,
    accentName,
    get_dark_palette,
    get_light_palette,
    setAccent,
    setTickStyle,
    statusColor,
    styleSheet,
    tickStyle,
)
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


class AppearanceChoiceTests(unittest.TestCase):
    '''Ice blue instead of red, switches instead of tick boxes, and comfortable rows with pictures'''

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        setAccent("red")
        setTickStyle("box")

    def test_accent_colour(self):
        red = accentColor(get_dark_palette()).name()
        setAccent("blue")
        self.assertEqual(accentName(), "blue")
        self.assertEqual(accentColor(get_dark_palette()).name(), ACCENTS["blue"]["accent"])
        self.assertNotEqual(accentColor(get_dark_palette()).name(), red)
        self.assertIn(ACCENTS["blue"]["accent"], styleSheet("Dark"))
        self.assertNotIn(ACCENTS["red"]["accent"], styleSheet("Dark"))
        self.assertEqual(get_dark_palette().color(QPalette.ColorRole.Highlight).name(), ACCENTS["blue"]["selection"])
        self.assertEqual(statusColor("accent", get_dark_palette()).name(), ACCENTS["blue"]["accentText"])
        setAccent("purple")  # unknown: back to red
        self.assertEqual(accentName(), "red")

    def test_white_text_is_readable_on_each_accent(self):
        from tests.test_themes import contrast

        for name, accent in ACCENTS.items():
            for key in ("accent", "light"):
                with self.subTest(accent=name, theme=key):
                    self.assertGreaterEqual(contrast(QColor(accent[key]), QColor("white")), 4.5)

    def test_switches(self):
        style = ManagerStyle()
        box = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth)
        setTickStyle("switch")
        self.assertEqual(style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth), ManagerStyle.SWITCH_WIDTH)
        self.assertGreater(ManagerStyle.SWITCH_WIDTH, box)
        for palette in (get_dark_palette(), get_light_palette()):
            for state, knobRight in ((QStyle.StateFlag.State_On, True), (QStyle.StateFlag.State_Off, False)):
                with self.subTest(dark=palette is not None, on=knobRight):
                    image = QImage(40, 20, QImage.Format.Format_ARGB32)
                    image.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(image)
                    option = QStyleOptionButton()
                    option.rect = image.rect()
                    option.palette = palette
                    option.state = state | QStyle.StateFlag.State_Enabled
                    style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, option, painter)
                    painter.end()
                    white = [x for x in range(40) if QColor(image.pixel(x, 10)) == QColor("white")]
                    self.assertTrue(white, "the knob is drawn")
                    self.assertEqual(sum(white) / len(white) > 20, knobRight)
        setTickStyle("wobbly")
        self.assertEqual(tickStyle(), "box")

    def test_comfortable_rows(self):
        tree = QTreeWidget()
        tree.setColumnCount(3)
        delegate = ModListDelegate(tree, 2, 2)
        tree.setItemDelegate(delegate)
        heading = GroupItem("Visuals", 0)
        tree.addTopLevelItem(heading)
        row = CustomTreeWidgetItem(["", "Cat Hood Fix", ""])
        heading.addChild(row)
        heading.setExpanded(True)
        option = QStyleOptionViewItem()
        compact = delegate.sizeHint(option, tree.indexFromItem(row, 1)).height()
        delegate.setComfortable(True)
        self.assertEqual(delegate.sizeHint(option, tree.indexFromItem(row, 1)).height(), COMFORTABLE_HEIGHT)
        self.assertLess(compact, COMFORTABLE_HEIGHT)
        self.assertLess(delegate.sizeHint(option, tree.indexFromItem(heading, 0)).height(), COMFORTABLE_HEIGHT)
        self.assertEqual(ModListDelegate.initials("[MOD.IO] Ciri E3 Scabbard"), "CE")
        self.assertEqual(ModListDelegate.initials("Cat Hood Fix"), "CH")
        self.assertIsNone(delegate.picture(""))
        self.assertIsNone(delegate.picture("missing.png"))
        tree.resize(500, 200)
        self.assertFalse(tree.grab().toImage().isNull())


class PreviewSectionTests(unittest.TestCase):
    '''What the details pane's Files, Changelog and Requirements tabs say about an installed mod'''

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.mod = Mod()
        self.mod._name = "The Living Continent"
        self.mod.files = ["modW3LivingSky"]
        self.mod.menus = ["mod00LivingContinent.xml"]
        self.mod.bins = ["x64_dx12/W3LivingSky.addon64"]
        self.mod.inputsettings = [Mock(), Mock()]
        self.mod.nexus = NexusInfo(13650, 2, "1.4.0")
        self.widget = Mock()
        self.widget.updateChecks = {}

    def test_files(self):
        text = CustomMainWidget.filesHtml(self.mod)
        for expected in (
            "modW3LivingSky",
            "mod00LivingContinent.xml",
            "x64_dx12/W3LivingSky.addon64",
            "2 key bindings",
        ):
            self.assertIn(expected, text)
        self.assertNotIn("DLC folder", text)
        self.assertIn("No files", CustomMainWidget.filesHtml(Mod()))

    def test_changelog(self):
        self.assertIn("comes from Nexus", CustomMainWidget.changelogHtml(self.widget, self.mod))
        installed = NexusFile(2, "Main", "1.4.0", "MAIN", True, 100, "a.zip", 1, changelog="Fixed fog")
        newer = NexusFile(3, "Main", "1.5", "MAIN", True, 200, "b.zip", 1, changelog="Snow<br>Rain")
        files = ModFiles({2: installed, 3: newer}, {2: 3})
        self.widget.updateChecks = {self.mod.name: UpdateCheck(UPDATE_AVAILABLE, installed, newer, files=files)}
        text = CustomMainWidget.changelogHtml(self.widget, self.mod)
        self.assertIn("v1.5", text)
        self.assertIn("Snow", text)
        self.assertNotIn("Fixed fog", text, "only the changes after the installed file")
        self.widget.updateChecks = {self.mod.name: UpdateCheck(UP_TO_DATE, installed, files=files)}
        text = CustomMainWidget.changelogHtml(self.widget, self.mod)
        self.assertIn("Fixed fog", text, "the installed file's own changes")
        unlinked = Mod()
        self.assertIn("Not linked", CustomMainWidget.changelogHtml(self.widget, unlinked))

    def test_requirements(self):
        self.widget.model = Mock()
        self.widget.nexusDetails = Mock()
        self.assertIn("been loaded yet", CustomMainWidget.requirementsHtml(self.widget, self.mod, None))
        details = ModDetails(13650, "The Living Continent")
        self.assertIn("no requirements", CustomMainWidget.requirementsHtml(self.widget, self.mod, details))
        details.requirements = [
            Requirement("Sharedutils", modId=6000, gameId=952),
            Requirement("ReShade", external=True, notes="bin&#92;x64_dx12"),
        ]
        missing = [MissingRequirement(self.mod.name, details.requirements[0], MISSING)]
        with (
            patch("src.gui.main_widget.missingRequirements", return_value=missing),
            patch("src.gui.main_widget.data.config", Mock(dlc="")),
        ):
            text = CustomMainWidget.requirementsHtml(self.widget, self.mod, details)
        self.assertIn("Sharedutils", text)
        self.assertIn("not installed", text)
        self.assertIn("ReShade", text)
        self.assertIn("bin\\x64_dx12", text, "Nexus's escaped characters are shown as themselves")


class TransferTests(unittest.TestCase):
    '''Downloads in progress on the Downloads page'''

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_progress_text(self):
        self.assertEqual(Transfer("A", waiting=True).progressText(), "Waiting")
        self.assertEqual(Transfer("A").progressText(), "Starting...")
        self.assertEqual(Transfer("A", 1024**2, 4 * 1024**2).progressText(), "1.0 MB of 4.0 MB")
        self.assertEqual(Transfer("A", 1, 3, inBytes=False).progressText(), "1 of 3")

    def test_panel_shows_running_and_waiting_downloads(self):
        with tempfile.TemporaryDirectory() as folder:
            page = DownloadsWidget(None, DetailsCache(folder), lambda: (folder, []), True)
            self.assertTrue(page.transfersBox.isHidden())
            page.setTransfers([Transfer("Brothers In Arms", 50, 100), Transfer("Sharedutils", waiting=True)])
            self.assertFalse(page.transfersBox.isHidden())
            (name, bar, amount), (_, waitingBar, waiting) = page.transferWidgets
            self.assertEqual(name.text(), "Brothers In Arms")
            self.assertEqual(bar.value(), 500)
            self.assertEqual(waiting.text(), "Waiting")
            page.setTransfers([Transfer("Brothers In Arms", 75, 100), Transfer("Sharedutils", waiting=True)])
            self.assertIs(page.transferWidgets[0][1], bar, "the same rows are updated, not built again")
            self.assertEqual(bar.value(), 750)
            page.setTransfers([])
            self.assertTrue(page.transfersBox.isHidden())

    def test_running_and_queued_downloads_are_counted(self):
        widget = Mock()
        widget.activeTransfer = Transfer("Brothers In Arms", 5, 10)
        widget.nxmQueue = ["nxm://witcher3/mods/13650/files/7?key=a&expires=1", "nxm://skyrim/mods/1/files/2"]
        widget.nexusLabel.return_value = "The Living Continent"
        widget.notInstalledCount = 4
        CustomMainWidget.showTransfers(widget)
        shown = widget.downloads.setTransfers.call_args.args[0]
        self.assertEqual(
            [(t.label, t.waiting) for t in shown], [("Brothers In Arms", False), ("The Living Continent", True)]
        )
        self.assertEqual(widget.rail.setBadge.call_args.args[:2], ("downloads", 2))
        widget.activeTransfer, widget.nxmQueue = None, []
        CustomMainWidget.showTransfers(widget)
        self.assertEqual(widget.rail.setBadge.call_args.args[:2], ("downloads", 4), "then the archives not installed")

    def test_downloads_register_while_they_run(self):
        widget = Mock()
        with patch("src.gui.main_widget.NexusWorker") as worker:
            CustomMainWidget.runNexus(widget, Mock(), Mock(), download="Sharedutils")
        transfer = widget.activeTransfer
        self.assertEqual(transfer.label, "Sharedutils")
        widget.showTransfers.assert_called()
        worker.return_value.start.assert_called_once()
        CustomMainWidget.transferProgressed(widget, transfer, 3, 9)
        self.assertEqual((transfer.done, transfer.total), (3, 9))
        CustomMainWidget.transferFinished(widget, transfer)
        self.assertIsNone(widget.activeTransfer)
        widget = Mock()
        widget.activeTransfer = None
        with patch("src.gui.main_widget.NexusWorker"):
            CustomMainWidget.runNexus(widget, Mock(), Mock())
        self.assertIsNone(widget.activeTransfer, "checks and searches aren't downloads")


class PictureTests(unittest.TestCase):
    '''Full-size Nexus pictures for the side panel, kept apart from the thumbnails of the same name'''

    def test_pictures_and_thumbnails_are_kept_apart(self):
        thumbnailUrl = "https://staticdelivery.nexusmods.com/mods/952/images/thumbnails/659-0-1.jpg"
        pictureUrl = "https://staticdelivery.nexusmods.com/mods/952/images/659-0-1.jpg"
        details = ModDetails(659, "Example", thumbnailUrl=thumbnailUrl, pictureUrl=pictureUrl)
        with tempfile.TemporaryDirectory() as folder:
            cache = DetailsCache(folder)
            self.assertNotEqual(cache.imagePath(thumbnailUrl), cache.imagePath(pictureUrl, full=True))
            self.assertEqual(cache.bestImage(details), "")
            response = Mock()
            response.__enter__ = Mock(return_value=Mock(read=Mock(return_value=b"small")))
            response.__exit__ = Mock(return_value=False)
            cache.downloadImage(thumbnailUrl, opener=lambda request, timeout: response)
            self.assertEqual(cache.bestImage(details), cache.image(details), "the thumbnail until the picture is here")
            response.__enter__ = Mock(return_value=Mock(read=Mock(return_value=b"large")))
            stored = cache.downloadPicture(pictureUrl, opener=lambda request, timeout: response)
            self.assertEqual(cache.picture(details), stored)
            self.assertEqual(cache.bestImage(details), stored)
            with open(cache.image(details), "rb") as file:
                self.assertEqual(file.read(), b"small", "the picture didn't overwrite the thumbnail")


if __name__ == "__main__":
    unittest.main()
