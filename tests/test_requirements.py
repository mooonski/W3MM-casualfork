import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from src.core.health import INFO, WARNING, checkHealth
from src.core.nexus import NexusInfo
from src.core.nexusdetails import GAME_ID, ModDetails, Requirement
from src.core.requirements import (
    DISABLED,
    DLC_MISSING,
    EXTERNAL,
    MISSING,
    MissingRequirement,
    findInstalled,
    missingRequirements,
)
from src.domain.mod import Mod
from src.gui.main_widget import CustomMainWidget
from src.gui.requirements_dialog import RequirementsDialog


def mod(name, modId=0, files=(), enabled=True):
    result = Mod(files=list(files))
    result._name = name
    result.enabled = enabled
    result.nexus = NexusInfo(modId) if modId else None
    return result


def model(*mods):
    return SimpleNamespace(all=lambda: list(mods))


SHAREDUTILS = Requirement("Sharedutils", 12997, GAME_ID, "Required to compile.")
RESHADE = Requirement("ReShade", external=True, url="https://reshade.me/")
BLOOD_AND_WINE = Requirement("Blood and Wine", dlc=True)


class RequirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dlc = Path(temporary.name) / "dlc"
        (self.dlc / "ep1").mkdir(parents=True)
        self.details = {
            1: ModDetails(1, "Brothers In Arms", requirements=[SHAREDUTILS, SHAREDUTILS, RESHADE]),
            2: ModDetails(2, "Wine Mod", requirements=[BLOOD_AND_WINE, Requirement("Hearts of Stone", dlc=True)]),
            3: ModDetails(3, "Self", requirements=[Requirement("Self", 3, GAME_ID)]),
            4: ModDetails(4, "Tool", requirements=[Requirement("Other game mod", 5, 1704)]),
        }

    def missing(self, *mods, names=None, external=True):
        return [
            (entry.mod, entry.requirement.name, entry.state)
            for entry in missingRequirements(model(*mods), self.details.get, str(self.dlc), names, external)
        ]

    def test_missing_nexus_mod_external_tool_and_expansion(self):
        found = self.missing(mod("Brothers In Arms", 1), mod("Wine Mod", 2), mod("Self", 3), mod("Tool", 4))
        self.assertEqual(
            found,
            [
                ("Brothers In Arms", "Sharedutils", MISSING),
                ("Brothers In Arms", "ReShade", EXTERNAL),
                ("Wine Mod", "Blood and Wine", DLC_MISSING),
            ],
        )
        self.assertEqual(self.missing(mod("Brothers In Arms", 1), external=False)[1:], [])

    def test_installed_by_id_name_or_folder(self):
        self.assertEqual(self.missing(mod("Brothers In Arms", 1), mod("Anything", 12997), external=False), [])
        self.assertEqual(self.missing(mod("Brothers In Arms", 1), mod("SharedUtils", 0), external=False), [])
        self.assertEqual(
            self.missing(mod("Brothers In Arms", 1), mod("Shared stuff", 0, ["modSharedutils"]), external=False), []
        )

    def test_disabled_requirement_and_disabled_mods(self):
        self.assertEqual(
            self.missing(mod("Brothers In Arms", 1), mod("Sharedutils", 12997, enabled=False), external=False),
            [("Brothers In Arms", "Sharedutils", DISABLED)],
        )
        # a disabled mod's requirements don't matter, unless that mod is asked about by name
        self.assertEqual(self.missing(mod("Brothers In Arms", 1, enabled=False)), [])
        self.assertEqual(len(self.missing(mod("Brothers In Arms", 1, enabled=False), names=["Brothers In Arms"])), 2)
        self.assertEqual(self.missing(mod("Brothers In Arms", 1), names=["Other"]), [])

    def test_remastered_keeps_expansions_as_tombstones(self):
        (self.dlc.parent / "dlc-tombstones" / "bob").mkdir(parents=True)
        self.assertEqual(self.missing(mod("Wine Mod", 2)), [])

    def test_unknown_game_folder_reports_no_expansion(self):
        found = missingRequirements(model(mod("Wine Mod", 2)), self.details.get, "", None)
        self.assertEqual(found, [])

    def test_name_matching_needs_enough_letters(self):
        self.assertEqual(findInstalled(model(mod("UI", 0, ["modUI"])), Requirement("Ultimate UI Pack", 9, GAME_ID)), [])
        self.assertEqual(
            [m.name for m in findInstalled(model(mod("A", 9), mod("B", 0)), Requirement("B", 9, GAME_ID))], ["A"]
        )
        self.assertEqual(findInstalled(model(mod("Sharedutils", 0)), SHAREDUTILS, exclude="Sharedutils"), [])

    def test_problem_texts(self):
        self.assertEqual(MissingRequirement("A", SHAREDUTILS, MISSING).problem, "Needs Sharedutils")
        self.assertEqual(
            MissingRequirement("A", SHAREDUTILS, DISABLED).problem, "Sharedutils is installed but disabled"
        )
        self.assertEqual(
            MissingRequirement("A", BLOOD_AND_WINE, DLC_MISSING).problem, "Needs the Blood and Wine expansion"
        )
        self.assertIn("outside Nexus", MissingRequirement("A", RESHADE, EXTERNAL).problem)
        self.assertEqual(MissingRequirement("A", Requirement("", 7, GAME_ID), MISSING).problem, "Needs Nexus mod 7")

    def test_health_check_lists_them(self):
        config = SimpleNamespace(mods="", dlc=str(self.dlc), menu="", priority=None, gameversion="re")
        issues = checkHealth(model(mod("Brothers In Arms", 1, ["modBIA"])), config, None, self.details.get)
        found = [(i.severity, i.problem, i.url) for i in issues if "Needs" in i.problem or "outside" in i.problem]
        self.assertEqual(
            found,
            [
                (WARNING, "Needs Sharedutils", "https://www.nexusmods.com/witcher3/mods/12997"),
                (INFO, "Also needs ReShade (from outside Nexus)", "https://reshade.me/"),
            ],
        )


class RequirementsGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def entries(self):
        return [
            MissingRequirement("Brothers In Arms", RESHADE, EXTERNAL),
            MissingRequirement("Brothers In Arms", SHAREDUTILS, MISSING),
            MissingRequirement("Wine Mod", BLOOD_AND_WINE, DLC_MISSING),
        ]

    def test_dialog_rows_and_install(self):
        dialog = RequirementsDialog(None, self.entries())
        self.assertIn("2 requirements not met, 1 tool from outside Nexus", dialog.heading.text())
        rows = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
        self.assertEqual([row.text(2) for row in rows], ["Check yourself", "Not installed", "Expansion missing"])
        # the first row that can be installed is selected
        self.assertIs(dialog.tree.currentItem(), rows[1])
        self.assertTrue(dialog.installButton.isEnabled())
        dialog.tree.setCurrentItem(rows[2])
        self.assertFalse(dialog.installButton.isEnabled())
        self.assertFalse(dialog.pageButton.isEnabled())
        dialog.tree.setCurrentItem(rows[0])
        with patch("src.gui.requirements_dialog.openUrl") as opened:
            dialog.pageButton.click()
        opened.assert_called_once_with("https://reshade.me/")
        dialog.tree.setCurrentItem(rows[1])
        dialog.installButton.click()
        self.assertEqual(dialog.chosen.requirement, SHAREDUTILS)

    def test_requirements_loaded(self):
        widget = Mock()
        widget.nexusDetails.get = {1: ModDetails(1, requirements=[SHAREDUTILS])}.get
        widget.model = model(mod("Brothers In Arms", 1))
        with (
            patch("src.gui.main_widget.data.config", SimpleNamespace(dlc="")),
            patch("src.gui.main_widget.RequirementsDialog") as dialog,
        ):
            dialog.return_value.exec.return_value = True
            dialog.return_value.chosen = MissingRequirement("Brothers In Arms", SHAREDUTILS, MISSING)
            CustomMainWidget.requirementsLoaded(widget, {1: "found"}, None, False)
        widget.detailsFetched.assert_called_once_with({1: "found"}, True)
        installed = widget.installFromNexus.call_args.args[0]
        self.assertEqual((installed.modId, installed.name), (12997, "Sharedutils"))
        self.assertIn("Brothers In Arms: Needs Sharedutils", [c.args[0].strip() for c in widget.output.call_args_list])

    def test_nothing_missing_after_install_stays_quiet(self):
        widget = Mock()
        widget.nexusDetails.get = {1: ModDetails(1)}.get
        widget.model = model(mod("Brothers In Arms", 1))
        with (
            patch("src.gui.main_widget.data.config", SimpleNamespace(dlc="")),
            patch("src.gui.main_widget.RequirementsDialog") as dialog,
        ):
            CustomMainWidget.requirementsLoaded(widget, {}, ["Brothers In Arms"], True)
            dialog.assert_not_called()
            widget.output.assert_not_called()
            CustomMainWidget.requirementsLoaded(widget, {}, None, False)
        self.assertIn("Every requirement", widget.output.call_args.args[0])

    def test_after_install_checks_the_installed_nexus_mods(self):
        widget = Mock()
        widget.model = model(mod("Brothers In Arms", 11260), mod("Other", 5))
        CustomMainWidget.checkRequirementsAfterInstall(
            widget, ["Z:/mods/Brothers In Arms 11260 4.0.4 2026-10-08T12-00Z abc123.zip", "Z:/mods/manual.zip"]
        )
        widget.checkRequirements.assert_called_once_with(["Brothers In Arms"], afterInstall=True)
        widget.checkRequirements.reset_mock()
        CustomMainWidget.checkRequirementsAfterInstall(widget, ["Z:/mods/manual.zip"])
        widget.checkRequirements.assert_not_called()


if __name__ == "__main__":
    unittest.main()
