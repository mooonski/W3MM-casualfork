import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QTreeWidget, QTreeWidgetItem

from src.configuration.config import Configuration
from src.core.nexus import ModFiles, NexusError, NexusFile, NexusInfo
from src.core.nxm import (
    NxmLink,
    NxmRegistration,
    forward,
    handlerCommand,
    instanceName,
    parseNxm,
    splitCommand,
)
from src.domain.mod import Mod
from src.gui.main_widget import CustomMainWidget
from src.gui.nxm_server import NxmServer, sendToRunningInstance

ZCOM = r'"C:\Users\me\AppData\Local\ZCOM Mod Manager\zcom-mod-manager.exe" "%1"'
LINK = "nxm://witcher3/mods/8621/files/78159?key=abc&expires=1791500000&user_id=1"


def app():
    return QApplication.instance() or QApplication([])


class LinkTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parseNxm(LINK), NxmLink("witcher3", 8621, 78159, "abc", "1791500000", LINK))
        self.assertEqual(
            parseNxm(" NXM://Witcher3/mods/1/files/2/ "),
            NxmLink("witcher3", 1, 2, url="NXM://Witcher3/mods/1/files/2/"),
        )
        for bad in (
            "https://nexusmods.com/witcher3/mods/1",
            "nxm://witcher3/mods/x/files/2",
            "nxm://witcher3/collections/1",
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(parseNxm(bad))

    def test_split_registered_commands(self):
        self.assertEqual(
            splitCommand(ZCOM, LINK), [r"C:\Users\me\AppData\Local\ZCOM Mod Manager\zcom-mod-manager.exe", LINK]
        )
        self.assertEqual(splitCommand('tool.exe -x "%1" --flag', LINK), ["tool.exe", "-x", LINK, "--flag"])
        self.assertEqual(splitCommand('"C:\\a b\\tool.exe"', LINK), ["C:\\a b\\tool.exe", LINK])
        self.assertEqual(splitCommand("   ", LINK), [])

    def test_forward(self):
        with patch("src.core.nxm.subprocess.Popen") as popen:
            self.assertTrue(forward(ZCOM, LINK))
            self.assertEqual(popen.call_args.args[0][1], LINK)
            popen.side_effect = OSError("missing")
            self.assertFalse(forward(ZCOM, LINK))
        self.assertFalse(forward("", LINK))

    def test_handler_command(self):
        arguments = ["-c", r"C:\Sandbox\config"]
        command = handlerCommand(arguments)
        self.assertTrue(command.endswith(r' -c C:\Sandbox\config --nxm "%1"'))
        self.assertIn("main.py", command)
        self.assertIn(
            "pythonw.exe" if os.path.isfile(Path(sys.executable).with_name("pythonw.exe")) else "python", command
        )
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", r"C:\App\TW3MM.exe"):
            self.assertEqual(handlerCommand([]), r'C:\App\TW3MM.exe --nxm "%1"')

    def test_instance_name_is_per_configuration(self):
        first = instanceName(r"C:\W3MM-sandbox\config")
        self.assertEqual(first, instanceName(r"C:\W3MM-sandbox\config"))
        self.assertNotEqual(first, instanceName(r"C:\Users\me\Documents\The Witcher 3 Mod Manager"))
        self.assertTrue(first.startswith("TW3MM-"))


class FakeRegistry:
    def __init__(self, command=""):
        self.command = command
        self.writes = []

    def get(self):
        return self.command

    def set(self, command):
        self.command = command
        self.writes.append(command)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(startarguments=["-c", "C:/config"], previousnxm="")

    def test_register_remembers_previous_handler_and_gives_it_back(self):
        registry = FakeRegistry(ZCOM)
        registration = NxmRegistration(registry, self.config)
        self.assertFalse(registration.isRegistered())
        registration.register()
        self.assertTrue(registration.isRegistered())
        self.assertEqual(self.config.previousnxm, ZCOM)
        self.assertTrue(registry.command.endswith('-c C:/config --nxm "%1"'))
        registration.register()  # registering again must not forget ZCOM
        self.assertEqual(self.config.previousnxm, ZCOM)
        self.assertTrue(registration.unregister())
        self.assertEqual(registry.command, ZCOM)

    def test_another_instance_of_this_program_is_not_remembered(self):
        registry = FakeRegistry(handlerCommand(["-c", "C:/other"]))
        NxmRegistration(registry, self.config).register()
        self.assertEqual(self.config.previousnxm, "")

    def test_nothing_to_give_back(self):
        registry = FakeRegistry("")
        registration = NxmRegistration(registry, self.config)
        registration.register()
        self.assertFalse(registration.unregister())
        self.assertTrue(registration.isRegistered())

    def test_previous_handler_survives_the_config_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / "Documents").mkdir()
            config = Configuration(str(Path(temporary) / "Documents"), configPath=str(Path(temporary) / "Manager"))
            self.assertEqual((config.previousnxm, config.startarguments), ("", []))
            config.previousnxm = ZCOM
            self.assertEqual(config.previousnxm, ZCOM)
            config.write_config().join()
            reread = Configuration(str(Path(temporary) / "Documents"), configPath=str(Path(temporary) / "Manager"))
            self.assertEqual(reread.previousnxm, ZCOM)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()

    def test_links_reach_the_running_instance(self):
        name = f"TW3MM-test-{os.getpid()}-{time.time_ns()}"
        server = NxmServer(name)
        self.assertTrue(server.listening)
        received = []
        server.received.connect(received.append)
        # the sender is a separate start of the program, like the one Windows launches for a link
        script = (
            "import sys; from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]); "
            "from src.gui.nxm_server import sendToRunningInstance; "
            "sys.exit(0 if sendToRunningInstance(sys.argv[1], sys.argv[2]) else 1)"
        )
        root = Path(__file__).resolve().parent.parent
        sender = subprocess.Popen([sys.executable, "-c", script, name, LINK], cwd=root)
        deadline = time.monotonic() + 15
        while (not received or sender.poll() is None) and time.monotonic() < deadline:
            self.app.processEvents()
        if sender.poll() is None:
            sender.kill()
        self.assertEqual(sender.wait(5), 0)
        self.assertEqual(received, [LINK])
        server.server.close()

    def test_no_running_instance(self):
        self.assertFalse(sendToRunningInstance(f"TW3MM-nobody-{time.time_ns()}", LINK, timeout=200))


def nexusFile(fileId=78159, name="No Fall Damage", version="2.1.1", fileName="No Fall Damage 8621 2.1.1 x.zip"):
    return NexusFile(fileId, name, version, "MAIN", True, 100, fileName, 2446)


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(archives="C:/archives", previousnxm=ZCOM)
        config_patch = patch("src.gui.main_widget.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.widget = Mock()
        self.widget.nxmQueue = []
        self.widget.nexusWorker = None
        self.widget.updateChecks = {}
        self.widget.model = SimpleNamespace(modList={}, write=Mock())
        self.widget.model.all = lambda: self.widget.model.modList.values()
        self.client = Mock()
        self.widget.nexusClient.return_value = self.client

    def outputs(self):
        return "\n".join(str(call.args[0]) for call in self.widget.output.call_args_list)

    def process(self):
        CustomMainWidget.processNxmQueue(self.widget)

    def test_links_are_queued_and_the_window_raised(self):
        CustomMainWidget.handleNxm(self.widget, LINK)
        self.assertEqual(self.widget.nxmQueue, [LINK])
        self.widget.raiseWindow.assert_called_once()
        self.widget.processNxmQueue.assert_called_once()

    def test_queue_handles_bad_and_other_game_links_then_downloads(self):
        self.widget.nxmQueue = ["not a link", "nxm://skyrimspecialedition/mods/1/files/2", LINK, LINK]
        with patch("src.gui.main_widget.forward", return_value=True) as forwarded:
            self.process()
        forwarded.assert_called_once_with(ZCOM, "nxm://skyrimspecialedition/mods/1/files/2")
        self.assertIn("Not a Nexus download link: not a link", self.outputs())
        self.assertIn("Passed the skyrimspecialedition link on", self.outputs())
        self.widget.downloadNxm.assert_called_once_with(self.client, parseNxm(LINK))
        self.assertEqual(self.widget.nxmQueue, [LINK])  # waits for the first download

    def test_other_game_without_previous_handler(self):
        self.config.previousnxm = ""
        self.widget.nxmQueue = ["nxm://skyrimspecialedition/mods/1/files/2"]
        self.process()
        self.assertIn("No program is set up for skyrimspecialedition links", self.outputs())

    def test_busy_or_no_key(self):
        self.widget.nxmQueue = [LINK]
        self.widget.nexusWorker = Mock()
        self.widget.nexusWorker.isRunning.return_value = True
        self.process()
        self.assertEqual(self.widget.nxmQueue, [LINK])
        self.widget.nexusWorker = None
        self.widget.nexusClient.return_value = None
        self.widget.nxmQueue = [LINK, LINK]
        self.process()
        self.assertEqual(self.widget.nxmQueue, [])
        self.widget.downloadNxm.assert_not_called()

    def test_finished_request_starts_the_next_link(self):
        worker, other = object(), object()
        self.widget.nexusWorker = worker
        CustomMainWidget.nexusFinished(self.widget, other)
        self.assertIs(self.widget.nexusWorker, worker)
        CustomMainWidget.nexusFinished(self.widget, worker)
        self.assertIsNone(self.widget.nexusWorker)
        self.assertEqual(self.widget.processNxmQueue.call_count, 2)

    def test_download_uses_the_link_key_and_nexus_file_name(self):
        link = parseNxm(LINK)
        file = nexusFile()
        self.client.modFiles.return_value = ModFiles({file.fileId: file})
        self.client.downloadLinks.return_value = ["https://cdn/file"]
        self.client.download.return_value = "C:/archives/" + file.fileName
        CustomMainWidget.downloadNxm(self.widget, self.client, link)
        work, done = self.widget.runNexus.call_args.args
        progress = Mock()
        self.assertEqual(work(progress), (link, file, "C:/archives/" + file.fileName))
        self.client.downloadLinks.assert_called_once_with(8621, 78159, "abc", "1791500000")
        self.client.download.assert_called_once_with(
            "https://cdn/file", "C:/archives", file.fileName, 2446, progress=progress
        )
        self.assertIs(done, self.widget.nxmDownloaded)

    def test_download_without_file_details(self):
        self.client.modFiles.side_effect = NexusError("hidden", 404)
        self.client.downloadLinks.return_value = ["https://cdn/file"]
        CustomMainWidget.downloadNxm(self.widget, self.client, parseNxm(LINK))
        work, _ = self.widget.runNexus.call_args.args
        link, file, _ = work(Mock())
        self.assertIsNone(file)
        self.assertEqual(self.client.download.call_args.args[2:4], ("8621-78159.zip", 0))
        self.client.modFiles.side_effect = NexusError("bad key", 401)
        with self.assertRaises(NexusError):
            work(Mock())

    def test_declining_install_keeps_the_archive(self):
        with patch("src.gui.main_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            CustomMainWidget.nxmDownloaded(self.widget, (parseNxm(LINK), nexusFile(), "C:/archives/a.zip"))
        self.widget.installModFiles.assert_not_called()
        self.assertIn("Kept in C:/archives", self.outputs())

    def test_installed_download_remembers_the_exact_file(self):
        file = nexusFile()
        target = Mod(nexus=NexusInfo(8621, version="2.1.1", archive=file.fileName))
        target._name = "No Fall Damage"
        other = Mod(nexus=NexusInfo(8621, archive="older.zip"))
        other._name = "Older Copy"
        self.widget.model.modList = {"No Fall Damage": target, "Older Copy": other}
        self.widget.updateChecks = {"No Fall Damage": object()}
        with patch("src.gui.main_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as asked:
            CustomMainWidget.nxmDownloaded(self.widget, (parseNxm(LINK), file, "C:/archives/" + file.fileName))
        self.assertIn("No Fall Damage 2.1.1", asked.call_args.args[2])
        self.widget.installModFiles.assert_called_once_with(["C:/archives/" + file.fileName])
        self.assertEqual(target.nexus, NexusInfo.fromFile(8621, file, file.fileName))
        self.assertEqual(other.nexus.fileId, 0)
        self.assertEqual(self.widget.updateChecks, {})
        self.widget.model.write.assert_called_once()

    def test_registration_toggle(self):
        registration = Mock()
        registration.previous.return_value = ZCOM
        self.widget.nxmRegistration.return_value = registration
        self.widget.actionHandleNxm.isChecked.return_value = True
        CustomMainWidget.handleNxmChanged(self.widget)
        registration.register.assert_called_once()
        self.assertIn("now open here. Links for other games are passed to " + ZCOM, self.outputs())

        self.widget.actionHandleNxm.isChecked.return_value = False
        registration.unregister.return_value = True
        CustomMainWidget.handleNxmChanged(self.widget)
        self.assertIn("open with the previous program again", self.outputs())

        registration.unregister.return_value = False
        CustomMainWidget.handleNxmChanged(self.widget)
        self.assertIn("still open here", self.outputs())
        self.widget.actionHandleNxm.setChecked.assert_called_with(True)

        registration.unregister.side_effect = OSError("access denied")
        registration.isRegistered.return_value = False
        CustomMainWidget.handleNxmChanged(self.widget)
        self.assertIn("access denied", self.outputs())
        self.widget.actionHandleNxm.setChecked.assert_called_with(False)

    def test_free_accounts_get_the_website_download(self):
        for handled, text in ((True, "comes back here"), (False, "Turn on Nexus > Handle Nexus Links")):
            self.widget.actionHandleNxm.isChecked.return_value = handled
            with patch("src.gui.main_widget.openUrl") as opened:
                CustomMainWidget.openNexusDownload(self.widget, 8621, 78159, "No Fall Damage")
            opened.assert_called_once_with("https://www.nexusmods.com/witcher3/mods/8621?tab=files&file_id=78159&nmm=1")
            self.assertIn(text, self.outputs())

    def test_refused_update_download_opens_the_website(self):
        existing = Mod(nexus=NexusInfo(8621))
        existing._name = "No Fall Damage"
        self.widget.model.modList = {"No Fall Damage": existing}
        with patch("src.gui.main_widget.ModUpdater") as updater:
            CustomMainWidget.applyUpdates(self.widget, [("No Fall Damage", 8621, nexusFile(), "", "Premium only", 403)])
        self.widget.openNexusDownload.assert_called_once_with(8621, 78159, "No Fall Damage")
        updater.return_value.update.assert_not_called()
        self.assertIn("Updated 0 mods (0 errors)", self.outputs())


class TickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app()

    def setUp(self):
        self.mods = {}
        self.widget = Mock()
        self.widget.treeWidget = QTreeWidget()
        self.widget.treeWidget.setColumnCount(2)
        self.changed = Mock()
        self.widget.treeWidget.itemChanged.connect(self.changed)
        for name, enabled in (("A", True), ("B", False), ("C", False), ("Broken", False)):
            mod = Mock()
            mod.enabled = enabled
            mod.enable.side_effect = lambda m=mod: setattr(m, "enabled", True) or []
            mod.disable.side_effect = lambda m=mod: setattr(m, "enabled", False)
            self.mods[name] = mod
            item = QTreeWidgetItem(["", name])
            item.setCheckState(0, Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            self.widget.treeWidget.addTopLevelItem(item)
        self.mods["Broken"].enable.side_effect = OSError("locked")
        self.changed.reset_mock()
        self.widget.model = SimpleNamespace(modList=self.mods, write=Mock(), get=lambda name: self.mods[name])
        self.widget.setModsEnabled = lambda names, enabled: CustomMainWidget.setModsEnabled(self.widget, names, enabled)

    def states(self):
        tree = self.widget.treeWidget
        return [tree.topLevelItem(i).checkState(0) == Qt.CheckState.Checked for i in range(tree.topLevelItemCount())]

    def outputs(self):
        return "\n".join(str(call.args[0]) for call in self.widget.output.call_args_list)

    def test_enable_several_at_once(self):
        self.assertEqual(CustomMainWidget.setModsEnabled(self.widget, ["A", "B", "C", "Broken", "Gone"], True), 2)
        self.assertEqual(self.states(), [True, True, True, False])
        self.widget.model.write.assert_called_once()
        self.changed.assert_not_called()  # one save instead of one per mod
        self.widget.alertRunScriptMerger.assert_called_once()
        self.widget.refreshLoadOrder.assert_called_once()
        self.assertIn("locked", self.outputs())
        self.assertIn("Enabled 2 mods", self.outputs())

    def test_nothing_to_change(self):
        self.assertEqual(CustomMainWidget.setModsEnabled(self.widget, ["B", "C"], False), 0)
        self.widget.model.write.assert_not_called()
        self.widget.alertRunScriptMerger.assert_not_called()

    def test_space_ticks_all_selected_or_unticks_when_all_ticked(self):
        self.widget.getSelectedMods.return_value = ["A", "B"]
        CustomMainWidget.toggleSelectedMods(self.widget)
        self.assertEqual(self.states()[:2], [True, True])
        CustomMainWidget.toggleSelectedMods(self.widget)
        self.assertEqual(self.states()[:2], [False, False])
        self.assertIn("Disabled 2 mods", self.outputs())

    def test_enable_and_disable_all_use_the_shown_rows(self):
        self.widget.treeWidget.takeTopLevelItem(3)  # hidden by the search
        CustomMainWidget.setVisibleModsEnabled(self.widget, True)
        self.assertTrue(all(self.mods[name].enabled for name in "ABC"))
        self.mods["Broken"].enable.assert_not_called()
        CustomMainWidget.setVisibleModsEnabled(self.widget, False)
        self.assertFalse(any(self.mods[name].enabled for name in "ABC"))

    def test_space_key_in_the_list(self):
        space = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ")
        self.assertTrue(CustomMainWidget.eventFilter(self.widget, self.widget.treeWidget, space))
        self.widget.toggleSelectedMods.assert_called_once()


if __name__ == "__main__":
    unittest.main()
