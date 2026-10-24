'''Layout checks: widgets are laid out like a real window (without appearing on screen) and their geometry is
checked against rules that must hold whatever the fonts or theme, instead of comparing screenshots.'''

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src.core.nexusdetails import DetailsCache, ModDetails
from src.gui.browse_widget import BrowseWidget
from src.gui.downloads_widget import DownloadsWidget
from src.gui.mod_preview import IMAGE_HEIGHT, ModPreview

SHORT = ModDetails(1, "Short", summary="One line.", category="Armour", version="1")
LONG = ModDetails(
    2,
    "A much longer mod name that has to wrap onto another line",
    summary="Fixes the shadow issue on the hood of the Grandmaster Cat Armor. " * 8,
    category="Bug Fixes",
    author="ScoutBr0",
    version="1.2",
    updated=1,
)


def settle(app: QApplication) -> None:
    for _ in range(10):
        app.processEvents()


def offscreen(widget: QWidget, width: int, height: int) -> QWidget:
    '''Shows a widget so Qt lays it out completely, without it appearing on screen'''
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    widget.resize(width, height)
    widget.show()
    return widget


def rectIn(widget: QWidget, ancestor: QWidget) -> QRect:
    return QRect(widget.mapTo(ancestor, QPoint(0, 0)), widget.size())


class LayoutTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def assertInside(self, widget: QWidget, area: QWidget, label: str = '') -> None:
        rect = rectIn(widget, area)
        bounds = QRect(QPoint(0, 0), area.size())
        self.assertTrue(
            bounds.contains(rect), f"{label or widget.objectName() or type(widget).__name__} {rect} outside {bounds}"
        )

    def assertNoOverlap(self, widgets: list[QWidget], ancestor: QWidget) -> None:
        shown = [widget for widget in widgets if widget.isVisible() and widget.width() > 0 and widget.height() > 0]
        for index, first in enumerate(shown):
            for second in shown[index + 1 :]:
                self.assertFalse(
                    rectIn(first, ancestor).intersects(rectIn(second, ancestor)),
                    f"{type(first).__name__} {rectIn(first, ancestor)} overlaps "
                    f"{type(second).__name__} {rectIn(second, ancestor)}",
                )


class PreviewLayoutTests(LayoutTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.picture = str(Path(temporary.name, "picture.png"))
        pixmap = QPixmap(1600, 900)
        pixmap.fill(QColor("darkgreen"))
        pixmap.save(self.picture)
        self.preview = offscreen(ModPreview(), 300, 260)
        self.addCleanup(self.preview.close)

    def check(self, situation: str) -> None:
        settle(self.app)
        viewport = self.preview.scroll.viewport()
        parts = [self.preview.image, self.preview.title, self.preview.meta, self.preview.warning]
        parts += [self.preview.summary, self.preview.link]
        with self.subTest(situation=situation):
            for part in parts:
                if part.isVisible() and part.height() > 0:
                    # inside the visible width; the height may scroll
                    rect = rectIn(part, viewport)
                    self.assertGreaterEqual(rect.left(), 0, situation)
                    self.assertLessEqual(rect.right(), viewport.width() - 1, f"{situation}: text is cut off")
            self.assertNoOverlap(parts, viewport)
            self.assertLessEqual(self.preview.image.height(), IMAGE_HEIGHT)

    def test_text_never_hides_behind_the_scrollbar_or_overlaps(self):
        self.preview.showDetails(SHORT, "", True)
        self.check("short text")
        self.preview.showDetails(LONG, self.picture, True)
        self.check("long text, the scrollbar appears")
        self.assertTrue(self.preview.scroll.verticalScrollBar().isVisible())
        self.preview.showDetails(SHORT, "", True)
        self.check("short text again, the scrollbar goes")
        self.preview.resize(120, 260)
        self.preview.showDetails(LONG, self.picture, True)
        self.check("narrower than allowed")
        self.preview.resize(700, 900)
        self.check("wide and tall")
        self.assertFalse(self.preview.scroll.verticalScrollBar().isVisible())

    def test_picture_keeps_its_shape(self):
        self.preview.resize(400, 600)
        self.preview.showDetails(LONG, self.picture, True)
        settle(self.app)
        shown = self.preview.image.pixmap()
        self.assertAlmostEqual(shown.width() / shown.height(), 1600 / 900, delta=0.02)


class DownloadsLayoutTests(LayoutTestCase):
    def test_list_preview_and_buttons_fit(self):
        with tempfile.TemporaryDirectory() as folder:
            downloads = offscreen(DownloadsWidget(None, DetailsCache(folder), lambda: (folder, []), True), 1100, 300)
            self.addCleanup(downloads.close)
            downloads.refresh()
            settle(self.app)
            for widget in (downloads.searchBox, downloads.otherFilesBox, downloads.refreshButton, downloads.tree):
                self.assertInside(widget, downloads)
            buttons = [downloads.folderButton, downloads.pageButton, downloads.recycleButton, downloads.installButton]
            for widget in [downloads.preview, downloads.status, *buttons]:
                self.assertInside(widget, downloads)
            self.assertNoOverlap([downloads.tree, downloads.preview], downloads)
            self.assertNoOverlap([downloads.status, *buttons], downloads)


class BrowseLayoutTests(LayoutTestCase):
    def test_results_preview_and_buttons_fit(self):
        with tempfile.TemporaryDirectory() as folder:
            browse = offscreen(BrowseWidget(None, DetailsCache(folder), lambda: set(), True), 1100, 300)
            self.addCleanup(browse.close)
            with patch("src.gui.browse_widget.NexusWorker"):
                browse.showResults(2, [SHORT, LONG])
            settle(self.app)
            for widget in (browse.searchBox, browse.tree, browse.preview, browse.status, browse.moreButton):
                self.assertInside(widget, browse)
            for widget in (browse.openButton, browse.installButton, browse.searchButton):
                self.assertInside(widget, browse)
            self.assertNoOverlap([browse.tree, browse.preview], browse)
            self.assertNoOverlap([browse.moreButton, browse.openButton, browse.installButton], browse)
            self.assertGreater(browse.preview.width(), 250, "the preview should have room for its text")


class MainWindowLayoutTests(LayoutTestCase):
    def test_default_window(self):
        from src.configuration.config import Configuration
        from src.core.model import Model
        from src.gui.main_widget import CustomMainWidget
        from src.gui.main_window import CustomMainWindow

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "The Witcher 3"
            for folder in ("content", "Mods", "DLC", "bin/x64_dx12", "bin/config/r4game/user_config_matrix/pc"):
                (game / folder).mkdir(parents=True)
            (game / "bin/x64_dx12/witcher3.exe").write_bytes(b"")
            (root / "Documents").mkdir()
            with patch.object(Configuration, "write_config"), patch.object(Configuration, "write_priority"):
                config = Configuration(str(root / "Documents"), str(game), str(root / "Manager"))
                with patch("src.globals.data.config", config):
                    window = CustomMainWindow()
                    widget = CustomMainWidget(window, Model(ignorelock=True))
                    try:
                        offscreen(window, 1024, 720)
                        settle(self.app)
                        self.checkMainWindow(window, widget)
                    finally:
                        # close while the configuration still exists, so delayed saves don't outlive the test
                        window.close()
                        widget.modsSettingsWatcher.stop()
                        settle(self.app)
                        QTest.qWait(300)

    def checkMainWindow(self, window, widget) -> None:
        tabs = [widget.tabs.tabText(i).split(" (")[0] for i in range(widget.tabs.count())]
        self.assertEqual(tabs, ["Installed", "Downloads", "Browse Nexus"])
        # the top bar: the page's title, Check for Updates, Add Mod, Script Merger and Play
        self.assertEqual(widget.pageTitle.text(), "Mods")
        self.assertIn("installed", widget.pageSubtitle.text())
        topButtons = [widget.checkButton, widget.addButton, widget.scriptMergerButton, widget.runGameButton]
        self.assertEqual(
            [button.text() for button in topButtons], ["Check for Updates", "Add Mod", "Script Merger", "Play"]
        )
        self.assertNoOverlap([widget.pageTitle] + topButtons, window)
        lefts = [rectIn(button, window).left() for button in topButtons]
        self.assertEqual(lefts, sorted(lefts))
        self.assertFalse(widget.updateButton.isVisible(), "Update shows only when updates are waiting")
        self.assertLess(rectIn(widget.pageTitle, window).right(), rectIn(widget.checkButton, window).left())
        self.assertIn("(casual fork 0.2, source)", window.windowTitle())
        self.assertIs(widget.menubar.cornerWidget(Qt.Corner.TopRightCorner), widget.forkLabel)
        self.assertTrue(widget.forkLabel.isVisible())
        self.assertInside(widget.forkLabel, window)
        labels = [widget.treeWidget.headerItem().text(i) for i in range(widget.treeWidget.columnCount())]
        self.assertEqual(len(labels), 17)
        self.assertTrue(all(labels[1:15]), labels)
        self.assertEqual(labels[12:], ["Version", "Update", "Category", "", "Contents"])
        self.assertFalse(widget.treeWidget.headerItem().icon(15).isNull(), "the endorse column shows a thumb")
        self.assertGreaterEqual(widget.treeWidget.header().sectionSize(15), 24)
        header = widget.treeWidget.header()
        self.assertEqual(header.visualIndex(16), header.visualIndex(2) + 1, "Contents comes after Priority")
        self.assertEqual([c for c in range(17) if header.isSectionHidden(c)], [3, 4, 5, 6, 7, 8, 9])
        # the section rail down the left replaces the tabs and the old toolbar's other buttons
        self.assertFalse(widget.tabs.tabBar().isVisible())
        rail = widget.rail
        self.assertEqual(
            list(rail.buttons),
            [
                "mods",
                "downloads",
                "nexus",
                "loadorder",
                "health",
                "saves",
                "history",
                "folders",
                "config",
                "tools",
                "settings",
            ],
        )
        self.assertLessEqual(rail.sizeHint().height(), widget.railScroll.height(), "all of it fits at 720 high")
        self.assertTrue(rail.buttons["mods"].isChecked())
        self.assertEqual(rail.buttons["downloads"].text(), "Downloads")
        self.assertEqual(rectIn(rail, window).left(), 0)
        self.assertLess(rectIn(rail, window).right(), rectIn(widget.tabs, window).left())
        for button in rail.buttons.values():
            self.assertInside(button, rail)
        self.assertNoOverlap(list(rail.buttons.values()), rail)
        rail.buttons["nexus"].click()
        self.assertIs(widget.tabs.currentWidget(), widget.browse)
        widget.tabs.setCurrentWidget(widget.downloads)
        self.assertTrue(rail.buttons["downloads"].isChecked(), "the rail follows the page shown")
        rail.buttons["mods"].click()
        settle(self.app)

        # Play sits at the right end of the top bar
        self.assertGreater(rectIn(widget.runGameButton, window).right(), window.width() - 40)
        widget.tabs.setCurrentWidget(widget.browse)
        self.assertEqual(widget.pageTitle.text(), "Nexus")
        widget.tabs.setCurrentWidget(widget.installedTab)
        self.assertEqual(widget.runGameButton.text(), "Play")
        self.assertGreaterEqual(widget.runGameButton.width(), widget.runGameButton.sizeHint().width())

        # search, then the filter buttons
        self.assertEqual(
            [widget.filterButtons[name].text().split()[0] for name in ("all", "enabled", "disabled", "updates")],
            ["All", "Enabled", "Disabled", "Updates"],
        )
        self.assertTrue(widget.filterButtons["all"].isChecked())
        buttons = [widget.searchWidget] + list(widget.filterButtons.values())
        self.assertNoOverlap(buttons, window)
        for button in buttons:
            self.assertInside(button, window)

        # the side panel shows the details, or the load order in the same place
        side = widget.sidePanel
        self.assertNoOverlap([widget.treeWidget, side], widget.horizontalSplitter_tree)
        self.assertGreaterEqual(side.width(), 320)
        self.assertTrue(widget.preview.isVisible())
        self.assertFalse(widget.loadOrder.isVisible())
        self.assertGreaterEqual(widget.preview.height(), 300, "the details need room for the picture")
        rail.buttons["loadorder"].click()
        settle(self.app)
        self.assertTrue(widget.loadOrder.isVisible())
        self.assertFalse(widget.preview.isVisible())
        self.assertTrue(rail.buttons["loadorder"].isChecked())
        self.assertTrue(widget.sideButtons["loadorder"].isChecked())
        self.assertEqual(rectIn(widget.loadOrder, side).top(), rectIn(widget.preview, side).top())
        widget.sideButtons["details"].click()
        settle(self.app)
        self.assertTrue(widget.preview.isVisible())
        self.assertFalse(rail.buttons["loadorder"].isChecked(), "the rail button follows the panel")
        rail.buttons["loadorder"].click()
        rail.buttons["loadorder"].click()
        self.assertTrue(widget.preview.isVisible(), "a second click goes back to the details")

        # the log is closed until asked for; the status row runs along the bottom
        self.assertFalse(widget.textEdit.isVisible())
        self.assertEqual(rectIn(widget.statusRow, window).bottom(), window.height() - 1)
        self.assertLess(rectIn(widget.tabs, window).bottom(), rectIn(widget.statusRow, window).top())
        widget.logButton.setChecked(True)
        settle(self.app)
        self.assertTrue(widget.textEdit.isVisible())
        self.assertNoOverlap([widget.tabs, widget.textEdit, widget.statusRow], window)
        for part in (widget.tabs, widget.textEdit, widget.statusRow, widget.runGameButton, widget.logButton):
            self.assertInside(part, window)


if __name__ == "__main__":
    unittest.main()
