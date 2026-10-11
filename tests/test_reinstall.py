import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.history import INSTALLED, REINSTALL, Download, DownloadHistory
from src.core.installer import Installer
from src.core.model import Model
from src.core.nexus import NexusError, NexusFile, NexusInfo
from src.core.updater import ModUpdater
from src.domain.mod import Mod
from src.gui.main_widget import CustomMainWidget
from tests.test_binfiles import ADDON
from tests.test_nexus import FakeConfig

YES = QMessageBox.StandardButton.Yes


def mod(name, nexus=None):
    result = Mod()
    result._name = name
    result.nexus = nexus
    return result


class FindArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archives = self.root / "archives"
        self.archives.mkdir()
        self.widget = Mock()
        self.widget.downloadHistory = DownloadHistory(str(self.root / "config"))
        patcher = patch("src.gui.main_widget.data.config", SimpleNamespace(archives=str(self.archives)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_archive_its_entry_names(self):
        (self.archives / "Sky 13650 1.4.1 a.zip").write_bytes(b"PK")
        found = CustomMainWidget.modArchive(
            self.widget, mod("Sky", NexusInfo(13650, 79784, archive="Sky 13650 1.4.1 a.zip"))
        )
        self.assertEqual(Path(found).name, "Sky 13650 1.4.1 a.zip")

    def test_an_empty_or_missing_archive_doesnt_count_but_history_does(self):
        (self.archives / "Sky.zip").write_bytes(b"")
        elsewhere = self.root / "Downloads" / "Sky copy.zip"
        elsewhere.parent.mkdir()
        elsewhere.write_bytes(b"PK")
        self.widget.downloadHistory.add(Download(1, 13650, 79784, archive=str(elsewhere)))
        self.widget.downloadHistory.add(Download(2, 13650, 11111, archive=str(self.archives / "Sky.zip")))
        found = CustomMainWidget.modArchive(self.widget, mod("Sky", NexusInfo(13650, 79784, archive="Sky.zip")))
        self.assertEqual(found, str(elsewhere))
        self.assertEqual(CustomMainWidget.modArchive(self.widget, mod("Plain")), "")


class ReinstallFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = Mock()
        self.widget.nexusBusy.return_value = False
        self.mods = {
            "Local": mod("Local", NexusInfo(1, 10, archive="local.zip")),
            "Gone": mod("Gone", NexusInfo(2, 20, version="1.0", fileName="Gone main")),
            "Manual": mod("Manual"),
        }
        self.widget.model.modList = self.mods
        self.widget.getSelectedMods.return_value = ["Local", "Gone", "Manual"]
        self.widget.modArchive = lambda m: "C:/archives/local.zip" if m.name == "Local" else ""
        patcher = patch("src.gui.main_widget.data.config", SimpleNamespace(archives="C:/archives"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_asks_then_downloads_what_is_missing(self):
        with patch("src.gui.main_widget.QMessageBox.question", return_value=YES) as question:
            CustomMainWidget.reinstallFromArchives(self.widget)
        self.assertIn("downloaded from Nexus again first:\nGone", question.call_args.args[2])
        self.assertTrue(any("Manual: its archive isn't in" in c.args[0] for c in self.widget.output.call_args_list))
        work, done = self.widget.runNexus.call_args.args
        client = self.widget.nexusClient.return_value
        client.modFiles.return_value.files = {20: NexusFile(20, "Gone main", "1.0", "MAIN", True, 5, "Gone.zip", 9)}
        client.downloadLinks.return_value = ["https://cdn/gone"]
        client.download.return_value = "C:/archives/Gone.zip"
        results = work(lambda *args: None)
        client.download.assert_called_once_with("https://cdn/gone", "C:/archives", "Gone.zip", 9)
        self.assertEqual(results, [("Gone", "C:/archives/Gone.zip", "")])
        done(results)
        self.widget.reinstallDownloaded.assert_called_once_with([("Local", "C:/archives/local.zip")], results)

    def test_nothing_to_download_installs_straight_away(self):
        self.widget.getSelectedMods.return_value = ["Local"]
        with patch("src.gui.main_widget.QMessageBox.question", return_value=YES):
            CustomMainWidget.reinstallFromArchives(self.widget)
        self.widget.reinstallArchives.assert_called_once_with([("Local", "C:/archives/local.zip")])
        self.widget.runNexus.assert_not_called()

    def test_saying_no_changes_nothing(self):
        with patch("src.gui.main_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            CustomMainWidget.reinstallFromArchives(self.widget)
        self.widget.reinstallArchives.assert_not_called()
        self.widget.runNexus.assert_not_called()

    def test_downloads_that_fail_are_reported_and_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            self.widget.downloadHistory = DownloadHistory(folder)
            CustomMainWidget.reinstallDownloaded(
                self.widget, [("Local", "a.zip")], [("Gone", "", "gone from Nexus"), ("Local2", "b.zip", "")]
            )
            entries = [(e.mod, e.source, e.result, e.message) for e in self.widget.downloadHistory.entries]
        self.assertEqual(entries[1], ("Gone", REINSTALL, "failed", "gone from Nexus"))
        self.widget.reinstallArchives.assert_called_once_with([("Local", "a.zip"), ("Local2", "b.zip")])
        self.assertIn("Gone: gone from Nexus", [c.args[0] for c in self.widget.output.call_args_list])

    def test_a_failing_download_of_one_mod_doesnt_stop_the_others(self):
        self.widget.getSelectedMods.return_value = ["Gone"]
        with patch("src.gui.main_widget.QMessageBox.question", return_value=YES):
            CustomMainWidget.reinstallFromArchives(self.widget)
        work, _ = self.widget.runNexus.call_args.args
        self.widget.nexusClient.return_value.modFiles.side_effect = NexusError("hidden", 404)
        self.assertEqual(work(lambda *args: None), [("Gone", "", "hidden")])


class ReinstallTests(unittest.TestCase):
    '''A real reinstall into a temporary game, through the same code updates use'''

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = FakeConfig(self.root)
        self.config.archives = str(self.root / "archives")
        patcher = patch("src.globals.data.config", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.game = Path(self.config.game)
        (Path(self.config.menu) / "input.xml").write_text("<input/>", encoding="utf-16")
        self.model = Model(ignorelock=True)
        source = self.root / "archives" / "Sky 13650 1.4.1"
        (source / "Mods/modSky/content/scripts").mkdir(parents=True)
        (source / "Mods/modSky/content/scripts/sky.ws").write_text("// sky")
        (source / "bin/x64_dx12").mkdir(parents=True)
        (source / "bin" / ADDON).write_bytes(b"add-on")
        self.source = str(source)

    def test_puts_back_damaged_files_and_keeps_the_entry(self):
        Installer(self.model).installMod(self.source, name="Sky")
        sky = self.model.get("Sky")
        sky.nexus = NexusInfo(13650, 79784, "1.4.1", archive="old name.zip")
        sky.priority = 4
        sky.group = "Visuals"
        # the add-on and a script went missing
        (self.game / "bin" / ADDON).unlink()
        (self.game / "Mods/modSky/content/scripts/sky.ws").unlink()
        widget = Mock()
        widget.model = self.model
        widget.downloadHistory = DownloadHistory(str(self.root / "config"))
        widget.downloadHistory.add(Download(1, 13650, 79784, archive=self.source, source=REINSTALL, mod="Sky"))
        with patch("src.gui.main_widget.ModUpdater", lambda model, output: ModUpdater(model, output)):
            CustomMainWidget.reinstallArchives(widget, [("Sky", self.source)])
        self.assertEqual((self.game / "bin" / ADDON).read_bytes(), b"add-on")
        self.assertTrue((self.game / "Mods/modSky/content/scripts/sky.ws").is_file())
        again = Model(ignorelock=True).get("Sky")
        self.assertEqual((again.priority, again.group, again.nexus.fileId), ("4", "Visuals", 79784))
        self.assertEqual(again.nexus.archive, "Sky 13650 1.4.1")
        self.assertEqual(again.bins, [ADDON])
        self.assertTrue(ModUpdater(self.model).listBackups("Sky"), "backed up first")
        self.assertIn("> Reinstalled 1 mod", [c.args[0] for c in widget.output.call_args_list])
        self.assertEqual(widget.downloadHistory.entries[0].result, INSTALLED)
        widget.alertRunScriptMerger.assert_called_once()


if __name__ == "__main__":
    unittest.main()
