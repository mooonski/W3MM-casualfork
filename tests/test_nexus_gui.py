import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from src.configuration.config import Configuration
from src.core.nexus import (
    FAILED,
    UP_TO_DATE,
    UPDATE_AVAILABLE,
    ModFiles,
    NexusError,
    NexusFile,
    NexusInfo,
    UpdateCheck,
)
from src.core.updater import Backup, UpdateResult
from src.domain.mod import Mod
from src.gui.main_widget import CustomMainWidget
from src.gui.nexus_dialogs import BackupsDialog, NexusWorker, UpdateRow, UpdatesDialog, askApiKey, statusText


def nexusFile(fileId, name="Example", version="1.0", category="MAIN", uploaded=100, fileName=""):
    return NexusFile(fileId, name, version, category, False, uploaded, fileName or f"{name} {fileId}.zip", 1000)


def mod(name, nexus=None):
    result = Mod()
    result._name = name
    result.nexus = nexus
    return result


class HandlerTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = SimpleNamespace(
            archives=str(self.root / "Archives"),
            backups=str(self.root / "Backups"),
            nexusapikey="key",
            scriptmerger="",
            gameversion="re",
        )
        config_patch = patch("src.gui.main_widget.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.widget = Mock()
        self.widget.updateChecks = {}
        self.widget.nexusBusy.return_value = False
        self.widget.model = SimpleNamespace(modList={}, write=Mock())
        self.widget.model.get = lambda name: self.widget.model.modList[name]
        self.widget.model.all = lambda: self.widget.model.modList.values()
        self.client = Mock()
        self.widget.nexusClient.return_value = self.client

    def add(self, *mods):
        for item in mods:
            self.widget.model.modList[item.name] = item

    def outputs(self) -> str:
        return "\n".join(str(call.args[0]) for call in self.widget.output.call_args_list)

    def background(self):
        '''Returns the (work, done) pair the handler passed to runNexus'''
        self.widget.runNexus.assert_called_once()
        return self.widget.runNexus.call_args.args


class CheckForUpdatesTests(HandlerTestCase):
    def test_nothing_to_check_explains_how_to_link_mods(self):
        self.add(mod("Plain"))
        CustomMainWidget.checkForUpdates(self.widget)
        self.assertIn("Set Nexus Mod", self.outputs())
        self.widget.nexusClient.assert_not_called()
        self.widget.runNexus.assert_not_called()

    def test_busy_or_missing_key_does_nothing(self):
        self.add(mod("A", NexusInfo(1)))
        self.widget.nexusBusy.return_value = True
        CustomMainWidget.checkForUpdates(self.widget)
        self.widget.nexusClient.assert_not_called()
        self.widget.nexusBusy.return_value = False
        self.widget.nexusClient.return_value = None
        CustomMainWidget.checkForUpdates(self.widget)
        self.widget.runNexus.assert_not_called()

    def test_checks_each_nexus_mod_once_and_keeps_per_mod_errors(self):
        self.add(mod("A", NexusInfo(1)), mod("B", NexusInfo(2)), mod("A2", NexusInfo(1)), mod("Plain"))
        files = ModFiles()
        self.client.modFiles.side_effect = lambda modId: (
            files if modId == 1 else (_ for _ in ()).throw(NexusError("hidden", 404))
        )
        CustomMainWidget.checkForUpdates(self.widget)
        work, done = self.background()
        progress = Mock()
        results = work(progress)
        self.assertEqual(results, {1: files, 2: "hidden"})
        self.assertEqual(self.client.modFiles.call_count, 2)
        progress.assert_called_with(2, 2)
        done(results)
        self.widget.updatesChecked.assert_called_once_with(["A", "B", "A2"], results)

    def test_only_selected_mods_are_checked(self):
        self.add(mod("A", NexusInfo(1)), mod("B", NexusInfo(2)))
        CustomMainWidget.checkForUpdates(self.widget, ["B"])
        work, _ = self.background()
        self.client.modFiles.return_value = ModFiles()
        self.assertEqual(list(work(Mock())), [2])

    def test_key_and_rate_limit_errors_stop_the_whole_check(self):
        self.add(mod("A", NexusInfo(1)))
        CustomMainWidget.checkForUpdates(self.widget)
        work, _ = self.background()
        for status in (401, 429):
            self.client.modFiles.side_effect = NexusError("stop", status)
            with self.subTest(status=status), self.assertRaises(NexusError):
                work(Mock())


class UpdatesCheckedTests(HandlerTestCase):
    def setUp(self):
        super().setUp()
        self.files = ModFiles.fromApi(
            {
                "files": [
                    {"file_id": 1, "name": "A", "version": "1.0", "category_name": "OLD_VERSION",
                     "uploaded_timestamp": 1, "file_name": "A 5 1.0 x.zip"},
                    {"file_id": 2, "name": "A", "version": "2.0", "category_name": "MAIN",
                     "uploaded_timestamp": 2, "file_name": "A 5 2.0 y.zip"},
                ],
                "file_updates": [{"old_file_id": 1, "new_file_id": 2}],
            }
        )  # fmt: skip
        self.add(mod("A", NexusInfo(5, archive="A 5 1.0 x.zip")), mod("B", NexusInfo(6)))

    def test_results_are_recorded_and_the_dialog_choice_is_downloaded(self):
        with patch("src.gui.main_widget.UpdatesDialog") as dialog:
            dialog.return_value.exec.return_value = True
            dialog.return_value.selection.return_value = [("A", self.files.files[2])]
            CustomMainWidget.updatesChecked(self.widget, ["A", "B"], {5: self.files, 6: "hidden"})
        self.assertEqual(self.widget.updateChecks["A"].state, UPDATE_AVAILABLE)
        self.assertEqual(self.widget.updateChecks["B"].state, FAILED)
        self.assertEqual(self.widget.updateChecks["B"].message, "hidden")
        # the exact installed file was identified from the archive name and remembered
        learned = self.widget.model.modList["A"].nexus
        self.assertEqual((learned.fileId, learned.version, learned.archive), (1, "1.0", "A 5 1.0 x.zip"))
        self.widget.model.write.assert_called_once()
        self.assertIn("1 update available (1 error)", self.outputs())
        self.assertIn("B: hidden", self.outputs())
        rows = dialog.call_args.args[1]
        self.assertEqual([row.name for row in rows], ["A", "B"])
        self.widget.refreshList.assert_called()
        self.widget.downloadAndUpdate.assert_called_once_with([("A", self.files.files[2])])

    def test_closing_the_dialog_downloads_nothing(self):
        with patch("src.gui.main_widget.UpdatesDialog") as dialog:
            dialog.return_value.exec.return_value = False
            CustomMainWidget.updatesChecked(self.widget, ["A"], {5: self.files})
        self.widget.downloadAndUpdate.assert_not_called()

    def test_mods_removed_meanwhile_are_skipped(self):
        with patch("src.gui.main_widget.UpdatesDialog") as dialog:
            CustomMainWidget.updatesChecked(self.widget, ["Gone"], {})
        dialog.assert_not_called()
        self.widget.model.write.assert_not_called()


class DownloadTests(HandlerTestCase):
    def test_downloads_into_archive_folder_with_overall_progress(self):
        self.add(mod("A", NexusInfo(5)), mod("B", NexusInfo(6)))
        first, second = nexusFile(1), nexusFile(2)
        second.fileName = ""  # Nexus didn't report a file name, so a fallback name is used
        self.client.downloadLinks.side_effect = lambda modId, fileId: [f"https://cdn/{fileId}"]

        def download(url, directory, filename, size, progress):
            progress(size, size)
            return str(Path(directory) / filename)

        self.client.download.side_effect = download
        CustomMainWidget.downloadAndUpdate(self.widget, [("A", first), ("B", second), ("Gone", first)])
        work, done = self.background()
        progress = Mock()
        results = work(progress)
        self.assertEqual(
            results,
            [
                ("A", 5, first, str(Path(self.config.archives) / first.fileName), "", 0),
                ("B", 6, second, str(Path(self.config.archives) / "6-2.zip"), "", 0),
            ],
        )
        self.assertEqual([call.args for call in progress.call_args_list], [(1000, 2000), (2000, 2000)])
        self.assertIs(done, self.widget.applyUpdates)

    def test_one_failed_download_does_not_stop_the_rest(self):
        self.add(mod("A", NexusInfo(5)), mod("B", NexusInfo(6)))
        self.client.downloadLinks.side_effect = [NexusError("gone", 404), ["https://cdn/2"]]
        self.client.download.return_value = "archive.zip"
        CustomMainWidget.downloadAndUpdate(self.widget, [("A", nexusFile(1)), ("B", nexusFile(2))])
        work, _ = self.background()
        results = work(Mock())
        self.assertEqual([(name, archive, error, status) for name, _, _, archive, error, status in results],
                         [("A", "", "gone", 404), ("B", "archive.zip", "", 0)])  # fmt: skip
        self.client.downloadLinks.side_effect = NexusError("limit", 429)
        with self.assertRaises(NexusError):
            work(Mock())


class ApplyUpdatesTests(HandlerTestCase):
    def test_updates_are_applied_and_counted(self):
        self.add(mod("A", NexusInfo(5, game="witcher3")), mod("B", NexusInfo(6)))
        file = nexusFile(2, version="2.0")
        with (
            patch("src.gui.main_widget.ModUpdater") as updater,
            patch("src.gui.main_widget.MessageAlertIncompleteInstallation") as incomplete,
        ):
            updater.return_value.update.side_effect = [
                UpdateResult(True, True, "backup"),
                UpdateResult(False, False, "b"),
            ]
            CustomMainWidget.applyUpdates(
                self.widget,
                [
                    ("A", 5, file, str(self.root / "a.zip"), "", 0),
                    ("B", 6, file, str(self.root / "b.zip"), "", 0),
                    ("C", 7, file, "", "download failed", 500),
                ],
            )
        calls = updater.return_value.update.call_args_list
        self.assertIs(calls[0].args[0], self.widget.model.modList["A"])
        self.assertEqual(calls[0].args[1], str(self.root / "a.zip"))
        self.assertEqual(calls[0].args[2], NexusInfo(5, 2, "2.0", "Example", 100, "a.zip", "witcher3"))
        self.assertEqual(self.widget.updateChecks["A"].state, UP_TO_DATE)
        self.assertNotIn("B", self.widget.updateChecks)
        self.assertIn("C: download failed", self.outputs())
        self.assertIn("Updated 1 mod (2 errors) (1 incomplete)", self.outputs())
        incomplete.assert_called_once()
        self.widget.alertRunScriptMerger.assert_called_once()
        self.widget.refreshList.assert_called()

    def test_no_successful_update_skips_script_merger_prompt(self):
        CustomMainWidget.applyUpdates(self.widget, [("A", 5, nexusFile(1), "", "failed", 500)])
        self.widget.alertRunScriptMerger.assert_not_called()


class LinkingTests(HandlerTestCase):
    def ask(self, *answers):
        return patch("src.gui.main_widget.QInputDialog.getText", side_effect=list(answers))

    def test_set_unlink_and_keep_known_file(self):
        self.add(mod("A"), mod("B", NexusInfo(5, fileId=9)), mod("C", NexusInfo(7)))
        self.widget.updateChecks = {"A": object(), "B": object(), "C": object()}
        self.widget.getSelectedMods.return_value = ["A", "B", "C"]
        with self.ask(("https://www.nexusmods.com/witcher3/mods/820", True), ("5", True), ("", True)) as dialog:
            CustomMainWidget.setNexusMod(self.widget)
        mods = self.widget.model.modList
        self.assertEqual(mods["A"].nexus, NexusInfo(820))
        self.assertEqual(mods["B"].nexus.fileId, 9)
        self.assertIsNone(mods["C"].nexus)
        self.assertEqual(dialog.call_args_list[1].args[4], "5")
        self.assertEqual(self.widget.updateChecks, {})
        self.widget.model.write.assert_called_once()

    def test_invalid_reference_is_reported_and_cancel_stops(self):
        self.add(mod("A"), mod("B"))
        self.widget.getSelectedMods.return_value = ["A", "B"]
        with self.ask(("not a mod", True), ("1", False)):
            CustomMainWidget.setNexusMod(self.widget)
        self.assertIn("Not a Nexus mod ID", self.outputs())
        self.assertIsNone(self.widget.model.modList["A"].nexus)
        self.assertIsNone(self.widget.model.modList["B"].nexus)
        self.widget.model.write.assert_not_called()

    def test_open_nexus_page(self):
        self.add(mod("A", NexusInfo(820)), mod("B"))
        self.widget.getSelectedMods.return_value = ["A", "B"]
        with patch("src.gui.main_widget.openUrl") as opened:
            CustomMainWidget.openNexusPage(self.widget)
        opened.assert_called_once_with("https://www.nexusmods.com/witcher3/mods/820")
        self.assertIn("B: no Nexus ID", self.outputs())

    def test_update_selected_mods(self):
        self.add(mod("A", NexusInfo(1)), mod("B"))
        self.widget.getSelectedMods.return_value = []
        CustomMainWidget.updateSelectedMods(self.widget)
        self.assertIn("Select the mods", self.outputs())
        self.widget.getSelectedMods.return_value = ["B"]
        CustomMainWidget.updateSelectedMods(self.widget)
        self.widget.checkForUpdates.assert_not_called()
        self.widget.getSelectedMods.return_value = ["A", "B"]
        CustomMainWidget.updateSelectedMods(self.widget)
        self.widget.checkForUpdates.assert_called_once_with(["A", "B"])


class RestoreHandlerTests(HandlerTestCase):
    def test_needs_exactly_one_mod_with_backups(self):
        self.add(mod("A"))
        for selected in ([], ["A", "B"]):
            self.widget.getSelectedMods.return_value = selected
            CustomMainWidget.restoreBackup(self.widget)
        self.assertEqual(self.outputs().count("Select one mod"), 2)
        self.widget.getSelectedMods.return_value = ["A"]
        with patch("src.gui.main_widget.ModUpdater") as updater:
            updater.return_value.listBackups.return_value = []
            CustomMainWidget.restoreBackup(self.widget)
        self.assertIn("A: no backups found", self.outputs())

    def test_restore_chosen_backup(self):
        self.add(mod("A"))
        self.widget.getSelectedMods.return_value = ["A"]
        self.widget.updateChecks = {"A": object()}
        backup = Backup("dir", "A", "2026-10-10 10:00:00", "update", "1.0")
        with (
            patch("src.gui.main_widget.ModUpdater") as updater,
            patch("src.gui.main_widget.BackupsDialog") as dialog,
        ):
            updater.return_value.listBackups.return_value = [backup]
            dialog.return_value.exec.return_value = False
            CustomMainWidget.restoreBackup(self.widget)
            updater.return_value.restore.assert_not_called()

            dialog.return_value.exec.return_value = True
            dialog.return_value.selected.return_value = backup
            updater.return_value.restore.return_value = UpdateResult(True, False, "new")
            CustomMainWidget.restoreBackup(self.widget)
            updater.return_value.restore.assert_called_once_with(backup)
        self.assertIn("Restored A", self.outputs())
        self.assertEqual(self.widget.updateChecks, {})
        self.widget.alertRunScriptMerger.assert_called_once()

    def test_failed_restore_is_reported(self):
        self.add(mod("A"))
        self.widget.getSelectedMods.return_value = ["A"]
        with (
            patch("src.gui.main_widget.ModUpdater") as updater,
            patch("src.gui.main_widget.BackupsDialog") as dialog,
        ):
            updater.return_value.listBackups.return_value = [Backup("d", "A", "c", "", "")]
            dialog.return_value.exec.return_value = True
            updater.return_value.restore.return_value = UpdateResult(False, False, "")
            CustomMainWidget.restoreBackup(self.widget)
        self.assertIn("Could not restore A", self.outputs())
        self.widget.alertRunScriptMerger.assert_not_called()

    def test_folder_settings(self):
        with patch("src.gui.main_widget.QFileDialog.getExistingDirectory", side_effect=["C:\\New\\Archives", ""]):
            CustomMainWidget.changeArchiveFolder(self.widget)
            CustomMainWidget.changeBackupFolder(self.widget)
        self.assertEqual(self.config.archives, "C:/New/Archives")
        self.assertEqual(self.config.backups, str(self.root / "Backups"))


class WorkerAndStatusTests(unittest.TestCase):
    def run_worker(self, function):
        worker = NexusWorker(function)
        received = {"succeeded": [], "failed": [], "progressed": []}
        worker.succeeded.connect(lambda value: received["succeeded"].append(value))
        worker.failed.connect(lambda value: received["failed"].append(value))
        worker.progressed.connect(lambda done, total: received["progressed"].append((done, total)))
        worker.run()
        return received

    def test_worker_reports_results_progress_and_errors(self):
        def work(progress):
            progress(1, 2)
            return "result"

        self.assertEqual(self.run_worker(work), {"succeeded": ["result"], "failed": [], "progressed": [(1, 2)]})
        failed = self.run_worker(lambda progress: (_ for _ in ()).throw(NexusError("nope")))
        self.assertEqual(failed["failed"], ["nope"])
        crashed = self.run_worker(lambda progress: 1 / 0)
        self.assertEqual(crashed["failed"], ["ZeroDivisionError: division by zero"])
        self.assertEqual(crashed["succeeded"], [])

    def test_status_text(self):
        target = nexusFile(2, version="2.0")
        self.assertEqual(statusText(None), "")
        self.assertEqual(statusText(UpdateCheck(UPDATE_AVAILABLE, target=target, confident=True)), "Update 2.0")
        self.assertEqual(statusText(UpdateCheck(UPDATE_AVAILABLE, target=target)), "Update 2.0?")
        self.assertEqual(statusText(UpdateCheck(UP_TO_DATE, confident=True)), "Up to date")
        self.assertEqual(statusText(UpdateCheck(UP_TO_DATE)), "Up to date?")
        self.assertEqual(statusText(UpdateCheck(FAILED, message="x")), "Error")


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self):
        old, new, lite = nexusFile(1, version="1.0"), nexusFile(2, version="2.0"), nexusFile(3, name="Example Lite")
        rows = [
            UpdateRow("Zed", "", UpdateCheck(UP_TO_DATE, new, new, [new, lite], confident=True)),
            UpdateRow("Broken", "", UpdateCheck(FAILED, message="hidden on Nexus")),
            UpdateRow("Alpha", "", UpdateCheck(UPDATE_AVAILABLE, old, new, [new, lite], confident=False)),
        ]
        return UpdatesDialog(None, rows), new, lite

    def items(self, dialog):
        return [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]

    def test_rows_states_and_default_selection(self):
        dialog, new, lite = self.dialog()
        items = self.items(dialog)
        self.assertEqual([item.text(0) for item in items], ["Alpha", "Broken", "Zed"])
        self.assertEqual(items[0].text(1), "1.0")
        self.assertEqual(items[0].text(3), "Check the file")
        self.assertEqual(items[0].checkState(0), Qt.CheckState.Checked)
        self.assertEqual(items[1].text(2), "hidden on Nexus")
        self.assertFalse(items[1].flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(items[2].checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(dialog.selection(), [("Alpha", new)])

    def test_choosing_another_file_ticks_the_row(self):
        dialog, new, lite = self.dialog()
        dialog.combos["Zed"].setCurrentIndex(1)
        self.assertEqual(dialog.selection(), [("Alpha", new), ("Zed", lite)])

    def test_space_toggles_selected_rows(self):
        dialog, new, _ = self.dialog()
        items = self.items(dialog)
        dialog.tree.hasFocus = lambda: True
        for item in items:
            item.setSelected(True)
        space = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ")
        dialog.keyPressEvent(space)
        self.assertEqual([name for name, _ in dialog.selection()], ["Alpha", "Zed"])
        dialog.keyPressEvent(space)
        self.assertEqual(dialog.selection(), [])

    def test_backups_dialog_selects_newest(self):
        backups = [Backup("b", "A", "2026-10-10", "update", "2.0"), Backup("a", "A", "2026-10-01", "update", "1.0")]
        dialog = BackupsDialog(None, "A", backups)
        self.assertIs(dialog.selected(), backups[0])
        self.assertEqual(dialog.list.item(1).text(), "2026-10-01  1.0  (update)")
        self.assertIsNone(BackupsDialog(None, "A", []).selected())


class ApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(nexusapikey="old")
        config_patch = patch("src.gui.nexus_dialogs.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        message_patch = patch("src.gui.nexus_dialogs.QMessageBox")
        self.message = message_patch.start()
        self.addCleanup(message_patch.stop)

    def ask(self, *answers):
        return patch("src.gui.nexus_dialogs.QInputDialog.getText", side_effect=list(answers))

    def test_cancel_and_clear(self):
        with self.ask(("x", False)):
            self.assertEqual(askApiKey(), "")
        self.assertEqual(self.config.nexusapikey, "old")
        with self.ask(("  ", True)):
            self.assertEqual(askApiKey(), "")
        self.assertEqual(self.config.nexusapikey, "")

    def test_invalid_key_is_asked_again_then_stored(self):
        with (
            self.ask((" bad ", True), (" good ", True)) as dialog,
            patch("src.gui.nexus_dialogs.NexusClient") as client,
        ):
            client.return_value.validate.side_effect = [
                NexusError("rejected", 401),
                {"name": "someone", "is_premium": True},
            ]
            self.assertEqual(askApiKey(), "good")
        self.assertEqual([call.args[0] for call in client.call_args_list], ["bad", "good"])
        self.assertEqual(dialog.call_args_list[1].args[4], "bad")
        self.assertEqual(self.config.nexusapikey, "good")
        self.message.warning.assert_called_once()
        self.assertIn("someone (Premium)", self.message.information.call_args.args[2])

    def test_free_account_is_told_about_downloads(self):
        with self.ask(("key", True)), patch("src.gui.nexus_dialogs.NexusClient") as client:
            client.return_value.validate.return_value = {"name": "someone", "is_premium": False}
            askApiKey()
        self.assertEqual(self.message.information.call_count, 2)
        self.assertIn("Premium", self.message.information.call_args.args[2])


class ConfigurationNexusSettingsTests(unittest.TestCase):
    def test_folders_and_key(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(Configuration, "write_config"):
            root = Path(temporary)
            (root / "Documents").mkdir()
            manager = root / "Manager"
            config = Configuration(str(root / "Documents"), configPath=str(manager))
            self.assertEqual(Path(config.archives), manager / "downloads")
            self.assertEqual(Path(config.backups), manager / "backups")
            config.archives = "D:/mods"
            config.backups = "D:/backups"
            self.assertEqual((config.archives, config.backups), ("D:/mods", "D:/backups"))
            self.assertEqual(config.rmmr, "")
            config.rmmr = "D:/mods/Witcher 3 Mod Manager"
            self.assertEqual(config.rmmr, "D:/mods/Witcher 3 Mod Manager")

            self.assertEqual(config.nexusapikey, "")
            config.nexusapikey = "  secret-key  "
            self.assertNotIn("secret", config.get("NEXUS", "apikey"))
            self.assertEqual(config.nexusapikey, "secret-key")
            config.nexusapikey = ""
            self.assertEqual(config.get("NEXUS", "apikey"), "")
            self.assertEqual(config.nexusapikey, "")


if __name__ == "__main__":
    unittest.main()
