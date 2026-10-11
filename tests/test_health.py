import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.core.health import ERROR, INFO, WARNING, Issue, checkHealth
from src.core.scriptmerger import NOT_MERGED, ConflictReport, ScriptConflict
from src.domain.mod import Mod
from src.gui.health_dialog import HealthDialog
from src.gui.main_widget import CustomMainWidget


def mod(name, files=(), dlcs=(), menus=(), enabled=True, inis=()):
    result = Mod(files=list(files), dlcs=list(dlcs), menus=list(menus), inis=list(inis))
    result._name = name
    result.enabled = enabled
    return result


class HealthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.mods, self.dlc, self.menu = root / "Mods", root / "DLC", root / "menu"
        for folder in (self.mods, self.dlc, self.menu):
            folder.mkdir()
        self.priority = configparser.ConfigParser()
        self.config = SimpleNamespace(
            mods=str(self.mods), dlc=str(self.dlc), menu=str(self.menu), priority=self.priority
        )

    def folders(self, *names):
        for name in names:
            (self.mods / name).mkdir()

    def check(self, *mods, scripts=None):
        model = SimpleNamespace(all=lambda: list(mods))
        return [(i.severity, i.mod, i.problem, i.detail) for i in checkHealth(model, self.config, scripts)]

    def test_healthy_setup(self):
        self.folders("modA", "~modB", "mod0000_MergedFiles")
        (self.dlc / "dlcA").mkdir()
        (self.menu / "a.xml").write_text("")
        (self.menu / "b.xml.disabled").write_text("")
        self.priority.read_string("[modA]\npriority=1\n[modB]\npriority=2\n")
        issues = self.check(
            mod("A", ["modA"], ["dlcA"], ["a.xml", "input.xml"]),
            mod("B", ["modB"], menus=["b.xml"], enabled=False),
            mod("Only settings", inis=["rendering.ini"]),
        )
        self.assertEqual(issues, [])

    def test_problems_are_found_and_ordered(self):
        self.folders(
            "modOnlyDisabled", "~modOnlyDisabled2", "modTwice", "~modTwice", "modShared", "modStray", "~modStray2"
        )
        (self.mods / "readme.txt").write_text("")
        self.priority.read_string("[modGone]\npriority=1\n[modStray]\npriority=2\n[General]\nx=1\n")
        conflicts = ConflictReport([ScriptConflict("a.ws", ["modA", "modB"], NOT_MERGED)], True)
        issues = self.check(
            mod("Missing", ["modMissing"], ["dlcMissing"], ["gone.xml"]),
            mod("Enabled But Off", ["modOnlyDisabled2"]),
            mod("Disabled But On", ["modOnlyDisabled"], enabled=False),
            mod("Twice", ["modTwice"]),
            mod("Empty"),
            mod("Share One", ["modShared"]),
            mod("Share Two", ["modShared"]),
            scripts=conflicts,
        )
        self.assertEqual(
            issues,
            [
                (ERROR, "Missing", "DLC folder is missing", "DLC/dlcMissing isn't in the game folder"),
                (ERROR, "Missing", "Mod folder is missing", "Mods/modMissing isn't in the game folder"),
                (WARNING, "", "Scripts need Script Merger",
                 "1 script files aren't merged or the merge is out of date (Mods > Script Conflicts)"),
                (WARNING, "Disabled But On", "Listed as disabled, but enabled", "Mods/modOnlyDisabled is still enabled"),
                (WARNING, "Empty", "Entry has no files", "Nothing is installed for it"),
                (WARNING, "Enabled But Off", "Listed as enabled, but disabled", "Only Mods/~modOnlyDisabled2 exists"),
                (WARNING, "Missing", "Menu file is missing", "gone.xml isn't in the game's menu folder"),
                (WARNING, "Share One", "Installed twice?", "Share One shares modShared with Share Two"),
                (WARNING, "Share Two", "Installed twice?", "Share Two shares modShared with Share One"),
                (WARNING, "Twice", "Folder is there twice", "Both Mods/modTwice and Mods/~modTwice exist; one is left over"),
                (INFO, "", "Folder isn't managed here",
                 "Mods/modStray isn't part of any mod in the list (installed by hand or by another manager)"),
                (INFO, "", "Folder isn't managed here",
                 "Mods/~modStray2 isn't part of any mod in the list (installed by hand or by another manager)"),
                (INFO, "", "Leftover priority", "mods.settings sets a priority for modGone, which isn't installed"),
            ],
        )  # fmt: skip

    def test_unconfigured_game(self):
        config = SimpleNamespace(mods=None, dlc=None, menu=None)
        model = SimpleNamespace(all=lambda: [mod("A", ["modA"], ["dlcA"], ["a.xml"])])
        self.assertEqual(checkHealth(model, config), [])


class HealthGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_rows_links_and_refresh(self):
        issues = [Issue(ERROR, "A", "Mod folder is missing", "Mods/modA"), Issue(INFO, "", "Note", "x", "https://n/1")]
        refresh = Mock(return_value=[])
        dialog = HealthDialog(None, issues, refresh)
        self.assertIn("1 problems, 0 warnings, 1 notes", dialog.heading.text())
        rows = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
        self.assertEqual(
            [(row.text(0), row.text(2)) for row in rows], [("Problem", "Mod folder is missing"), ("Note", "Note")]
        )
        with patch("src.gui.health_dialog.openUrl") as opened:
            dialog.openLink(rows[0])
            dialog.openLink(rows[1])
        opened.assert_called_once_with("https://n/1")
        self.assertEqual(rows[1].data(0, Qt.ItemDataRole.UserRole), "https://n/1")
        dialog.refreshButton.click()
        refresh.assert_called_once()
        self.assertEqual(dialog.tree.topLevelItemCount(), 0)
        self.assertEqual(dialog.heading.text(), "No problems found.")

    def test_handlers(self):
        widget = Mock()
        config = SimpleNamespace()
        with patch("src.gui.main_widget.data.config", config), patch("src.gui.main_widget.checkHealth") as check:
            check.return_value = [Issue(INFO, "", "Note"), Issue(WARNING, "A", "W"), Issue(ERROR, "B", "E")]
            self.assertEqual(len(CustomMainWidget.healthIssues(widget)), 3)
            check.assert_called_once_with(widget.model, config, widget.scriptConflictReport.return_value)
            check.side_effect = OSError("denied")
            self.assertEqual(CustomMainWidget.healthIssues(widget), [])
        self.assertIn("denied", widget.output.call_args.args[0])

        widget.healthIssues.return_value = [Issue(INFO, "", "Note"), Issue(WARNING, "A", "W"), Issue(ERROR, "B", "E")]
        CustomMainWidget.reportHealth(widget)
        self.assertIn("Health check: 2 problems or warnings", widget.output.call_args.args[0])
        widget.output.reset_mock()
        widget.healthIssues.return_value = [Issue(INFO, "", "Note")]
        CustomMainWidget.reportHealth(widget)
        widget.output.assert_not_called()

        with patch("src.gui.main_widget.HealthDialog") as dialog:
            CustomMainWidget.showHealthCheck(widget)
        dialog.assert_called_once_with(widget, widget.healthIssues.return_value, widget.healthIssues)


if __name__ == "__main__":
    unittest.main()
