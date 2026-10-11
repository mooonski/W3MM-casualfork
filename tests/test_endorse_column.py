import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QStyle, QTreeWidget, QTreeWidgetItem

from src.core.endorsements import EndorsementCache
from src.core.groups import uniqueName
from src.core.nexus import NexusError, NexusInfo
from src.domain.mod import Mod
from src.gui.main_widget import ENDORSE_COLUMN, CustomMainWidget
from src.gui.tree_widget import GroupItem


def mod(name, modId=0):
    result = Mod()
    result._name = name
    result.nexus = NexusInfo(modId, version="1.0") if modId else None
    return result


class Worker:
    '''Runs the work straight away instead of on a thread'''

    def __init__(self, function, parent=None):
        self.function = function
        self.succeeded = SimpleNamespace(connect=lambda slot: setattr(self, 'done', slot))
        self.failed = SimpleNamespace(connect=lambda slot: setattr(self, 'fail', slot))

    def isRunning(self):
        return False

    def start(self):
        try:
            result = self.function(lambda *args: None)
        except NexusError as error:
            self.fail(str(error))
        else:
            self.done(result)


class CacheTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = EndorsementCache(folder)
            self.assertFalse(cache.endorsed(5))
            cache.replace({5: "Endorsed", 6: "Abstained"})
            cache.set(7, "Endorsed")
            again = EndorsementCache(folder)
            self.assertEqual(again.statuses, {5: "Endorsed", 6: "Abstained", 7: "Endorsed"})
            self.assertTrue(again.endorsed(7))
            self.assertFalse(again.endorsed(6))
            self.assertFalse(again.endorsed(0))
            self.assertGreater(again.fetched, 0)
            Path(folder, "endorsements.json").write_text("{broken")
            self.assertEqual(EndorsementCache(folder).statuses, {})

    def test_unique_separator_names(self):
        self.assertEqual(uniqueName("Separator", []), "Separator")
        self.assertEqual(uniqueName("Separator", ["Separator", "Separator 2"]), "Separator 3")


class ColumnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.widget = Mock()
        self.widget.palette.return_value = self.app.palette()
        self.widget.endorsements = EndorsementCache(temporary.name)
        self.widget.endorsements.replace({5: "Endorsed"})
        self.mods = {m.name: m for m in (mod("Liked", 5), mod("Other", 6), mod("Manual"))}
        self.widget.model = SimpleNamespace(modList=self.mods)
        self.widget.treeWidget = QTreeWidget()
        self.widget.treeWidget.setColumnCount(16)
        self.items = {}
        for name in self.mods:
            item = QTreeWidgetItem(["", name] + [""] * 14)
            self.widget.treeWidget.addTopLevelItem(item)
            self.items[name] = item
        self.widget.modItems = lambda: list(self.items.values())
        self.widget.showEndorsement = lambda item, m: CustomMainWidget.showEndorsement(self.widget, item, m)
        self.widget.showEndorsements = lambda: CustomMainWidget.showEndorsements(self.widget)
        self.widget.endorseWorker = None
        self.widget.endorsementsWorker = None

    def image(self, name):
        return self.items[name].icon(ENDORSE_COLUMN).pixmap(16, 16).toImage()

    def test_filled_when_endorsed_outline_otherwise(self):
        CustomMainWidget.showEndorsements(self.widget)
        self.assertFalse(self.items["Liked"].icon(ENDORSE_COLUMN).isNull())
        self.assertFalse(self.items["Other"].icon(ENDORSE_COLUMN).isNull())
        self.assertTrue(self.items["Manual"].icon(ENDORSE_COLUMN).isNull(), "no thumb without a Nexus page")
        self.assertNotEqual(self.image("Liked"), self.image("Other"))
        self.assertIn("You endorsed", self.items["Liked"].toolTip(ENDORSE_COLUMN))

    def test_clicking_the_thumb(self):
        CustomMainWidget.modClicked(self.widget, self.items["Other"], ENDORSE_COLUMN)
        self.widget.toggleEndorsement.assert_called_once_with("Other")
        CustomMainWidget.modClicked(self.widget, self.items["Other"], 1)
        CustomMainWidget.modClicked(self.widget, GroupItem("Visuals", 0), ENDORSE_COLUMN)
        self.widget.toggleEndorsement.assert_called_once()

    def test_endorse_and_take_back(self):
        client = self.widget.nexusClient.return_value
        client.endorse.side_effect = lambda modId, version, endorse: "Endorsed" if endorse else "Abstained"
        self.widget.endorsementSent = lambda *args: CustomMainWidget.endorsementSent(self.widget, *args)
        with (
            patch("src.gui.main_widget.NexusWorker", Worker),
            patch("src.gui.main_widget.QMessageBox.question") as question,
        ):
            CustomMainWidget.toggleEndorsement(self.widget, "Other")
            question.assert_not_called()  # endorsing needs no confirmation
            client.endorse.assert_called_with(6, "1.0", True)
            self.assertTrue(self.widget.endorsements.endorsed(6))
            self.assertIn("Endorsed Other", self.widget.output.call_args.args[0])
            self.assertFalse(self.items["Other"].icon(ENDORSE_COLUMN).isNull())

            question.return_value = QMessageBox.StandardButton.No
            CustomMainWidget.toggleEndorsement(self.widget, "Liked")
            self.assertTrue(self.widget.endorsements.endorsed(5))
            question.return_value = QMessageBox.StandardButton.Yes
            CustomMainWidget.toggleEndorsement(self.widget, "Liked")
            client.endorse.assert_called_with(5, "1.0", False)
            self.assertFalse(self.widget.endorsements.endorsed(5))
            self.assertIn("Took back", self.widget.output.call_args.args[0])

            client.endorse.side_effect = NexusError("Nexus only takes endorsements from people who downloaded it")
            CustomMainWidget.toggleEndorsement(self.widget, "Other")
            CustomMainWidget.toggleEndorsement(self.widget, "Manual")
        self.assertIn("Other: Nexus only takes", self.widget.output.call_args.args[0])

    def test_loading_endorsements(self):
        self.widget.endorsementsLoaded = lambda statuses: CustomMainWidget.endorsementsLoaded(self.widget, statuses)
        with (
            patch("src.gui.main_widget.NexusWorker", Worker),
            patch("src.gui.main_widget.data.config", SimpleNamespace(nexusapikey="")),
        ):
            CustomMainWidget.refreshEndorsements(self.widget)
        self.assertTrue(self.widget.endorsements.endorsed(5), "nothing is fetched without an API key")
        with (
            patch("src.gui.main_widget.NexusWorker", Worker),
            patch("src.gui.main_widget.data.config", SimpleNamespace(nexusapikey="key")),
            patch("src.gui.main_widget.NexusClient") as client,
        ):
            client.return_value.endorsements.return_value = {6: "Endorsed"}
            CustomMainWidget.refreshEndorsements(self.widget)
        self.assertEqual(self.widget.endorsements.statuses, {6: "Endorsed"})
        self.assertFalse(self.items["Other"].icon(ENDORSE_COLUMN).isNull())


class SeparatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_blank_space_menu_adds_a_plain_separator(self):
        widget = Mock()
        widget.model.all.return_value = [mod("A")]
        config = SimpleNamespace(groups=["Separator"])
        widget.saveGroups = lambda order: setattr(config, "groups", order)
        with patch("src.gui.main_widget.data.config", config):
            CustomMainWidget.addSeparator(widget)
            CustomMainWidget.addSeparator(widget)
        self.assertEqual(config.groups, ["Separator", "Separator 2", "Separator 3"])

        widget.treeWidget = QTreeWidget()
        shown = []

        class Menu(QMenu):
            def exec(self, *args):
                shown.append(self)

        with patch("src.gui.main_widget.QMenu", Menu):
            CustomMainWidget.openMenu(widget, QPoint(5, 5))
        self.assertEqual([action.text() for action in shown[0].actions()], ["Add Separator"])
        shown[0].actions()[0].trigger()
        widget.addSeparator.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class ToggleSafetyTests(unittest.TestCase):
    '''Changing anything but the tick on a mod's row must never enable or disable it'''

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_icons_and_colours_never_disable_a_mod(self):
        widget = Mock()
        enabled = Mock(enabled=True)
        widget.model.modList = {"Liked": enabled}
        widget.model.get = lambda name: widget.model.modList[name]
        tree = QTreeWidget()
        tree.setColumnCount(16)
        tree.itemChanged.connect(lambda item, column: CustomMainWidget.modToggled(widget, item, column))
        item = QTreeWidgetItem(["", "Liked"] + [""] * 14)
        item.setCheckState(0, Qt.CheckState.Checked)
        tree.addTopLevelItem(item)
        item.setIcon(ENDORSE_COLUMN, QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DialogYesButton))
        item.setForeground(1, QBrush(QColor("orange")))
        item.setText(13, "Update 2.0")
        enabled.disable.assert_not_called()
        enabled.enable.assert_not_called()
        item.setCheckState(0, Qt.CheckState.Unchecked)
        enabled.disable.assert_called_once()
