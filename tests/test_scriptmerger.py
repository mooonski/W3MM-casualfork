import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from src.core.nexus import ModFiles, NexusError, NexusFile
from src.core.smupdate import ScriptMergerUpdate, ScriptMergerUpdateFailed
from src.core.scriptmerger import (
    LEFTOVER,
    MERGED,
    NOT_MERGED,
    OUTDATED,
    ConflictReport,
    Merge,
    ScriptConflict,
    analyse,
    fileHash,
    findScriptMerger,
    installedVersion,
    isNewer,
    latestScriptMerger,
    nexusId,
    readInventory,
    xxh32,
)
from src.gui.main_widget import CustomMainWidget
from src.gui.scriptmerger_dialog import (
    OPEN_PAGE,
    UPDATE,
    MessageAlertScriptConflicts,
    ScriptConflictsDialog,
    askScriptMergerUpdate,
    summary,
)


class HashTests(unittest.TestCase):
    def test_xxhash32_reference_values(self):
        self.assertEqual(xxh32(b""), 0x02CC5D05)
        self.assertEqual(xxh32(b"a"), 0x550D7456)
        self.assertEqual(xxh32(b"abc"), 0x32D153FF)
        self.assertEqual(xxh32(b"Nobody inspects the spammish repetition"), 0xE2293B2F)
        self.assertEqual(xxh32(b"", seed=1), 0x0B2CB792)

    def test_file_hash_is_script_mergers_format(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory, "a.ws")
            script.write_bytes(b"abc")
            self.assertEqual(fileHash(str(script)), "32D153FF")


def inventory(*merges: tuple[str, dict[str, str]], merged="mod0000_MergedFiles") -> str:
    body = ""
    for relative, included in merges:
        mods = "".join(
            f'<IncludedMod Hash="{value}" ModIoId="0">{name}</IncludedMod>' for name, value in included.items()
        )
        body += f"<Merge><RelativePath>{relative}</RelativePath>{mods}<MergedModName>{merged}</MergedModName></Merge>"
    return f'<?xml version="1.0" encoding="utf-8"?><MergeInventory>{body}</MergeInventory>'


class AnalyseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.mods = self.root / "Mods"
        self.merger = self.root / "Script Merger"
        self.merger.mkdir()

    def script(self, folder: str, relative: str, text: str = "") -> str:
        target = self.mods / folder / "content" / "scripts" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text or f"// {folder} {relative}")
        return fileHash(str(target))

    def write(self, *merges, **kwargs):
        (self.merger / "MergeInventory.xml").write_text(inventory(*merges, **kwargs), encoding="utf-8")

    def states(self, report: ConflictReport) -> dict[str, tuple[str, str]]:
        return {
            conflict.relativePath.replace("\\", "/"): (conflict.state, conflict.detail) for conflict in report.conflicts
        }

    def test_every_state(self):
        a = self.script("modA", "game/merged.ws")
        b = self.script("modB", "game/merged.ws")
        self.script("mod0000_MergedFiles", "game/merged.ws")
        self.script("modA", "game/new.ws")
        self.script("modB", "game/NEW.ws")
        c = self.script("modA", "game/missing.ws")
        self.script("modC", "game/missing.ws")
        self.script("modB", "game/missing.ws")
        d = self.script("modA", "game/changed.ws")
        e = self.script("modB", "game/changed.ws", "original")
        self.script("modB", "game/changed.ws", "updated since")
        self.script("mod0000_MergedFiles", "game/changed.ws")
        f = self.script("modA", "game/nofile.ws")
        g = self.script("modB", "game/nofile.ws")
        h = self.script("modA", "game/gone.ws")
        i = self.script("modB", "game/gone.ws")
        self.script("modA", "game/leftover.ws")
        self.script("~modDisabled", "game/leftover.ws")
        self.script("mod0000_MergedFiles", "game/gone.ws")
        self.script("modA", "game/other.txt")
        self.script("modB", "game/other.txt")
        self.write(
            ("game\\merged.ws", {"modA": a, "modB": b}),
            ("game\\missing.ws", {"modA": c, "modC": "00000000"}),
            ("game\\changed.ws", {"modA": d, "modB": e}),
            ("game\\nofile.ws", {"modA": f, "modB": g}),
            ("game\\gone.ws", {"modA": h, "modB": i, "modGone": "12345678"}),
            ("game\\leftover.ws", {"modA": "1", "modDisabled": "2"}),
        )
        report = analyse(str(self.mods), str(self.merger))
        self.assertTrue(report.inventoryFound)
        states = self.states(report)
        self.assertEqual(
            states,
            {
                "game/merged.ws": (MERGED, "merged"),
                "game/new.ws": (NOT_MERGED, "not merged"),
                "game/missing.ws": (NOT_MERGED, "not in the merge: modB"),
                "game/changed.ws": (OUTDATED, "changed since the merge: modB"),
                "game/nofile.ws": (OUTDATED, "the merged file is missing"),
                "game/gone.ws": (OUTDATED, "the merge includes mods no longer enabled: modgone"),
                "game/leftover.ws": (
                    LEFTOVER,
                    "merged, but the mods no longer conflict; run Script Merger to remove the merge",
                ),
            },
        )
        merged = next(conflict for conflict in report.conflicts if conflict.state == MERGED)
        self.assertEqual(merged.mods, ["modA", "modB"])
        self.assertEqual(len(report.pending), 6)

    def test_hashes_without_leading_zeros_still_match(self):
        a = self.script("modA", "game/merged.ws", "// modA 30")
        b = self.script("modB", "game/merged.ws")
        self.script("mod0000_MergedFiles", "game/merged.ws")
        self.assertEqual(a, "0155A7C3")
        # Script Merger writes the hash as a plain hex number
        self.write(("game\\merged.ws", {"modA": "155a7c3", "modB": b}))
        report = analyse(str(self.mods), str(self.merger))
        self.assertEqual(self.states(report), {"game/merged.ws": (MERGED, "merged")})
        self.assertEqual(readInventory(str(self.merger))[0].included["modA"], "0155A7C3")

    def test_without_script_merger_everything_is_unmerged(self):
        self.script("modA", "game/a.ws")
        self.script("modB", "game/a.ws")
        self.script("modA", "game/only.ws")
        for folder in ("", str(self.merger)):
            report = analyse(str(self.mods), folder)
            self.assertFalse(report.inventoryFound)
            self.assertEqual([(c.relativePath, c.state) for c in report.conflicts], [("game\\a.ws", NOT_MERGED)])
        self.assertEqual(analyse(str(self.root / "missing"), "").conflicts, [])

    def test_custom_merged_folder_is_not_a_conflict(self):
        a = self.script("modA", "game/a.ws")
        b = self.script("modB", "game/a.ws")
        self.script("mod0000_Custom", "game/a.ws")
        self.write(("game\\a.ws", {"modA": a, "modB": b}), merged="mod0000_Custom")
        self.assertEqual(self.states(analyse(str(self.mods), str(self.merger))), {"game/a.ws": (MERGED, "merged")})

    def test_inventory_reading(self):
        self.assertIsNone(readInventory(str(self.merger)))
        (self.merger / "MergeInventory.xml").write_text("<broken")
        self.assertIsNone(readInventory(str(self.merger)))
        self.write(("game\\a.ws", {"modA": "abcdef01"}), ("", {"modB": "1"}))
        self.assertEqual(readInventory(str(self.merger)), [Merge("game\\a.ws", {"modA": "ABCDEF01"})])


class FindTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def exe(self, *parts) -> Path:
        target = self.root.joinpath(*parts, "WitcherScriptMerger.exe")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
        return target

    def test_found_next_to_archives_game_or_through_rmmr(self):
        archives = self.root / "witcher 3 mods"
        game = self.root / "Steam" / "The Witcher 3"
        (game / "content").mkdir(parents=True)
        self.assertEqual(findScriptMerger(str(game), str(archives)), "")
        found = self.exe("witcher 3 mods", "script merger")
        self.assertEqual(findScriptMerger(str(game), str(archives)), found.as_posix())
        found.unlink()
        self.exe("Steam", "The Witcher 3", "content", "hidden")  # inside game data, never searched
        self.exe("Steam", "Tools", "a", "b", "c")  # too deep
        self.assertEqual(findScriptMerger(str(game), str(archives)), "")
        sibling = self.exe("Steam", "Script Merger")
        self.assertEqual(findScriptMerger(str(game), str(archives)), sibling.as_posix())

        rmmr = self.root / "RMMR"
        rmmr.mkdir()
        elsewhere = self.exe("Elsewhere", "SM")
        (rmmr / "config.ini").write_text(f"[PATHS]\nscriptmerger={elsewhere.as_posix()}\n")
        self.assertEqual(findScriptMerger("", "", str(rmmr)), elsewhere.as_posix())

    def test_versions(self):
        self.assertEqual(installedVersion(""), "")
        self.assertEqual(installedVersion(str(self.root / "missing.exe")), "")
        if sys.platform == "win32":
            self.assertRegex(installedVersion(sys.executable), r"^\d+\.\d+")
        self.assertEqual((nexusId("re"), nexusId("ng"), nexusId("og")), (13076, 8405, 8405))
        self.assertTrue(isNewer("1.5", "1.4"))
        self.assertTrue(isNewer("v1.10", "1.9"))
        self.assertFalse(isNewer("1.4", "1.4.0.1"))
        self.assertFalse(isNewer("1.4", "1.4"))
        self.assertFalse(isNewer("", "1.4"))
        self.assertTrue(isNewer("beta", "alpha"))


def conflict(relative, state, mods=("modA", "modB")):
    return ScriptConflict(relative, list(mods), state, state)


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_rows_heading_and_prompt(self):
        report = ConflictReport(
            [
                conflict("game\\z.ws", MERGED),
                conflict("game\\a.ws", LEFTOVER, ["modA"]),
                conflict("game\\b.ws", NOT_MERGED),
                conflict("game\\c.ws", OUTDATED),
            ],
            inventoryFound=True,
        )
        dialog = ScriptConflictsDialog(None, report, "C:/SM/WitcherScriptMerger.exe", "1.4")
        rows = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
        self.assertEqual(
            [(row.text(0), row.text(2)) for row in rows],
            [("game\\b.ws", "Not merged"), ("game\\c.ws", "Merge out of date"), ("game\\a.ws", "Old merge"),
             ("game\\z.ws", "Merged")],
        )  # fmt: skip
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertIn("Script Merger 1.4: C:/SM/WitcherScriptMerger.exe", labels[0])
        self.assertIn("3 script conflicts, 3 files need Script Merger.", labels[0])
        self.assertTrue(dialog.runButton.isDefault())

        empty = ScriptConflictsDialog(None, ConflictReport(), "", "")
        text = [label.text() for label in empty.findChildren(QLabel)][0]
        self.assertIn("Script Merger wasn't found", text)
        self.assertIn("No scripts are changed by more than one enabled mod.", text)

    def test_summary_and_alert(self):
        many = [conflict(f"game\\{i}.ws", NOT_MERGED) for i in range(8)]
        text = summary(many)
        self.assertEqual(len(text.splitlines()), 7)
        self.assertIn("... 2 more", text)
        with patch(
            "src.gui.scriptmerger_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ) as q:
            self.assertEqual(MessageAlertScriptConflicts(many[:2], True), QMessageBox.StandardButton.Yes)
            self.assertIn("2 script files need Script Merger", q.call_args.args[2])
            self.assertIn("run it now", q.call_args.args[2])
            MessageAlertScriptConflicts(many[:1], False)
            self.assertIn("wasn't found", q.call_args.args[2])


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            allowpopups="1", mods="C:/Game/Mods", scriptmerger="C:/SM/WitcherScriptMerger.exe", game="C:/Game",
            archives="C:/archives", rmmr="", gameversion="re",
        )  # fmt: skip
        config_patch = patch("src.gui.main_widget.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.widget = Mock()
        self.widget.scriptMergerRunning.return_value = False

    def outputs(self):
        return "\n".join(str(call.args[0]) for call in self.widget.output.call_args_list)

    def test_prompt_only_when_something_needs_merging(self):
        alert = "src.gui.main_widget.MessageAlertScriptConflicts"
        self.widget.scriptConflictReport.return_value = ConflictReport([conflict("a.ws", MERGED)], True)
        with patch(alert) as asked:
            CustomMainWidget.promptScriptMerger(self.widget)
        asked.assert_not_called()
        self.assertIn("all merged", self.outputs())

        pending = conflict("b.ws", OUTDATED)
        self.widget.scriptConflictReport.return_value = ConflictReport([pending], True)
        with patch(alert, return_value=QMessageBox.StandardButton.Yes) as asked:
            CustomMainWidget.promptScriptMerger(self.widget)
        asked.assert_called_once_with([pending], True)
        self.widget.runScriptMerger.assert_called_once()

        self.widget.runScriptMerger.reset_mock()
        with patch(alert, return_value=QMessageBox.StandardButton.No):
            CustomMainWidget.promptScriptMerger(self.widget)
        self.widget.runScriptMerger.assert_not_called()

        self.config.allowpopups = "0"
        self.widget.scriptConflictReport.reset_mock()
        CustomMainWidget.promptScriptMerger(self.widget)
        self.widget.scriptConflictReport.assert_not_called()

    def test_report_uses_script_merger_folder(self):
        with patch("src.gui.main_widget.analyse", return_value=ConflictReport()) as analysed:
            CustomMainWidget.scriptConflictReport(self.widget)
            analysed.assert_called_once_with("C:/Game/Mods", "C:/SM")
            self.config.scriptmerger = ""
            CustomMainWidget.scriptConflictReport(self.widget)
            analysed.assert_called_with("C:/Game/Mods", "")
            analysed.side_effect = OSError("denied")
            self.assertIsNone(CustomMainWidget.scriptConflictReport(self.widget))
        self.assertIn("denied", self.outputs())
        self.config.mods = None
        self.assertIsNone(CustomMainWidget.scriptConflictReport(self.widget))

    def test_detect(self):
        with patch("src.gui.main_widget.findScriptMerger", return_value="Z:/sm/WitcherScriptMerger.exe") as found:
            self.assertTrue(CustomMainWidget.detectScriptMerger(self.widget))
            found.assert_not_called()
            self.config.scriptmerger = ""
            self.assertTrue(CustomMainWidget.detectScriptMerger(self.widget))
            found.assert_called_once_with("C:/Game", "C:/archives", "")
        self.assertEqual(self.config.scriptmerger, "Z:/sm/WitcherScriptMerger.exe")
        self.assertIn("Found Script Merger: Z:/sm/WitcherScriptMerger.exe", self.outputs())
        self.config.scriptmerger = ""
        with patch("src.gui.main_widget.findScriptMerger", return_value=""):
            self.assertFalse(CustomMainWidget.detectScriptMerger(self.widget))

    def test_run_tries_detection_before_asking(self):
        self.config.scriptmerger = ""
        self.widget.detectScriptMerger.side_effect = lambda: setattr(self.config, "scriptmerger", "C:/SM/x.exe")
        with patch("src.gui.main_widget.platform", "win32"), patch("src.gui.main_widget.subprocess.Popen") as popen:
            CustomMainWidget.runScriptMerger(self.widget)
        self.widget.changeScriptMergerPath.assert_not_called()
        popen.assert_called_once_with(["C:/SM/x.exe"], cwd="C:/SM")

    def test_show_conflicts(self):
        self.widget.scriptConflictReport.return_value = ConflictReport()
        with (
            patch("src.gui.main_widget.ScriptConflictsDialog") as dialog,
            patch("src.gui.main_widget.installedVersion", return_value="1.4"),
        ):
            dialog.return_value.exec.return_value = True
            CustomMainWidget.showScriptConflicts(self.widget)
        dialog.assert_called_once_with(self.widget, ConflictReport(), "C:/SM/WitcherScriptMerger.exe", "1.4")
        self.widget.runScriptMerger.assert_called_once()

    def files(self, *versions):
        return ModFiles(
            {i: NexusFile(i, "Script Merger", version, category, False, i, f"sm{i}.zip", 1)
             for i, (version, category) in enumerate(versions, 1)}
        )  # fmt: skip

    def test_script_merger_update_check(self):
        with (
            patch("src.gui.main_widget.installedVersion", return_value="1.4"),
            patch("src.gui.main_widget.askScriptMergerUpdate", return_value=OPEN_PAGE) as asked,
            patch("src.gui.main_widget.openUrl") as opened,
        ):
            CustomMainWidget.scriptMergerChecked(self.widget, "hidden")
            self.assertIn("Couldn't check Script Merger: hidden", self.outputs())
            CustomMainWidget.scriptMergerChecked(self.widget, self.files(("1.3", "OLD_VERSION"), ("1.4", "MAIN")))
            self.assertIn("Script Merger is up to date (1.4)", self.outputs())
            asked.assert_not_called()
            CustomMainWidget.scriptMergerChecked(
                self.widget, self.files(("1.4", "MAIN"), ("1.5", "MAIN"), ("9", "OPTIONAL"))
            )
            self.assertIn("Script Merger 1.5 is available, you have 1.4", self.outputs())
            asked.assert_called_once_with(self.widget, "1.5", "1.4")
            opened.assert_called_once_with("https://www.nexusmods.com/witcher3/mods/13076?tab=files")
            self.widget.updateScriptMerger.assert_not_called()

            asked.return_value = UPDATE
            files = self.files(("1.5", "MAIN"))
            CustomMainWidget.scriptMergerChecked(self.widget, files)
            self.widget.updateScriptMerger.assert_called_once_with(files.files[1])
            asked.return_value = ""
            CustomMainWidget.scriptMergerChecked(self.widget, files)
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(self.widget.updateScriptMerger.call_count, 1)

    def test_latest_leaves_out_source_code(self):
        files = ModFiles(
            {1: NexusFile(1, "Script Merger Remastered", "1.5", "MAIN", True, 10, "sm.zip", 1),
             2: NexusFile(2, "Script Merger Remastered Source Code", "1.5", "MAIN", False, 20, "src.zip", 1),
             3: NexusFile(3, "Script Merger Remastered", "1.4", "ARCHIVED", False, 30, "old.zip", 1)}
        )  # fmt: skip
        self.assertEqual(latestScriptMerger(files).fileId, 1)
        self.assertIsNone(latestScriptMerger(ModFiles()))

    def test_manual_check(self):
        self.widget.nexusBusy.return_value = False
        client = self.widget.nexusClient.return_value
        with patch("src.gui.main_widget.installedVersion", return_value="1.4"):
            CustomMainWidget.checkScriptMergerUpdate(self.widget)
        work, done = self.widget.runNexus.call_args.args
        self.assertIs(done, self.widget.scriptMergerChecked)
        client.modFiles.return_value = "files"
        self.assertEqual(work(Mock()), "files")
        client.modFiles.assert_called_once_with(13076)
        client.modFiles.side_effect = NexusError("gone", 404)
        self.assertEqual(work(Mock()), "gone")
        client.modFiles.side_effect = NexusError("bad key", 401)
        with self.assertRaises(NexusError):
            work(Mock())

        self.widget.runNexus.reset_mock()
        with patch("src.gui.main_widget.installedVersion", return_value=""):
            CustomMainWidget.checkScriptMergerUpdate(self.widget)
        self.widget.runNexus.assert_not_called()
        self.assertIn("Couldn't read the version of C:/SM/WitcherScriptMerger.exe", self.outputs())

    def test_update_downloads_then_installs(self):
        self.widget.nexusBusy.return_value = False
        self.config.backups = "C:/backups"
        client = self.widget.nexusClient.return_value
        client.downloadLinks.return_value = ["https://cdn/sm.zip"]
        client.download.return_value = "C:/archives/sm.zip"
        file = NexusFile(7, "Script Merger", "1.5.1", "MAIN", True, 1, "SM 13076 1.5.1.zip", 100)
        CustomMainWidget.updateScriptMerger(self.widget, file)
        work, _ = self.widget.runNexus.call_args.args
        with patch("src.gui.main_widget.updateScriptMergerFiles", return_value="done") as update:
            self.assertEqual(work(Mock()), ("C:/archives/sm.zip", "done"))
        client.downloadLinks.assert_called_once_with(13076, 7)
        self.assertEqual(client.download.call_args.args[:4], ("https://cdn/sm.zip", "C:/archives", "SM 13076 1.5.1.zip", 100))
        update.assert_called_once_with("C:/SM/WitcherScriptMerger.exe", "C:/archives/sm.zip", "C:/backups")
        with patch("src.gui.main_widget.updateScriptMergerFiles", side_effect=ScriptMergerUpdateFailed("open")):
            self.assertEqual(work(Mock()), ("C:/archives/sm.zip", "open"))
        client.downloadLinks.side_effect = NexusError("premium only", 403)
        self.assertIsNone(work(Mock()))

        self.widget.runNexus.reset_mock()
        self.widget.scriptMergerRunning.return_value = True
        CustomMainWidget.updateScriptMerger(self.widget, file)
        self.widget.runNexus.assert_not_called()
        self.assertIn("Close Script Merger before updating it.", self.outputs())

    def test_update_results(self):
        file = NexusFile(7, "Script Merger", "1.5.1", "MAIN", True, 1, "sm.zip", 100)
        history = self.widget.downloadHistory
        with patch("src.gui.main_widget.openUrl") as opened:
            CustomMainWidget.scriptMergerUpdated(self.widget, file, None)
        opened.assert_called_once_with("https://www.nexusmods.com/witcher3/mods/13076?tab=files&file_id=7")
        history.add.assert_not_called()

        with patch("src.gui.main_widget.QMessageBox.warning") as warned:
            CustomMainWidget.scriptMergerUpdated(self.widget, file, ("C:/a/sm.zip", "Script Merger is open."))
        warned.assert_called_once()
        entry = history.add.call_args.args[0]
        self.assertEqual((entry.result, entry.message, entry.archive), ("failed", "Script Merger is open.", "C:/a/sm.zip"))

        done = ScriptMergerUpdate("1.5", "1.5.1", "C:/backups/~Script Merger/1.5 x", ["a.dll"])
        CustomMainWidget.scriptMergerUpdated(self.widget, file, ("C:/a/sm.zip", done))
        entry = history.add.call_args.args[0]
        self.assertEqual((entry.result, entry.version, entry.mod), ("updated", "1.5.1", "Script Merger"))
        self.assertIn("Script Merger updated 1.5 -> 1.5.1. The files it replaced are backed up in C:/backups/", self.outputs())
        self.assertNotIn("still reports", self.outputs())
        CustomMainWidget.scriptMergerUpdated(
            self.widget, file, ("C:/a/sm.zip", ScriptMergerUpdate("1.5", "1.5", "C:/b", ["a.dll"]))
        )
        self.assertIn("Script Merger still reports version 1.5, expected 1.5.1.", self.outputs())

    def test_update_check_includes_script_merger_only_for_all_mods(self):
        mod = SimpleNamespace(name="A", nexus=SimpleNamespace(modId=5))
        self.widget.model = SimpleNamespace(all=lambda: [mod])
        self.widget.nexusBusy.return_value = False
        client = self.widget.nexusClient.return_value
        client.modFiles.side_effect = lambda modId: f"files of {modId}"
        with patch("src.gui.main_widget.installedVersion", return_value="1.4"):
            CustomMainWidget.checkForUpdates(self.widget)
            work, _ = self.widget.runNexus.call_args.args
            self.assertEqual(work(Mock()), {5: "files of 5", "scriptmerger": "files of 13076"})
            CustomMainWidget.checkForUpdates(self.widget, ["A"])
            work, _ = self.widget.runNexus.call_args.args
            self.assertEqual(work(Mock()), {5: "files of 5"})

    def test_results_report_script_merger_first(self):
        self.widget.model = SimpleNamespace(modList={}, write=Mock())
        with patch("src.gui.main_widget.UpdatesDialog"):
            CustomMainWidget.updatesChecked(self.widget, [], {"scriptmerger": "x"})
        self.widget.scriptMergerChecked.assert_called_once_with("x")


if __name__ == "__main__":
    unittest.main()


class ToolbarButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.config = SimpleNamespace(scriptmerger="C:/SM/WitcherScriptMerger.exe", mods="C:/Game/Mods")
        config_patch = patch("src.gui.main_widget.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.widget = Mock()

    def outputs(self):
        return "\n".join(str(call.args[0]) for call in self.widget.output.call_args_list)

    def test_a_second_copy_is_not_started(self):
        self.widget.scriptMergerRunning.return_value = True
        with patch("src.gui.main_widget.subprocess.Popen") as popen:
            CustomMainWidget.runScriptMerger(self.widget)
        popen.assert_not_called()
        self.assertIn("already open", self.outputs())

    def test_the_started_program_is_watched(self):
        self.widget.scriptMergerRunning.return_value = False
        with patch("src.gui.main_widget.platform", "win32"), patch("src.gui.main_widget.subprocess.Popen") as popen:
            CustomMainWidget.runScriptMerger(self.widget)
        self.widget.watchScriptMerger.assert_called_once_with(popen.return_value)

    def test_closing_is_noticed_once_and_reported(self):
        process = Mock()
        process.poll.side_effect = [None, None, 0]  # running, running, closed
        widget = SimpleNamespace(output=Mock(), scriptMergerClosed=Mock())
        widget.scriptMergerRunning = lambda: CustomMainWidget.scriptMergerRunning(widget)
        widget.checkScriptMergerClosed = lambda: CustomMainWidget.checkScriptMergerClosed(widget)
        with patch("src.gui.main_widget.QTimer") as timer:
            CustomMainWidget.watchScriptMerger(widget, process)
        timer.return_value.start.assert_called_once()
        self.assertIs(widget.scriptMergerProcess, process)
        self.assertIn("checked here when you close it", widget.output.call_args.args[0])

        widget.checkScriptMergerClosed()  # still open
        widget.scriptMergerClosed.assert_not_called()
        widget.checkScriptMergerClosed()  # still open
        widget.checkScriptMergerClosed()  # closed
        widget.scriptMergerClosed.assert_called_once()
        timer.return_value.stop.assert_called_once()
        self.assertIsNone(widget.scriptMergerProcess)
        self.assertFalse(CustomMainWidget.scriptMergerRunning(widget))

    def test_results_after_closing(self):
        merged = ScriptConflict("a.ws", ["modA", "modB"], MERGED)
        pending = ScriptConflict("b.ws", ["modA", "modB"], OUTDATED)
        cases = [
            (ConflictReport([], True), "Script Merger closed. No scripts conflict."),
            (ConflictReport([merged], True), "Script Merger closed. All 1 script conflict merged."),
            (ConflictReport([merged, pending], True), "Script Merger closed. 1 script file still needs merging"),
        ]
        for report, text in cases:
            self.widget.scriptConflictReport.return_value = report
            CustomMainWidget.scriptMergerClosed(self.widget)
            self.assertIn(text, self.widget.output.call_args.args[0])
        self.assertEqual(self.widget.refreshLoadOrder.call_count, 3)
