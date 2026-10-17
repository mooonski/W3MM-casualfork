import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.configuration.config import Configuration
from src.core.health import WARNING, Fix, Issue, checkHealth, sharedutilsIssues
from src.core.sharedutils import checkSharedutils, fixedPriorities
from src.domain.mod import Mod
from src.gui.health_dialog import HealthDialog
from src.gui.main_widget import CustomMainWidget

SHAREDUTILS = """
class SU_MenuDescriptor extends CR4ListBaseMenu { }
class SU_GlossaryEntry { }
struct SU_MenuDescriptor_OptionDescription { var label: string; }
function SU_removeCustomPinByTag(tag: name) { }
"""
RER = """
class RER_MenuDescriptor extends SU_MenuDescriptor { }
class RER_GlossaryEntry extends SU_GlossaryEntry { }
function RER_cleanup() { SU_removeCustomPinByTag('RER'); }
"""
BIA = "class BIA_MenuDescriptor extends SU_MenuDescriptor { }\n"


def mod(name, *files):
    result = Mod(files=list(files))
    result._name = name
    return result


class SharedutilsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.mods = Path(temporary.name) / "Mods"
        self.mods.mkdir()
        self.priority = configparser.ConfigParser()
        self.written = Mock()
        self.config = SimpleNamespace(
            mods=str(self.mods),
            dlc="",
            menu="",
            priority=self.priority,
            gameversion="re",
            setPriority=self.setPriority,
            write_priority=self.written,
        )

    def setPriority(self, section, value):
        if not self.priority.has_section(section):
            self.priority.add_section(section)
        self.priority.set(section, "priority", value)

    def script(self, folder, text, name="main.ws", encoding="utf-8"):
        target = self.mods / folder / "content" / "scripts" / "local" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode(encoding))

    def priorities(self, **values):
        for section, value in values.items():
            self.setPriority(section, str(value))

    def get(self, section):
        if self.priority.has_section(section) and self.priority.has_option(section, "priority"):
            return self.priority.get(section, "priority")
        return None

    def check(self):
        return checkSharedutils(str(self.mods), self.get)

    def standardSetup(self):
        '''The real setup that failed to compile: Sharedutils and Brothers in Arms at 10, RER with no priority'''
        self.script("modzzz_sharedutils", SHAREDUTILS)
        self.script("modrandom_encounters_reworked", RER)
        self.script("modbrothersinarms", BIA)
        self.script("modOther", "class Other { }\n")
        self.priorities(modzzz_sharedutils=10, modbrothersinarms=10, modOther=5)

    def test_rer_without_priority_loads_after_sharedutils(self):
        self.standardSetup()
        check = self.check()
        self.assertEqual(check.sharedutils, "modzzz_sharedutils")
        self.assertEqual(
            check.dependents,
            {
                "modbrothersinarms": ["SU_MenuDescriptor"],
                "modrandom_encounters_reworked": ["SU_GlossaryEntry", "SU_MenuDescriptor", "SU_removeCustomPinByTag"],
            },
        )
        # Brothers in Arms has the same number and comes first by name, which compiles
        self.assertEqual(check.late, ["modrandom_encounters_reworked"])

    def test_higher_number_or_same_number_later_by_name(self):
        self.standardSetup()
        self.priorities(modrandom_encounters_reworked=0, modbrothersinarms=11)
        self.assertEqual(self.check().late, ["modbrothersinarms"])
        self.priorities(modbrothersinarms=9, modzzz_sharedutils=0)
        self.script("modzzzz_late", BIA)
        self.priorities(modzzzz_late=0)
        # at 0, RER still comes before Sharedutils by name and modzzzz_late after it
        self.assertEqual(self.check().late, ["modzzzz_late", "modbrothersinarms"])

    def test_community_fixes_compile(self):
        self.standardSetup()
        self.priorities(modrandom_encounters_reworked=0, modzzz_sharedutils=1)
        self.assertEqual(self.check().late, ["modbrothersinarms"])
        self.priorities(modbrothersinarms=999, modzzz_sharedutils=1000)
        self.assertEqual(self.check().late, [])

    def test_nothing_numbered_loads_dependents_first_by_name(self):
        self.script("modzzz_sharedutils", SHAREDUTILS)
        self.script("modrandom_encounters_reworked", RER)
        self.assertEqual(self.check().late, [])

    def test_fix_numbers_unprioritized_dependents_and_raises_sharedutils(self):
        self.standardSetup()
        self.script("modNoNumberDependent", BIA)
        self.assertEqual(self.check().late, ["modNoNumberDependent", "modrandom_encounters_reworked"])
        changes = fixedPriorities(self.check())
        self.assertEqual(
            changes, {"modNoNumberDependent": 11, "modrandom_encounters_reworked": 11, "modzzz_sharedutils": 999}
        )
        self.priorities(**changes)
        self.assertEqual(self.check().late, [])
        self.assertEqual(fixedPriorities(self.check()), {})

    def test_fix_numbers_dependents_that_only_loaded_first_by_name(self):
        # with nothing numbered, a dependent named after Sharedutils is late; numbering Sharedutils would make
        # every unnumbered dependent late, so they all get numbers
        self.script("modzzz_sharedutils", SHAREDUTILS)
        self.script("modrandom_encounters_reworked", RER)
        self.script("modzzzz_late", BIA)
        self.assertEqual(self.check().late, ["modzzzz_late"])
        changes = fixedPriorities(self.check())
        self.assertEqual(changes, {"modrandom_encounters_reworked": 0, "modzzzz_late": 0, "modzzz_sharedutils": 999})

    def test_fix_goes_above_numbers_over_999(self):
        self.standardSetup()
        self.priorities(modOther=1500, modbrothersinarms=1200)
        self.assertEqual(
            fixedPriorities(self.check()), {"modrandom_encounters_reworked": 1501, "modzzz_sharedutils": 1502}
        )

    def test_sharedutils_found_by_its_classes_and_utf16_scripts_read(self):
        self.script("modSharedImports", SHAREDUTILS, encoding="utf-16")
        self.script("modrandom_encounters_reworked", RER, encoding="utf-16")
        self.priorities(modSharedImports=1)
        check = self.check()
        self.assertEqual(check.sharedutils, "modSharedImports")
        self.assertEqual(check.late, ["modrandom_encounters_reworked"])

    def test_disabled_and_own_definitions_are_not_dependents(self):
        self.script("modzzz_sharedutils", SHAREDUTILS)
        self.script("~modrandom_encounters_reworked", RER)
        self.script("modOwnNames", "class SU_Mine { }\nclass X extends SU_Mine { }\n")
        self.script("modComment", "// see SU_SomethingElse\n")
        self.priorities(modzzz_sharedutils=10)
        self.assertEqual(self.check().dependents, {})

    def test_without_sharedutils_every_dependent_is_reported(self):
        self.script("modrandom_encounters_reworked", RER)
        self.script("~modzzz_sharedutils", SHAREDUTILS)
        check = self.check()
        self.assertEqual(check.sharedutils, "")
        self.assertEqual(list(check.dependents), ["modrandom_encounters_reworked"])
        self.assertEqual(check.late, [])

    # ---------------------------------------------------------------- health check

    def health(self, *mods):
        model = SimpleNamespace(all=lambda: list(mods))
        return checkHealth(model, self.config)

    def suIssues(self, *mods):
        return sharedutilsIssues(SimpleNamespace(all=lambda: list(mods)), self.config)

    def test_health_warns_with_fix(self):
        self.standardSetup()
        issues = self.health(
            mod("Random Encounters Reworked", "modrandom_encounters_reworked"),
            mod("Brothers in Arms", "modbrothersinarms"),
            mod("Sharedutils", "modzzz_sharedutils"),
            mod("Other", "modOther"),
        )
        self.assertEqual(
            [(i.severity, i.mod, i.problem) for i in issues],
            [(WARNING, "Random Encounters Reworked", "Loads after Sharedutils")],
        )
        issue = issues[0]
        self.assertIn("lower priority number than modzzz_sharedutils (priority 10)", issue.detail)
        self.assertIn("it has none", issue.detail)
        self.assertIn("highest number (e.g. 999)", issue.detail)
        self.assertEqual(
            issue.fix.summary.splitlines()[1:],
            ["  modrandom_encounters_reworked: no priority → 11", "  modzzz_sharedutils: priority 10 → 999"],
        )
        issue.fix.apply()
        self.written.assert_called_once_with()
        self.assertEqual(self.get("modzzz_sharedutils"), "999")
        self.assertEqual(self.get("modrandom_encounters_reworked"), "11")
        self.assertEqual(self.suIssues(), [])

    def test_fix_has_written_mods_settings_when_it_returns(self):
        self.standardSetup()
        documents = self.mods.parent / "Documents"
        documents.mkdir()
        with patch.object(Configuration, "write_config"):
            real = Configuration(str(documents), configPath=str(self.mods.parent / "Manager"))
        for section in self.priority.sections():
            real.setPriority(section, self.priority.get(section, "priority"))
        config = SimpleNamespace(
            mods=str(self.mods),
            gameversion="re",
            getPriority=real.getPriority,
            setPriority=real.setPriority,
            write_priority=real.write_priority,
        )
        [issue] = sharedutilsIssues(SimpleNamespace(all=list), config)
        issue.fix.apply()
        written = configparser.ConfigParser()
        written.read(Path(real.settings) / "mods.settings", encoding="utf-8")
        self.assertEqual(written.get("modzzz_sharedutils", "priority"), "999")
        self.assertEqual(written.get("modrandom_encounters_reworked", "priority"), "11")
        self.assertEqual(written.get("modbrothersinarms", "priority"), "10")
        self.assertEqual(sharedutilsIssues(SimpleNamespace(all=list), config), [])

    def test_health_names_unmanaged_folders_and_same_number(self):
        self.standardSetup()
        self.priorities(modrandom_encounters_reworked=1, modbrothersinarms=10, modzzz_sharedutils=10)
        self.script("modzzzz_late", BIA)
        self.priorities(modzzzz_late=10)
        issues = self.suIssues()
        self.assertEqual([i.mod for i in issues], ["modzzzz_late"])
        self.assertIn("it has the same number, 10, and comes after it by name", issues[0].detail)

    def test_health_only_checks_order_on_remastered(self):
        self.standardSetup()
        self.config.gameversion = "ng"
        self.assertEqual(self.suIssues(), [])

    def test_health_warns_when_sharedutils_is_missing(self):
        self.script("modrandom_encounters_reworked", RER)
        self.config.gameversion = "og"
        issues = self.health(mod("RER", "modrandom_encounters_reworked"))
        self.assertEqual(
            [(i.severity, i.mod, i.problem, i.fix) for i in issues], [(WARNING, "RER", "Needs Sharedutils", None)]
        )
        self.assertIn("SU_GlossaryEntry, SU_MenuDescriptor, SU_removeCustomPinByTag", issues[0].detail)

    def test_issues_involving_installed_mods(self):
        self.standardSetup()
        model = SimpleNamespace(
            all=lambda: [mod("RER", "modrandom_encounters_reworked"), mod("Sharedutils", "modzzz_sharedutils")]
        )
        self.assertEqual([i.mod for i in sharedutilsIssues(model, self.config, {"RER"})], ["RER"])
        self.assertEqual(sharedutilsIssues(model, self.config, {"Other"}), [])
        # installing Sharedutils itself can make others late
        self.assertEqual([i.mod for i in sharedutilsIssues(model, self.config, {"Sharedutils"})], ["RER"])


class SharedutilsGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fix_button(self):
        apply = Mock()
        fixed = Mock()
        refresh = Mock(return_value=[Issue(WARNING, "B", "Other")])
        issues = [
            Issue(WARNING, "A", "Plain"),
            Issue(WARNING, "RER", "Loads after Sharedutils", fix=Fix("Sets", apply)),
        ]
        dialog = HealthDialog(None, issues, refresh, fixed=fixed)
        self.assertFalse(dialog.fixButton.isHidden())
        # the first fixable row is selected
        self.assertIs(dialog.tree.currentItem(), dialog.tree.topLevelItem(1))
        self.assertTrue(dialog.fixButton.isEnabled())
        dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
        self.assertFalse(dialog.fixButton.isEnabled())
        dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1))

        with patch("src.gui.health_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as asked:
            dialog.fixButton.click()
        self.assertIn("Sets", asked.call_args.args[2])
        apply.assert_not_called()

        with patch("src.gui.health_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dialog.fixButton.click()
        apply.assert_called_once_with()
        fixed.assert_called_once_with()
        refresh.assert_called_once_with()
        self.assertEqual(dialog.tree.topLevelItemCount(), 1)
        self.assertTrue(dialog.fixButton.isHidden())

    def test_failed_fix_is_reported(self):
        fix = Fix("Sets", Mock(side_effect=OSError("locked")))
        dialog = HealthDialog(None, [Issue(WARNING, "RER", "Late", fix=fix)])
        with (
            patch("src.gui.health_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
            patch("src.gui.health_dialog.QMessageBox.warning") as warned,
        ):
            dialog.fixSelected()
        self.assertIn("locked", warned.call_args.args[2])

    def test_no_fix_button_without_fixes(self):
        dialog = HealthDialog(None, [Issue(WARNING, "A", "Plain")])
        self.assertTrue(dialog.fixButton.isHidden())

    def test_warning_after_install(self):
        old, kept, again = mod("Old", "modOld"), mod("Kept", "modKept"), mod("Again", "modAgain")
        widget = Mock()
        widget.model.all.return_value = [old, kept, again]
        before = CustomMainWidget.modVersions(widget)
        replacement = mod("Old", "modOld")  # an update puts a new entry in place
        again.date = "2026-10-17 10:00:00"  # installing over an entry keeps it with a new date
        widget.model.all.return_value = [replacement, kept, again, mod("RER", "modrandom_encounters_reworked")]
        widget.modVersions = lambda: CustomMainWidget.modVersions(widget)
        config = SimpleNamespace()
        issue = Issue(WARNING, "RER", "Loads after Sharedutils")
        with (
            patch("src.gui.main_widget.data.config", config),
            patch("src.gui.main_widget.sharedutilsIssues", return_value=[issue]) as found,
            patch("src.gui.main_widget.HealthDialog") as dialog,
        ):
            CustomMainWidget.checkSharedutilsAfterInstall(widget, before)
            found.assert_called_once_with(widget.model, config, {"Old", "Again", "RER"})
            args, kwargs = dialog.call_args
            self.assertEqual(args[:2], (widget, [issue]))
            self.assertEqual(args[3], "Sharedutils Load Order")
            self.assertIs(kwargs["fixed"], widget.showPriorities)
            dialog.return_value.exec.assert_called_once_with()
            args[2]()
            self.assertEqual(found.call_args.args, (widget.model, config))
        self.assertIn("RER: Loads after Sharedutils", [call.args[0] for call in widget.output.call_args_list][-1])

    def test_no_warning_when_fine_or_nothing_installed(self):
        widget = Mock()
        entry = mod("RER", "modrandom_encounters_reworked")
        widget.model.all.return_value = [entry]
        widget.modVersions = lambda: CustomMainWidget.modVersions(widget)
        with (
            patch("src.gui.main_widget.data.config", SimpleNamespace()),
            patch("src.gui.main_widget.sharedutilsIssues", return_value=[]) as found,
            patch("src.gui.main_widget.HealthDialog") as dialog,
        ):
            CustomMainWidget.checkSharedutilsAfterInstall(widget, widget.modVersions())
            found.assert_not_called()
            CustomMainWidget.checkSharedutilsAfterInstall(widget, {})
            found.assert_called_once()
        dialog.assert_not_called()
        widget.output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
