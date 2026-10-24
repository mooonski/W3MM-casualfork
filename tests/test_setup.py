'''Tests for finding the game and for the setup dialog'''

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.configuration.config import Configuration
from src.core.gamefinder import GameInstall, findGameInstalls, gogInstalls, steamInstalls, steamLibraries
from src.core.nexus import NexusError
from src.gui.main_widget import CustomMainWidget
from src.gui.setup_dialog import KEPT_KEY, SetupDialog
from src.util.util import normalizePath


def makeGame(root: Path, name: str = "The Witcher 3", renderer: str = "x64_dx12") -> Path:
    game = root / name
    (game / "content").mkdir(parents=True)
    exe = game / "bin" / renderer / "witcher3.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    return game


class FakeRegistry:
    def __init__(self, keys: dict[tuple[str, str], dict[str, str]]):
        self.keys = keys

    def read(self, root: str, key: str):
        return self.keys.get((root, key))

    def subkeys(self, root: str, key: str) -> list[str]:
        prefix = key + "\\"
        return [
            k[len(prefix) :]
            for r, k in self.keys
            if r == root and k.startswith(prefix) and "\\" not in k[len(prefix) :]
        ]


class GameFinderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def steam(self, libraries: list[Path]) -> Path:
        steam = self.root / "Steam"
        (steam / "steamapps").mkdir(parents=True)
        entries = "".join(
            f'\t"{i}"\n\t{{\n\t\t"path"\t\t"{str(library).replace(chr(92), chr(92) * 2)}"\n\t}}\n'
            for i, library in enumerate(libraries)
        )
        (steam / "steamapps" / "libraryfolders.vdf").write_text(f'"libraryfolders"\n{{\n{entries}}}\n')
        return steam

    def test_steam_libraries_come_from_libraryfolders(self):
        steam = self.steam([self.root / "Steam", self.root / "Drive" / "SteamLibrary"])
        self.assertEqual(steamLibraries(str(steam)), [str(steam), str(self.root / "Drive" / "SteamLibrary")])
        self.assertEqual(steamLibraries(str(self.root / "missing")), [str(self.root / "missing")])
        self.assertEqual(steamLibraries(""), [])

    def test_steam_install_needs_manifest_and_folder(self):
        library = self.root / "Drive" / "SteamLibrary"
        steam = self.steam([self.root / "Steam", library])
        (library / "steamapps" / "common").mkdir(parents=True)
        registry = FakeRegistry({("HKCU", r"Software\Valve\Steam"): {"SteamPath": str(steam)}})
        self.assertEqual(steamInstalls(registry.read), [])
        (library / "steamapps" / "appmanifest_292030.acf").write_text('"AppState" {}')
        self.assertEqual(steamInstalls(registry.read), [])
        game = makeGame(library / "steamapps" / "common")
        self.assertEqual(steamInstalls(registry.read), [GameInstall(game.as_posix(), "Steam")])

    def test_steam_path_falls_back_to_machine_key(self):
        steam = self.steam([])
        (steam / "steamapps" / "appmanifest_292030.acf").write_text("")
        game = makeGame(steam / "steamapps" / "common")
        registry = FakeRegistry({("HKLM", r"SOFTWARE\Valve\Steam"): {"InstallPath": str(steam)}})
        self.assertEqual(steamInstalls(registry.read), [GameInstall(game.as_posix(), "Steam")])

    def test_gog_installs_match_witcher_3_entries_only(self):
        game = makeGame(self.root / "GOG Games", "The Witcher 3 Wild Hunt GOTY")
        registry = FakeRegistry(
            {
                ("HKLM", r"SOFTWARE\GOG.com\Games\1495134320"): {
                    "gameName": "The Witcher 3: Wild Hunt - Game of the Year Edition",
                    "path": str(game),
                },
                ("HKLM", r"SOFTWARE\GOG.com\Games\1430744353"): {"gameName": "Invisible, Inc.", "path": str(self.root)},
                ("HKLM", r"SOFTWARE\GOG.com\Games\99"): {"gameName": "The Witcher 3", "path": str(self.root / "gone")},
            }
        )
        self.assertEqual(gogInstalls(registry.read, registry.subkeys), [GameInstall(game.as_posix(), "GOG")])
        self.assertEqual(gogInstalls(FakeRegistry({}).read, FakeRegistry({}).subkeys), [])

    def test_find_validates_and_removes_duplicates(self):
        library = self.root / "SteamLibrary"
        steam = self.steam([library])
        (library / "steamapps").mkdir(parents=True)
        (library / "steamapps" / "appmanifest_292030.acf").write_text("")
        game = makeGame(library / "steamapps" / "common")
        other = makeGame(self.root / "GOG Games")
        registry = FakeRegistry(
            {
                ("HKCU", r"Software\Valve\Steam"): {"SteamPath": str(steam)},
                ("HKLM", r"SOFTWARE\GOG.com\Games\1"): {"gameName": "The Witcher 3", "path": str(game).upper()},
                ("HKLM", r"SOFTWARE\GOG.com\Games\2"): {"gameName": "The Witcher 3", "path": str(other)},
            }
        )
        found = findGameInstalls(registry.read, registry.subkeys, valid=Configuration.getCorrectGamePath)
        self.assertEqual(found, [GameInstall(game.as_posix(), "Steam"), GameInstall(other.as_posix(), "GOG")])
        self.assertEqual(found[0].label, f"Steam: {game.as_posix()}")
        # a validator that rejects everything
        self.assertEqual(findGameInstalls(registry.read, registry.subkeys, valid=lambda folder: ""), [])


class SetupDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        documents = self.root / "Documents"
        documents.mkdir()
        self.game = makeGame(self.root)
        self.exe = (self.game / "bin" / "x64_dx12" / "witcher3.exe").as_posix()
        self.merger = self.root / "Script Merger" / "WitcherScriptMerger.exe"
        self.merger.parent.mkdir()
        self.merger.touch()
        self.writes = patch.object(Configuration, "write_config", Mock())
        self.writes.start()
        self.addCleanup(self.writes.stop)
        self.config = Configuration(str(documents), configPath=str(self.root / "Manager"))
        self.validate = Mock(return_value={"name": "someone", "is_premium": True})
        self.findMerger = Mock(return_value=self.merger.as_posix())

    def dialog(self, installs=None, **kwargs) -> SetupDialog:
        dialog = SetupDialog(
            None,
            self.config,
            installs=lambda: [GameInstall(self.game.as_posix(), "Steam")] if installs is None else installs,
            findMerger=self.findMerger,
            validateKey=self.validate,
            **kwargs,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        dialog.show()
        self.addCleanup(dialog.close)
        return dialog

    def test_first_run_prefills_detected_game_and_script_merger(self):
        dialog = self.dialog(firstRun=True)
        self.assertEqual(dialog.gameFolder(), self.game.as_posix())
        self.assertEqual(dialog.gameExe(), self.exe)
        self.assertTrue(dialog.okButton.isEnabled())
        self.findMerger.assert_called_once_with(
            self.game.as_posix(), normalizePath(self.config.archives), self.config.rmmr
        )
        self.assertEqual(dialog.mergerEdit.text(), self.merger.as_posix())
        self.assertIn("Script Merger found", dialog.mergerStatus.text())
        self.assertEqual(dialog.archivesEdit.text(), normalizePath(self.config.archives))
        self.assertEqual(dialog.keyEdit.text(), "")

    def test_accept_writes_everything_and_checks_a_new_key(self):
        dialog = self.dialog()
        downloads = self.root / "downloads"
        dialog.archivesEdit.setText(str(downloads))
        dialog.keyEdit.setText("  abc123  ")
        dialog.accept()
        self.assertEqual(dialog.result(), SetupDialog.DialogCode.Accepted)
        self.validate.assert_called_once_with("abc123")
        self.assertEqual(self.config.gameexe, self.exe)
        self.assertEqual(self.config.scriptmerger, self.merger.as_posix())
        self.assertEqual(self.config.archives, downloads.as_posix())
        self.assertEqual(self.config.nexusapikey, "abc123")
        self.assertIn("someone (Premium)", dialog.keyStatus.text())

    def test_rejected_key_blocks_accept(self):
        self.validate.side_effect = NexusError("Invalid API key")
        dialog = self.dialog()
        dialog.keyEdit.setText("bad")
        dialog.accept()
        self.assertEqual(dialog.result(), SetupDialog.DialogCode.Rejected)
        self.assertTrue(dialog.isVisible())
        self.assertIn("Invalid API key", dialog.keyStatus.text())
        self.assertFalse(self.config.gameexe)
        self.assertEqual(self.config.nexusapikey, "")

    def test_stored_key_is_kept_unless_changed(self):
        self.config.nexusapikey = "stored"
        dialog = self.dialog()
        self.assertEqual(dialog.keyEdit.text(), KEPT_KEY)
        self.assertIsNone(dialog.keyEntered())
        dialog.accept()
        self.validate.assert_not_called()
        self.assertEqual(self.config.nexusapikey, "stored")
        # clearing the field removes the key without a check
        dialog = self.dialog()
        dialog.keyEdit.clear()
        self.assertEqual(dialog.keyEntered(), "")
        dialog.accept()
        self.validate.assert_not_called()
        self.assertEqual(self.config.nexusapikey, "")

    def test_cancel_writes_nothing(self):
        dialog = self.dialog()
        dialog.keyEdit.setText("abc")
        dialog.reject()
        self.assertFalse(self.config.gameexe)
        self.assertEqual(self.config.scriptmerger, "")
        self.assertEqual(self.config.nexusapikey, "")
        self.validate.assert_not_called()

    def test_without_a_game_ok_is_disabled_until_a_folder_is_typed(self):
        dialog = self.dialog(installs=[])
        self.assertFalse(dialog.okButton.isEnabled())
        self.assertIn("No Steam or GOG installation found", dialog.gameStatus.text())
        dialog.gameBox.setEditText(str(self.root / "elsewhere"))
        self.assertFalse(dialog.okButton.isEnabled())
        self.assertIn("No witcher3.exe", dialog.gameStatus.text())
        dialog.gameBox.setEditText(str(self.game))
        self.assertTrue(dialog.okButton.isEnabled())
        self.assertEqual(dialog.gameExe(), self.exe)

    def test_existing_setup_is_shown_and_a_missing_merger_is_flagged(self):
        self.config.gameexe = self.exe
        self.config.scriptmerger = str(self.root / "gone.exe")
        dialog = self.dialog(installs=[])
        self.assertEqual(dialog.gameFolder(), self.game.as_posix())
        self.assertTrue(dialog.okButton.isEnabled())
        self.findMerger.assert_not_called()
        self.assertIn("doesn't exist", dialog.mergerStatus.text())
        dialog.mergerEdit.clear()
        dialog.accept()
        self.assertEqual(self.config.scriptmerger, "")


class ShowSetupTests(unittest.TestCase):
    '''Settings > Setup... in the main window'''

    def run_(self, before: dict, after: dict, accepted: bool = True) -> Mock:
        widget = Mock()
        config = SimpleNamespace(**before)

        def exec_():
            config.__dict__.update(after)
            return accepted

        with (
            patch("src.gui.main_widget.data.config", config),
            patch("src.gui.main_widget.SetupDialog") as dialog,
        ):
            dialog.return_value.exec.side_effect = exec_
            CustomMainWidget.showSetup(widget)
        dialog.assert_called_once_with(widget, config)
        return widget

    def test_cancel_changes_nothing(self):
        state = dict(game="G", scriptmerger="", archives="A", nexusapikey="")
        widget = self.run_(state, {}, accepted=False)
        widget.output.assert_not_called()
        widget.refreshList.assert_not_called()

    def test_changed_game_refreshes_the_list_and_toolbar(self):
        state = dict(game="G", scriptmerger="", archives="A", nexusapikey="")
        widget = self.run_(state, dict(game="H"))
        widget.refreshList.assert_called_once()
        widget.configureToolbar.assert_called_once()
        widget.refreshNexusDetails.assert_not_called()

    def test_new_key_and_paths_are_reported(self):
        state = dict(game="G", scriptmerger="", archives="A", nexusapikey="")
        widget = self.run_(state, dict(scriptmerger="S/WitcherScriptMerger.exe", archives="B", nexusapikey="k"))
        lines = [call.args[0] for call in widget.output.call_args_list]
        self.assertEqual(lines[0], "Setup saved.")
        self.assertIn("Script Merger: S/WitcherScriptMerger.exe", lines)
        self.assertIn("Downloads go to B", lines)
        widget.refreshList.assert_not_called()
        widget.refreshNexusDetails.assert_called_once_with(silent=True, onlyMissing=True)
        widget.refreshEndorsements.assert_called_once()


if __name__ == "__main__":
    unittest.main()
