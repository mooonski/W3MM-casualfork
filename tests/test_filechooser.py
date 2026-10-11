import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication

from src.core.nexus import UP_TO_DATE, UPDATE_AVAILABLE, ModFiles, NexusFile, NexusInfo
from src.domain.mod import Mod
from src.gui.main_widget import CustomMainWidget
from src.gui.nexus_dialogs import RECORD, SWITCH, NexusFileDialog


def file(fileId, category="MAIN", uploaded=100, name=None, version="1.0", size=2 * 1048576):
    return NexusFile(
        fileId, name or f"File {fileId}", version, category, False, uploaded, f"f{fileId}.zip", size, "notes"
    )


FILES = ModFiles(
    {
        1: file(1, "OLD_VERSION", 50),
        2: file(2, "MAIN", 300, version="2.0"),
        3: file(3, "OPTIONAL", 400, size=5000),
        4: file(4, "ARCHIVED", 60),
    },
    {1: 2},
)


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def rows(self, dialog):
        tree = dialog.tree
        return [[tree.topLevelItem(i).text(c) for c in range(6)] for i in range(tree.topLevelItemCount())]

    def test_rows_marks_and_selection(self):
        dialog = NexusFileDialog(None, "Example", FILES, FILES.files[2], True)
        self.assertEqual(
            [(row[0], row[2], row[4], row[5]) for row in self.rows(dialog)],
            [
                ("File 3", "Optional", "4 KB", ""),
                ("File 2", "Main", "2.0 MB", "Yes"),
                ("File 4", "Archived", "2.0 MB", ""),
                ("File 1", "Old Version", "2.0 MB", ""),
            ],
        )
        self.assertEqual(dialog.selected().fileId, 2)
        greyed = dialog.tree.topLevelItem(3).foreground(0).color().name()
        self.assertEqual(greyed, "#a0a0a0")
        self.assertEqual(dialog.tree.topLevelItem(0).toolTip(0), "notes")

    def test_guess_unknown_and_empty(self):
        guess = NexusFileDialog(None, "Example", FILES, FILES.files[1], False)
        self.assertEqual(self.rows(guess)[3][5], "Probably")
        self.assertEqual(guess.selected().fileId, 1)
        unknown = NexusFileDialog(None, "Example", FILES, None, False)
        self.assertEqual(unknown.selected().fileId, 3)
        self.assertTrue(all(row[5] == "" for row in self.rows(unknown)))
        empty = NexusFileDialog(None, "Example", ModFiles(), None, False)
        self.assertIsNone(empty.selected())
        self.assertFalse(empty.recordButton.isEnabled())
        self.assertFalse(empty.switchButton.isEnabled())
        empty.finish(RECORD)
        self.assertEqual(empty.choice, "")

    def test_choices(self):
        for button, choice in (("recordButton", RECORD), ("switchButton", SWITCH)):
            dialog = NexusFileDialog(None, "Example", FILES, None, False)
            dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1))
            getattr(dialog, button).click()
            self.assertEqual((dialog.choice, dialog.result(), dialog.selected().fileId), (choice, 1, 2))
        dialog = NexusFileDialog(None, "Example", FILES, None, False)
        dialog.tree.itemDoubleClicked.emit(dialog.tree.topLevelItem(2), 0)
        self.assertEqual((dialog.choice, dialog.selected().fileId), (RECORD, 4))

    def test_layout(self):
        dialog = NexusFileDialog(None, "Example", FILES, FILES.files[2], True)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        dialog.resize(1000, 480)
        dialog.show()
        for _ in range(5):
            self.app.processEvents()

        def rectIn(widget):
            return QRect(widget.mapTo(dialog, QPoint(0, 0)), widget.size())

        for widget in (dialog.tree, dialog.recordButton, dialog.switchButton):
            self.assertTrue(dialog.rect().contains(rectIn(widget)), f"{widget} is outside the dialog")
        self.assertLess(rectIn(dialog.tree).bottom(), rectIn(dialog.recordButton).top())
        self.assertFalse(rectIn(dialog.recordButton).intersects(rectIn(dialog.switchButton)))
        dialog.close()

    def test_category_names(self):
        self.assertEqual(file(1, "OLD_VERSION").categoryName, "Old Version")
        self.assertIn("[Miscellaneous]", file(1, "MISCELLANEOUS").label())


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.widget = Mock()
        self.widget.nexusBusy.return_value = False
        self.widget.updateChecks = {}
        self.mod = Mod(nexus=NexusInfo(5, archive="Example 5 1.0 x.zip", game="witcher3"))
        self.mod._name = "Example"
        self.mod.date = "2020-01-01 00:00:00"
        plain = Mod()
        plain._name = "Plain"
        self.widget.model = SimpleNamespace(modList={"Example": self.mod, "Plain": plain}, write=Mock())
        self.widget.model.get = lambda name: self.widget.model.modList[name]

    def outputs(self):
        return "\n".join(str(call.args[0]) for call in self.widget.output.call_args_list)

    def test_guards_and_fetch(self):
        for selected in ([], ["Example", "Plain"]):
            self.widget.getSelectedMods.return_value = selected
            CustomMainWidget.chooseInstalledFile(self.widget)
        self.assertEqual(self.outputs().count("Select one mod."), 2)
        self.widget.getSelectedMods.return_value = ["Plain"]
        CustomMainWidget.chooseInstalledFile(self.widget)
        self.assertIn("Plain: no Nexus ID", self.outputs())
        self.widget.getSelectedMods.return_value = ["Example"]
        self.widget.nexusClient.return_value = None
        CustomMainWidget.chooseInstalledFile(self.widget)
        self.widget.runNexus.assert_not_called()
        client = Mock()
        self.widget.nexusClient.return_value = client
        CustomMainWidget.chooseInstalledFile(self.widget)
        work, done = self.widget.runNexus.call_args.args
        work(Mock())
        client.modFiles.assert_called_once_with(5)
        done(FILES)
        self.widget.installedFileChosen.assert_called_once_with("Example", FILES)

    def choose(self, choice, fileId, accepted=True):
        with patch("src.gui.main_widget.NexusFileDialog") as dialog:
            dialog.return_value.exec.return_value = accepted
            dialog.return_value.choice = choice
            dialog.return_value.selected.return_value = FILES.files[fileId]
            CustomMainWidget.installedFileChosen(self.widget, "Example", FILES)
        return dialog

    def test_record(self):
        dialog = self.choose(RECORD, 1)
        name, files, installed, recorded = dialog.call_args.args[1:]
        # nothing recorded yet, so the dialog gets the best guess, marked as not recorded
        self.assertEqual((name, installed, recorded), ("Example", FILES.files[3], False))
        self.assertEqual(self.mod.nexus, NexusInfo(5, 1, "1.0", "File 1", 50, "Example 5 1.0 x.zip", "witcher3"))
        self.assertEqual(self.widget.updateChecks["Example"].state, UPDATE_AVAILABLE)
        self.assertTrue(self.widget.updateChecks["Example"].confident)
        self.widget.model.write.assert_called_once()
        self.widget.refreshList.assert_called_once()
        self.assertIn("Example: recorded as File 1", self.outputs())
        dialog = self.choose(RECORD, 2)
        self.assertEqual(dialog.call_args.args[3:], (FILES.files[1], True))
        self.assertEqual(self.widget.updateChecks["Example"].state, UP_TO_DATE)

    def test_switch_and_cancel(self):
        self.choose(SWITCH, 3)
        self.widget.downloadAndUpdate.assert_called_once_with([("Example", FILES.files[3])])
        self.assertEqual(self.mod.nexus.fileId, 0)
        self.widget.downloadAndUpdate.reset_mock()
        self.choose(RECORD, 3, accepted=False)
        self.widget.downloadAndUpdate.assert_not_called()
        self.assertEqual(self.mod.nexus.fileId, 0)
        CustomMainWidget.installedFileChosen(self.widget, "Gone", FILES)
        self.widget.model.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
