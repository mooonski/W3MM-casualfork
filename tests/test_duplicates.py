import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.configuration.config import Configuration
from src.core.duplicates import duplicateEntries, matchingEntries, versionNote
from src.core.installer import Installer
from src.core.model import Model
from src.core.nexus import NexusInfo
from src.core.updater import UpdateResult
from src.domain.mod import Mod
from src.gui.main_widget import CustomMainWidget

NEW = "Example 5 2.0 2026-10-08T12-42Z abc.zip"
OLD = "Example 5 1.0 2026-09-01T12-42Z abc.zip"


def mod(name, files=(), dlcs=(), nexus=None):
    result = Mod(files=list(files), dlcs=list(dlcs), nexus=nexus)
    result._name = name
    return result


def model(*mods):
    return SimpleNamespace(modList={m.name: m for m in mods}, all=lambda: [m for m in mods])


class MatchingTests(unittest.TestCase):
    def test_kinds_of_match_strongest_first(self):
        existing = [
            mod("Example", ["modExample"]),
            mod("Other Name", ["modOther", "modShared"], ["dlcShared"]),
            mod("Same Name"),
            mod("Example Mod", ["modDifferent"], nexus=NexusInfo(5)),
            mod("Unrelated Patch", ["modPatch"], nexus=NexusInfo(5)),
        ]
        installed = model(*existing)

        reinstall = matchingEntries(installed, mod("Example", ["modexample"]))
        self.assertEqual(
            [(m.mod.name, m.strength, m.reason) for m in reinstall], [("Example", 4, "is already installed")]
        )

        shared = matchingEntries(installed, mod("New", ["modShared"], ["DLCSHARED"]))
        self.assertEqual([(m.mod.name, m.strength) for m in shared], [("Other Name", 3)])
        self.assertEqual(shared[0].reason, "uses the same folders (modShared, dlcShared)")

        named = matchingEntries(installed, mod("Same Name", ["modNew"]))
        self.assertEqual([(m.mod.name, m.reason) for m in named], [("Same Name", "has the same name")])

        nexus = matchingEntries(installed, mod("Example Mods", ["modNew"], nexus=NexusInfo(5)))
        self.assertEqual([(m.mod.name, m.strength) for m in nexus], [("Example Mod", 1)])
        self.assertEqual(nexus[0].reason, "is the same Nexus mod (5)")

        self.assertEqual(matchingEntries(installed, mod("Brand New", ["modBrandNew"], nexus=NexusInfo(9))), [])

        several = matchingEntries(installed, mod("Example", ["modExample", "modOther"]))
        self.assertEqual([(m.mod.name, m.strength) for m in several], [("Example", 3), ("Other Name", 3)])

    def test_installed_duplicates(self):
        first = mod("First", ["modA", "modB"])
        second = mod("Second", ["moda"], nexus=NexusInfo(5, 7))
        third = mod("Third", ["modC"], nexus=NexusInfo(5, 7))
        unknown = mod("Unknown File", ["modD"], nexus=NexusInfo(5))
        unknownToo = mod("Unknown Too", ["modE"], nexus=NexusInfo(5))
        found = duplicateEntries(model(first, second, third, unknown, unknownToo))
        self.assertEqual(
            found,
            {
                "First": ["shares modA with Second"],
                "Second": ["shares modA with First", "is the same Nexus file as Third"],
                "Third": ["is the same Nexus file as Second"],
            },
        )
        self.assertEqual(duplicateEntries(model(mod("A", ["modA"]), mod("B", ["modB"]))), {})

    def test_version_note(self):
        installed = mod("Example", nexus=NexusInfo(5, version="2.0", archive=NEW))
        older = mod("Example", nexus=NexusInfo(5, version="1.0", archive=OLD))
        self.assertEqual(
            versionNote(older, installed), "Installed: 2.0, this archive: 1.0 (older than the installed version)"
        )
        self.assertEqual(versionNote(installed, older), "Installed: 1.0, this archive: 2.0")
        self.assertEqual(versionNote(mod("A"), mod("B")), "")
        self.assertEqual(
            versionNote(mod("A"), mod("B", nexus=NexusInfo(5, version="3"))), "Installed: 3, this archive: ?"
        )
        exact = mod("B", nexus=NexusInfo(5, version="3", uploaded=2_000_000_000))
        self.assertIn("older", versionNote(installed, exact))


class InstallerHookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        game = self.root / "The Witcher 3"
        for directory in ("content", "Mods", "DLC", "bin/config/r4game/user_config_matrix/pc"):
            (game / directory).mkdir(parents=True)
        (self.root / "Manager").mkdir()
        self.config = SimpleNamespace(
            game=str(game), mods=str(game / "Mods"), dlc=str(game / "DLC"),
            menu=str(game / "bin/config/r4game/user_config_matrix/pc"), settings=str(self.root),
            configuration=str(self.root / "Manager"), gameversion="re",
            priority=SimpleNamespace(has_section=lambda section: False),
        )  # fmt: skip
        config_patch = patch("src.globals.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.source = self.root / "source"
        (self.source / "modExample" / "content").mkdir(parents=True)
        (self.source / "modExample" / "content" / "asset").write_text("v2")
        self.model = Model(ignorelock=True)

    def test_handled_install_stops_before_copying(self):
        hook = Mock(return_value=(True, 1, 0))
        result = Installer(self.model, duplicate=hook).installMod(str(self.source), name="Example")
        self.assertEqual(result, (True, 1, 0))
        fetched, source = hook.call_args.args
        self.assertEqual((fetched.name, fetched.files, source), ("Example", ["modExample"], str(self.source)))
        self.assertFalse((Path(self.config.mods) / "modExample").exists())
        self.assertEqual(list(self.model.list()), [])

    def test_unhandled_install_continues(self):
        hook = Mock(return_value=None)
        self.assertEqual(Installer(self.model, duplicate=hook).installMod(str(self.source)), (True, 1, 0))
        hook.assert_called_once()
        self.assertTrue((Path(self.config.mods) / "modExample" / "content" / "asset").exists())

    def test_archives_hand_over_their_extracted_folder(self):
        archive = shutil.make_archive(str(self.root / "Example 5 2.0 2026-10-08T12-42Z abc"), "zip", self.source)
        seen = {}

        def hook(fetched, source):
            seen["files"] = sorted(path.name for path in Path(source).iterdir())
            seen["nexus"] = fetched.nexus
            return True, 0, 0

        Installer(self.model, duplicate=hook).installMod(archive)
        self.assertEqual(seen["files"], ["modExample"])
        self.assertEqual(seen["nexus"], NexusInfo(5, version="2.0", archive=Path(archive).name))


class HandlerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = SimpleNamespace(archives=str(self.root / "Archives"), keeparchives="1")
        config_patch = patch("src.gui.main_widget.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.existing = mod("Example", ["modExample"], nexus=NexusInfo(5, 1, "1.0", archive=OLD))
        self.widget = Mock()
        self.widget.model = model(self.existing)
        self.widget.updateChecks = {"Example": object()}
        self.widget.uniqueName = lambda name: CustomMainWidget.uniqueName(self.widget, name)

    def outputs(self):
        return "\n".join(str(call.args[0]) for call in self.widget.output.call_args_list)

    def choose(self, button: int, incoming: Mod, result=UpdateResult(True, False, "backup")):
        buttons = [object(), object(), object()]  # update, install separately, skip
        with (
            patch("src.gui.main_widget.QMessageBox") as box,
            patch("src.gui.main_widget.ModUpdater") as updater,
        ):
            box.return_value.addButton.side_effect = buttons
            box.return_value.clickedButton.return_value = buttons[button]
            updater.return_value.update.return_value = result
            outcome = CustomMainWidget.handleDuplicate(self.widget, incoming, "C:/extracted")
        return outcome, box.return_value, updater.return_value

    def test_nothing_installed_twice(self):
        with patch("src.gui.main_widget.QMessageBox") as box:
            self.assertIsNone(CustomMainWidget.handleDuplicate(self.widget, mod("New", ["modNew"]), "x"))
        box.assert_not_called()

    def test_update_goes_through_the_updater(self):
        incoming = mod("Example", ["modExample"], nexus=NexusInfo(5, version="2.0", archive=NEW))
        outcome, box, updater = self.choose(0, incoming)
        self.assertEqual(outcome, (True, 1, 0))
        updater.update.assert_called_once_with(self.existing, "C:/extracted", incoming.nexus, "install")
        text = box.setText.call_args.args[0]
        self.assertIn("Example is already installed", text)
        self.assertIn("Installed: 1.0, this archive: 2.0", text)
        self.assertEqual(self.widget.updateChecks, {})

    def test_update_from_unnamed_archive_keeps_nexus_mod(self):
        _, _, updater = self.choose(0, mod("Renamed Copy", ["modExample"]), UpdateResult(False, False, "b"))
        self.assertEqual(updater.update.call_args.args[2], NexusInfo(5))

    def test_failed_update_is_reported_as_failed(self):
        outcome, _, _ = self.choose(0, mod("Example", ["modExample"]), UpdateResult(False, False, "b"))
        self.assertEqual(outcome, (False, 0, 0))

    def test_install_separately_avoids_replacing_an_entry(self):
        self.widget.model.modList["Example (2)"] = mod("Example (2)")
        incoming = mod("Example", ["modOther"])
        outcome, _, updater = self.choose(1, incoming)
        self.assertIsNone(outcome)
        self.assertEqual(incoming.name, "Example (3)")
        self.assertIn("installed as 'Example (3)'", self.outputs())
        updater.update.assert_not_called()
        renamed = mod("Different", ["modExample"])
        self.choose(1, renamed)
        self.assertEqual(renamed.name, "Different")

    def test_skip(self):
        outcome, _, updater = self.choose(2, mod("Example", ["modExample"]))
        self.assertEqual(outcome, (True, 0, 0))
        updater.update.assert_not_called()
        self.assertIn("Skipped Example", self.outputs())

    def test_report_duplicates(self):
        self.widget.model = model(mod("A", ["modA"]), mod("B", ["modA"]))
        CustomMainWidget.reportDuplicates(self.widget)
        self.assertEqual(self.outputs(), "Installed twice? A shares modA with B\nInstalled twice? B shares modA with A")

    def test_install_uses_the_duplicate_check_and_keeps_archives(self):
        with (
            patch("src.gui.main_widget.Installer") as installer,
            patch("src.gui.main_widget.MessageAlertIncompleteInstallation"),
        ):
            installer.return_value.installMod.side_effect = [(True, 1, 0), (True, 0, 0), (False, 0, 0)]
            CustomMainWidget.installModFiles(self.widget, ["C:/a.zip", "C:/b.zip", "C:/c.zip"])
        self.assertIs(installer.call_args.kwargs["duplicate"], self.widget.handleDuplicate)
        self.widget.keepArchive.assert_called_once_with("C:/a.zip")
        self.widget.reportDuplicates.assert_called_once()
        self.assertIn("Installed 1 mods or dlcs (1 errors)", self.outputs())


class KeepArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archives = self.root / "Archives"
        self.config = SimpleNamespace(archives=str(self.archives), keeparchives="1")
        config_patch = patch("src.gui.main_widget.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.widget = Mock()
        self.downloads = self.root / "Downloads"
        self.downloads.mkdir()

    def archive(self, name, content=b"archive"):
        path = self.downloads / name
        path.write_bytes(content)
        return str(path)

    def keep(self, archive):
        CustomMainWidget.keepArchive(self.widget, archive)

    def test_copies_once_and_never_overwrites(self):
        source = self.archive("Mod.zip")
        self.keep(source)
        self.assertEqual((self.archives / "Mod.zip").read_bytes(), b"archive")
        self.keep(source)
        self.assertEqual(sorted(p.name for p in self.archives.iterdir()), ["Mod.zip"])
        different = self.downloads / "other"
        different.mkdir()
        (different / "Mod.zip").write_bytes(b"a different archive")
        self.keep(str(different / "Mod.zip"))
        self.assertEqual(sorted(p.name for p in self.archives.iterdir()), ["Mod (2).zip", "Mod.zip"])
        self.assertEqual((self.archives / "Mod.zip").read_bytes(), b"archive")
        self.assertTrue(Path(source).exists())

    def test_skipped_cases(self):
        self.archives.mkdir()
        inside = self.archives / "Inside.zip"
        inside.write_bytes(b"x")
        self.keep(str(inside))
        self.keep(str(self.downloads))  # a folder install
        self.keep(self.archive("readme.txt"))
        self.keep(str(self.downloads / "missing.zip"))
        self.config.keeparchives = "0"
        self.keep(self.archive("Off.zip"))
        self.assertEqual(sorted(p.name for p in self.archives.iterdir()), ["Inside.zip"])
        self.widget.output.assert_not_called()

    def test_copy_errors_are_reported(self):
        with patch("src.gui.main_widget.shutil.copy2", side_effect=OSError("disk full")):
            self.keep(self.archive("Mod.zip"))
        self.assertIn("disk full", self.widget.output.call_args.args[0])

    def test_setting_toggle_and_default(self):
        self.widget.actionKeepArchives.isChecked.return_value = False
        CustomMainWidget.keepArchivesChanged(self.widget)
        self.assertEqual(self.config.keeparchives, "0")
        with tempfile.TemporaryDirectory() as temporary, patch.object(Configuration, "write_config"):
            (Path(temporary) / "Documents").mkdir()
            config = Configuration(str(Path(temporary) / "Documents"), configPath=str(Path(temporary) / "Manager"))
            self.assertEqual(config.keeparchives, "1")
            config.keeparchives = "0"
            self.assertEqual(config.keeparchives, "0")


if __name__ == "__main__":
    unittest.main()
