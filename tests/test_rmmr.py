import calendar
import hashlib
import json
import tempfile
import time
import unittest
import xml.etree.ElementTree as XML
from base64 import b64encode
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from src.core.model import Model
from src.core.nexus import NexusInfo, archiveUploadTime
from src.core.rmmr import (
    ADD,
    LINK,
    SKIP,
    Download,
    ImportItem,
    ImportPlan,
    RmmrError,
    applyImport,
    isRmmrFolder,
    listDownloads,
    nexusFromMetadata,
    pickDownload,
    planImport,
    readInventory,
    translateEntry,
)
from src.domain.mod import Mod
from src.gui.main_widget import CustomMainWidget
from src.gui.nexus_dialogs import RmmrImportDialog

INSTALLED = 1_791_500_000
DATE = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(INSTALLED))


def entry(name, *children, enabled="True", priority="Not Set", date=DATE):
    attributes = f'name="{name}" enabled="{enabled}" date="{date}" priority="{priority}"'
    return f"<mod {attributes}>{''.join(children)}</mod>"


class RmmrFolder:
    '''Writes an RMMR folder: installed.xml, mod_metadata.json, config.ini and optionally Nexus Updater records'''

    def __init__(self, root: Path, game: str):
        self.folder = root / "RMMR"
        self.folder.mkdir()
        (self.folder / "config.ini").write_text(f"[PATHS]\ngameexe={game}/bin/x64_dx12/witcher3.exe\n")

    def write(self, mods: list[str], metadata: dict, records: dict | None = None):
        (self.folder / "installed.xml").write_text("<installed>" + "".join(mods) + "</installed>", encoding="utf-8")
        (self.folder / "mod_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        if records is not None:
            (self.folder / "NexusUpdater").mkdir(exist_ok=True)
            state = json.dumps({"downloads": records, "choices": {}})
            (self.folder / "NexusUpdater" / "state.json").write_text(state, encoding="utf-8-sig")

    def digest(self) -> str:
        files = sorted(path for path in self.folder.rglob("*") if path.is_file())
        return hashlib.sha256(b"".join(path.read_bytes() for path in files)).hexdigest()


def meta(modId, **extra):
    return {"nexus_mod_id": str(modId), "nexus_game_domain": "witcher3", "installed_at": INSTALLED, **extra}


class TranslationTests(unittest.TestCase):
    def test_rmmr_elements_are_translated(self):
        readme = b64encode("Read me".encode()).decode()
        element = XML.fromstring(
            entry(
                "Example",
                "<data>modExample</data><dlc>dlcExample</dlc>",
                '<menu path="bin/config/r4game/user_config_matrix/pc/Example.xml" original="False" />',
                '<menu path="bin/config/r4game/user_config_matrix/pc/shared.xml" claims="2" '
                'encoding="zlib-base64-json">eNrt</menu>',
                '<ini path="bin/config/base/rendering.ini" section="R" key="A" baseline="present">1</ini>',
                '<ini path="bin/config/base/rendering.ini" section="R" key="B" baseline="absent">2</ini>',
                '<ini path="bin/config/platform/pc/Other.ini" section="R" key="C">3</ini>',
                '<bin original="False">bin/x64_dx12/xinput9_1_0.dll</bin>',
                '<xmlkey>&lt;Var id="A" /&gt;</xmlkey><hidden>&lt;Var id="H" /&gt;</hidden>',
                '<key context="[Boat]">IK_F2=(Action=Toggle)</key>',
                '<setting context="fhudHUD" dx12-baseline="present" dx12-previous="2">enabled=true</setting>',
                f"<readmeb64>{readme}</readmeb64><readmeb64>not base64!</readmeb64>",
                enabled="False",
                priority="7",
            )
        )
        translated = translateEntry(element)
        mod = translated.mod
        self.assertEqual((mod.name, mod.enabled, mod.date, mod._priority), ("Example", False, DATE, "7"))
        self.assertEqual((mod.files, mod.dlcs), (["modExample"], ["dlcExample"]))
        self.assertEqual(mod.menus, ["Example.xml", "shared.xml"])
        self.assertEqual(mod.inis, ["rendering.ini"])
        self.assertEqual(mod.xmlkeys, ['<Var id="A" />'])
        self.assertEqual(mod.hidden, ['<Var id="H" />'])
        self.assertEqual((mod.inputsettings[0].context, str(mod.inputsettings[0])), ("[Boat]", "IK_F2=(Action=Toggle)"))
        self.assertEqual((mod.usersettings[0].context, str(mod.usersettings[0])), ("fhudHUD", "enabled=true"))
        self.assertEqual(mod.readmes, ["Read me"])
        self.assertEqual(translated.installedAt, INSTALLED)
        self.assertEqual(
            translated.notes,
            [
                "RMMR merged settings into bin/config/base/rendering.ini; they stay as they are",
                "RMMR merged settings into bin/config/platform/pc/Other.ini; they stay as they are",
            ],
        )
        self.assertEqual(mod.bins, ["x64_dx12/xinput9_1_0.dll"])

    def test_unset_priority_and_unreadable_inventory(self):
        self.assertFalse(translateEntry(XML.fromstring(entry("A"))).mod._priority)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RmmrError):
                readInventory(directory)
            Path(directory, "installed.xml").write_text("<mods/>")
            with self.assertRaises(RmmrError):
                readInventory(directory)
            Path(directory, "installed.xml").write_text("<installed><mod name=''/>" + entry("B") + "</installed>")
            self.assertEqual([item.mod.name for item in readInventory(directory)], ["B"])


class DownloadEvidenceTests(unittest.TestCase):
    def test_archive_upload_time(self):
        self.assertEqual(
            archiveUploadTime("Example 5 1.0 2026-10-08T12-42Z abc123.zip"),
            calendar.timegm((2026, 10, 8, 12, 42, 0)),
        )
        self.assertEqual(archiveUploadTime("Friendly HUD-365-12-3-1600000000.zip"), 1600000000)
        self.assertEqual(archiveUploadTime("modExample.zip"), 0)

    def test_archives_and_updater_records_are_listed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archives = root / "archives"
            archives.mkdir()
            (archives / "Example 5 1.0 2026-10-08T12-42Z abc.zip").write_text("")
            (archives / "notes.txt").write_text("")
            (root / "RMMR" / "NexusUpdater").mkdir(parents=True)
            records = {
                "77": {
                    "modId": "5",
                    "name": "Example",
                    "version": "2.0",
                    "downloadedAt": 5,
                    "path": "C:\\d\\E 2.0.zip",
                },
                "bad": {"modId": "5"},
                "78": {"name": "no mod id"},
            }
            (root / "RMMR/NexusUpdater/state.json").write_text(json.dumps({"downloads": records}), encoding="utf-8-sig")
            downloads = listDownloads([str(archives), str(root / "missing")], str(root / "RMMR"))
        self.assertEqual(
            downloads,
            [
                Download(5, "Example", calendar.timegm((2026, 10, 8, 12, 42, 0)), "Example 5 1.0 2026-10-08T12-42Z abc.zip",
                         version="1.0"),
                Download(5, "Example", 5, "E 2.0.zip", 77, "2.0"),
            ],
        )  # fmt: skip
        self.assertEqual(listDownloads([], ""), [])

    def test_pick_newest_matching_download_before_install(self):
        downloads = [
            Download(5, "Example", 100, "old.zip", version="1.0"),
            Download(5, "Example", 200, "new.zip", 22, "2.0"),
            Download(5, "Example", 900, "later.zip", 33, "3.0"),
            Download(5, "Patch for Something Else", 300, "patch.zip", 44, "1.0"),
            Download(6, "Example", 250, "other mod.zip", 55, "1.0"),
        ]
        self.assertEqual(pickDownload(downloads, 5, "Example", 500).archive, "new.zip")
        self.assertEqual(pickDownload(downloads, 5, "Example", 150).archive, "old.zip")
        self.assertIsNone(pickDownload(downloads, 5, "Example", 50))
        self.assertEqual(pickDownload(downloads, 5, "Example", 0).archive, "later.zip")
        self.assertIsNone(pickDownload(downloads, 5, "Completely Different", 500))
        tie = [Download(5, "Example", 100, "archive.zip"), Download(5, "Example", 100, "record.zip", 9)]
        self.assertEqual(pickDownload(tie, 5, "Example", 500).fileId, 9)

    def test_nexus_details_from_metadata(self):
        item = translateEntry(XML.fromstring(entry("Example")))
        downloads = [Download(5, "Example", 100, "Example 5 2.0 x.zip", 22, "2.0")]
        self.assertIsNone(nexusFromMetadata({}, item, downloads))
        self.assertIsNone(nexusFromMetadata({"nexus_mod_id": "modio:4"}, item, downloads))
        self.assertEqual(
            nexusFromMetadata(meta(5), item, downloads), NexusInfo(5, 22, "2.0", archive="Example 5 2.0 x.zip")
        )
        exact = nexusFromMetadata(meta(5, nexus_file_id="77", installed_version="1.5"), item, downloads)
        self.assertEqual(exact, NexusInfo(5, 77, "1.5"))
        self.assertEqual(nexusFromMetadata(meta(5), item, []), NexusInfo(5))


class PlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        game = self.root / "The Witcher 3"
        for directory in ("content", "Mods", "DLC", "bin/config/r4game/user_config_matrix/pc"):
            (game / directory).mkdir(parents=True)
        (self.root / "Manager").mkdir()
        self.config = SimpleNamespace(
            game=game.as_posix(),
            mods=str(game / "Mods"),
            dlc=str(game / "DLC"),
            menu=str(game / "bin/config/r4game/user_config_matrix/pc"),
            settings=str(self.root),
            configuration=str(self.root / "Manager"),
            priority=SimpleNamespace(has_section=lambda section: False),
            setPriority=Mock(),
            write_priority=Mock(),
        )
        config_patch = patch("src.globals.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.game = game
        self.rmmr = RmmrFolder(self.root, game.as_posix())
        self.model = Model(ignorelock=True)

    def folder(self, *parts):
        path = self.game.joinpath(*parts)
        path.mkdir(parents=True)
        return path

    def items(self, plan: ImportPlan) -> dict[str, ImportItem]:
        return {item.name: item for item in plan.items}

    def test_plan_covers_every_case_and_reads_rmmr_only(self):
        self.folder("Mods", "modEnabled")
        self.folder("Mods", "~modDisabled")
        self.folder("Mods", "~modMismatch")
        self.folder("Mods", "modOwned")
        self.folder("Mods", "modRenamed")
        dlc = self.folder("DLC", "dlcOff", "content")
        (dlc / "blob.bundle.disabled").write_text("")
        menu = Path(self.config.menu)
        (menu / "MenuOnly.xml.disabled").write_text("")
        (menu / "Shared.xml").write_text("")

        existing = Mod(files=["modRenamed"])
        existing._name = "Renamed Here"
        owner = Mod(files=["modOwned"])
        owner._name = "Owner"
        linked = Mod(files=["modLinked"], nexus=NexusInfo(9))
        linked._name = "Linked"
        for mod in (existing, owner, linked):
            self.model.modList[mod.name] = mod

        self.rmmr.write(
            [
                entry("Enabled", "<data>modEnabled</data>"),
                entry("Disabled", "<data>modDisabled</data>", enabled="False"),
                entry("Mismatch", "<data>modMismatch</data>"),
                entry("DLC Off", "<dlc>dlcOff</dlc>"),
                entry("Menu Only", '<menu path="bin/config/r4game/user_config_matrix/pc/MenuOnly.xml" />'),
                entry("Not Here", "<data>modNotHere</data>",
                      '<menu path="bin/config/r4game/user_config_matrix/pc/Shared.xml" />'),
                entry("Renamed", "<data>modRenamed</data>"),
                entry("Linked", "<data>modLinked</data>"),
                entry("Claims Owned", "<data>modOwned</data><data>modEnabled</data>"),
                entry("No Id", "<data>modRenamed</data><data>modExtra</data>"),
            ],
            {
                "Enabled": meta(1), "Disabled": meta(2), "Mismatch": meta(3), "DLC Off": meta(4),
                "Menu Only": meta(5), "Not Here": meta(6), "Renamed": meta(7), "Linked": meta(8),
                "Claims Owned": meta(10), "modio:1": {"nexus_mod_id": ""},
            },
        )  # fmt: skip
        before = self.rmmr.digest()
        plan = planImport(str(self.rmmr.folder), self.model, [])
        self.assertEqual(self.rmmr.digest(), before)
        self.assertEqual(plan.warnings, [])
        items = self.items(plan)
        actions = {name: item.action for name, item in items.items()}
        self.assertEqual(
            actions,
            {
                "Enabled": ADD, "Disabled": ADD, "Mismatch": ADD, "DLC Off": ADD, "Menu Only": ADD,
                "Not Here": SKIP, "Renamed": LINK, "Linked": SKIP, "Claims Owned": SKIP, "No Id": SKIP,
            },
        )  # fmt: skip
        self.assertTrue(items["Enabled"].mod.enabled)
        self.assertEqual(items["Enabled"].mod.nexus, NexusInfo(1))
        self.assertFalse(items["Disabled"].mod.enabled)
        self.assertFalse(items["Mismatch"].mod.enabled)
        self.assertIn("using the files", items["Mismatch"].notes[0])
        self.assertFalse(items["DLC Off"].mod.enabled)
        self.assertFalse(items["Menu Only"].mod.enabled)
        self.assertEqual(items["Not Here"].reason, "Not installed in this game folder")
        self.assertEqual((items["Renamed"].target, items["Renamed"].nexus), ("Renamed Here", NexusInfo(7)))
        self.assertIn("as Renamed Here", items["Renamed"].reason)
        self.assertEqual(items["Linked"].reason, "Already linked to Nexus")
        self.assertEqual(items["Claims Owned"].reason, "Its folders belong to Owner")
        self.assertIn("Its folders belong to Renamed Here", items["No Id"].reason)
        self.assertEqual((plan.count(ADD), plan.count(LINK), plan.count(SKIP)), (5, 1, 4))
        self.assertIsNone(items["No Id"].nexus)

    def test_mods_that_only_change_ini_settings(self):
        base = self.game / "bin/config/base"
        base.mkdir(parents=True)
        (base / "rendering.ini").write_text("[Rendering]\n")
        ini = '<ini path="bin/config/base/rendering.ini" section="R" key="A">1</ini>'
        gone = '<ini path="bin/config/base/missing.ini" section="R" key="A">1</ini>'
        self.rmmr.write(
            [entry("Wind", ini, enabled="False"), entry("Gone", gone)], {"Wind": meta(10486), "Gone": meta(2)}
        )
        items = self.items(planImport(str(self.rmmr.folder), self.model, []))
        self.assertEqual(items["Wind"].action, ADD)
        self.assertEqual(items["Wind"].mod.inis, ["rendering.ini"])
        self.assertFalse(items["Wind"].mod.enabled)  # the ini can't tell, so RMMR's record is kept
        self.assertEqual((items["Gone"].action, items["Gone"].reason), (SKIP, "Not installed in this game folder"))

    def test_existing_entry_without_nexus_id_in_rmmr(self):
        mod = Mod(files=["modA"])
        mod._name = "A"
        self.model.modList["A"] = mod
        self.rmmr.write([entry("A", "<data>modA</data>")], {})
        item = planImport(str(self.rmmr.folder), self.model, []).items[0]
        self.assertEqual((item.action, item.reason), (SKIP, "Already here; RMMR has no Nexus ID for it"))

    def test_other_game_folder_is_warned_about(self):
        (self.rmmr.folder / "config.ini").write_text("[PATHS]\ngameexe=D:/Games/The Witcher 3/bin/x64/witcher3.exe\n")
        self.rmmr.write([], {})
        plan = planImport(str(self.rmmr.folder), self.model, [])
        self.assertIn("different game folder (d:/games/the witcher 3)", plan.warnings[0])

    def test_folder_without_rmmr_files_is_rejected(self):
        self.assertFalse(isRmmrFolder(str(self.root)))
        with self.assertRaises(RmmrError):
            planImport(str(self.root), self.model, [])
        (self.rmmr.folder / "installed.xml").write_text("<installed/>")
        (self.rmmr.folder / "mod_metadata.json").write_text("{broken")
        with self.assertRaises(RmmrError):
            planImport(str(self.rmmr.folder), self.model, [])

    def test_apply_adds_links_and_saves(self):
        self.folder("Mods", "modNew")
        target = Mod(files=["modOld"])
        target._name = "Old"
        self.model.modList["Old"] = target
        self.rmmr.write(
            [entry("New", "<data>modNew</data>", priority="3"), entry("Old", "<data>modOld</data>")],
            {"New": meta(1), "Old": meta(2)},
        )
        plan = planImport(str(self.rmmr.folder), self.model, [])
        self.assertEqual(applyImport(plan, self.model), (1, 1))
        self.config.setPriority.assert_called_once_with("modNew", "3")
        self.config.write_priority.assert_called_once()
        reloaded = Model(ignorelock=True)
        self.assertEqual(sorted(reloaded.list()), ["New", "Old"])
        self.assertEqual(reloaded.get("New").nexus, NexusInfo(1))
        self.assertEqual(reloaded.get("Old").nexus, NexusInfo(2))
        self.assertEqual(reloaded.get("New").date, DATE)
        # running it again changes nothing
        with patch.object(self.model, "write") as write:
            again = planImport(str(self.rmmr.folder), self.model, [])
            self.assertEqual(applyImport(again, self.model), (0, 0))
            write.assert_not_called()


class ImportHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.config = SimpleNamespace(rmmr="", archives="C:/archives")
        config_patch = patch("src.gui.main_widget.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.widget = Mock()

    def run_import(self, folder, isRmmr=True, plan=None, accepted=True, error=None):
        with (
            patch("src.gui.main_widget.QFileDialog.getExistingDirectory", return_value=folder),
            patch("src.gui.main_widget.isRmmrFolder", return_value=isRmmr),
            patch("src.gui.main_widget.planImport", side_effect=error, return_value=plan) as planned,
            patch("src.gui.main_widget.RmmrImportDialog") as dialog,
            patch("src.gui.main_widget.applyImport", return_value=(2, 3)) as applied,
        ):
            dialog.return_value.exec.return_value = accepted
            CustomMainWidget.importFromRmmr(self.widget)
        return planned, dialog, applied

    def outputs(self):
        return "\n".join(str(call.args[0]) for call in self.widget.output.call_args_list)

    def test_cancelled_or_wrong_folder(self):
        planned, _, _ = self.run_import("")
        planned.assert_not_called()
        planned, _, _ = self.run_import("C:\\Somewhere", isRmmr=False)
        planned.assert_not_called()
        self.assertIn("No RMMR installed.xml", self.outputs())
        self.assertEqual(self.config.rmmr, "")

    def test_read_error_is_reported(self):
        _, dialog, _ = self.run_import("C:/RMMR", error=RmmrError("unreadable"))
        self.assertIn("unreadable", self.outputs())
        dialog.assert_not_called()

    def test_preview_then_apply(self):
        plan = ImportPlan([])
        _, dialog, applied = self.run_import("C:\\RMMR", plan=plan, accepted=False)
        applied.assert_not_called()
        self.assertEqual(self.config.rmmr, "C:/RMMR")
        planned, dialog, applied = self.run_import("C:/RMMR", plan=plan)
        planned.assert_called_once_with("C:/RMMR", self.widget.model, ["C:/archives"])
        applied.assert_called_once_with(plan, self.widget.model)
        self.widget.refreshList.assert_called_once()
        self.assertIn("Imported from RMMR: 2 added, 3 linked to Nexus", self.outputs())

    def test_dialog_lists_actions_in_order(self):
        plan = ImportPlan(
            [
                ImportItem("Zed", SKIP, "Not installed in this game folder"),
                ImportItem("Beta", LINK, "Already here", nexus=NexusInfo(5, version="v1.0", archive="b.zip")),
                ImportItem("Alpha", ADD, "Adds it", nexus=NexusInfo(4), notes=["a note"]),
            ],
            ["RMMR manages a different game folder"],
        )
        dialog = RmmrImportDialog(None, plan)
        rows = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
        self.assertEqual([(row.text(0), row.text(1)) for row in rows],
                         [("Alpha", "Add"), ("Beta", "Link Nexus ID"), ("Zed", "Skip")])  # fmt: skip
        self.assertEqual(rows[0].text(3), "Adds it; a note")
        self.assertEqual(rows[1].text(2), "5  v1.0")
        self.assertIn("b.zip", rows[1].toolTip(2))
        self.assertTrue(dialog.importButton.isEnabled())
        nothing = RmmrImportDialog(None, ImportPlan([ImportItem("Zed", SKIP, "x")]))
        self.assertFalse(nothing.importButton.isEnabled())


if __name__ == "__main__":
    unittest.main()
