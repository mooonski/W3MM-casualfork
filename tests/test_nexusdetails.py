import calendar
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from src.core.health import WARNING, checkHealth
from src.core.nexus import NexusError, NexusInfo
from src.core.nexusdetails import (
    DETAILS_SCHEMA,
    REMASTERED_RELEASE,
    DetailsCache,
    ModDetails,
    NexusGraphQL,
    Requirement,
    parseTime,
    remasteredSuggestions,
    searchTerms,
)
from src.domain.mod import Mod
from src.gui.main_widget import CustomMainWidget
from src.gui.mod_preview import IMAGE_HEIGHT, ModPreview

NODE = {
    "modId": 13130,
    "name": "(Remastered) Friendly HUD",
    "summary": "HUD options",
    "category": "User Interface",
    "author": "wghost81",
    "version": "0.9.3R",
    "updatedAt": "2026-10-08T15:13:10Z",
    "thumbnailUrl": "https://staticdelivery.nexusmods.com/mods/952/images/thumbnails/13130/a b.jpg",
    "pictureUrl": "https://x/p.jpg",
    "endorsements": 222,
    "downloads": 1000,
    "adultContent": False,
}
BEFORE = REMASTERED_RELEASE - 86400
AFTER = REMASTERED_RELEASE + 86400


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def opener(*payloads):
    '''Fake urlopen that answers with the given JSON payloads in turn and records the requests'''
    requests = []
    answers = list(payloads)

    def open_(request, timeout):
        requests.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Response(json.dumps(answer).encode() if not isinstance(answer, bytes) else answer)

    return open_, requests


def body(request):
    return json.loads(request.data.decode())


class DetailsTests(unittest.TestCase):
    def test_parsing(self):
        details = ModDetails.fromGraphQL(NODE, 5)
        self.assertEqual(
            (details.modId, details.category, details.version, details.fetched), (13130, "User Interface", "0.9.3R", 5)
        )
        self.assertEqual(details.updated, calendar.timegm((2026, 10, 8, 15, 13, 10)))
        self.assertEqual(details.url, "https://www.nexusmods.com/witcher3/mods/13130")
        self.assertTrue(details.updatedForRemastered)
        self.assertFalse(ModDetails(1, updated=BEFORE).updatedForRemastered)
        self.assertTrue(ModDetails(1).updatedForRemastered)  # unknown date is not flagged
        self.assertEqual(ModDetails.fromJson({"modId": 3, "name": "A", "unknown": 1}), ModDetails(3, "A"))
        self.assertEqual((parseTime(None), parseTime("garbage")), (0, 0))

    def test_requirements(self):
        node = dict(
            NODE,
            modRequirements={
                "dlcRequirements": [{"gameExpansion": {"name": "Blood and Wine"}, "notes": None}, {"gameExpansion": None}],
                "nexusRequirements": {
                    "nodes": [
                        {"modId": "12997", "modName": "Sharedutils", "gameId": "952", "notes": "Needed",
                         "externalRequirement": False, "url": ""},
                        {"modId": "0", "modName": "ReShade", "gameId": "0", "notes": None,
                         "externalRequirement": True, "url": "https://reshade.me/"},
                        {"modId": "bad"},
                    ]
                },
            },
        )  # fmt: skip
        details = ModDetails.fromGraphQL(node, 5)
        self.assertEqual(details.schema, DETAILS_SCHEMA)
        self.assertEqual(
            details.requirements,
            [
                Requirement("Blood and Wine", dlc=True),
                Requirement("Sharedutils", 12997, 952, "Needed"),
                Requirement("ReShade", external=True, url="https://reshade.me/"),
            ],
        )
        self.assertEqual(details.requirements[1].pageUrl, "https://www.nexusmods.com/witcher3/mods/12997")
        self.assertEqual(details.requirements[2].pageUrl, "https://reshade.me/")
        self.assertEqual(details.requirements[0].pageUrl, "")
        self.assertEqual(ModDetails.fromGraphQL(NODE).schema, 0)  # search results don't carry requirements
        stored = json.loads(json.dumps(__import__("dataclasses").asdict(details)))
        self.assertEqual(ModDetails.fromJson(stored), details)

    def test_query_errors(self):
        open_, _ = opener({"errors": [{"message": "x" * 500}], "data": None})
        with self.assertRaises(NexusError) as raised:
            NexusGraphQL(open_).query("{}")
        self.assertEqual(len(str(raised.exception)), 303)
        open_, _ = opener(urllib.error.HTTPError("u", 503, "down", {}, io.BytesIO()))
        with self.assertRaisesRegex(NexusError, "503"):
            NexusGraphQL(open_).query("{}")
        open_, _ = opener(b"not json")
        with self.assertRaisesRegex(NexusError, "unreadable"):
            NexusGraphQL(open_).query("{}")
        open_, _ = opener({"errors": [{"message": "partial"}], "data": {"a": 1}})
        self.assertEqual(NexusGraphQL(open_).query("{}"), {"a": 1})

    def test_details_are_fetched_in_batches(self):
        page = {"data": {"legacyModsByDomain": {"nodes": [NODE, {"name": "no id"}]}}}
        open_, requests = opener(page, {"data": {"legacyModsByDomain": {"nodes": []}}})
        found = NexusGraphQL(open_).details([3, 1, 2, 2, 0], chunk=2)
        self.assertEqual(list(found), [13130])
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            body(requests[0])["variables"]["ids"],
            [{"gameDomain": "witcher3", "modId": 1}, {"gameDomain": "witcher3", "modId": 2}],
        )
        self.assertEqual(requests[0].get_header("Content-type"), "application/json")
        self.assertIn("nexusRequirements", body(requests[0])["query"])

    def test_search_filters(self):
        result = {"data": {"mods": {"totalCount": 7, "nodes": [NODE]}}}
        open_, requests = opener(result, result, result)
        graphql = NexusGraphQL(open_)
        total, mods = graphql.search("friendly hud", "relevance", 5, 10, REMASTERED_RELEASE)
        self.assertEqual((total, [m.modId for m in mods]), (7, [13130]))
        variables = body(requests[0])["variables"]
        self.assertEqual(variables["filter"]["nameStemmed"], [{"value": "friendly hud", "op": "MATCHES"}])
        self.assertEqual(variables["filter"]["updatedAt"], [{"value": str(REMASTERED_RELEASE), "op": "GTE"}])
        self.assertEqual(
            (variables["sort"], variables["count"], variables["offset"]),
            ([{"relevance": {"direction": "DESC"}}], 5, 10),
        )
        graphql.search("", "relevance")
        variables = body(requests[1])["variables"]
        self.assertNotIn("nameStemmed", variables["filter"])
        self.assertEqual(variables["sort"], [{"endorsements": {"direction": "DESC"}}])
        graphql.search("x", "bogus")
        self.assertEqual(body(requests[2])["variables"]["sort"], [{"relevance": {"direction": "DESC"}}])


class SuggestionTests(unittest.TestCase):
    def test_search_terms(self):
        self.assertEqual(searchTerms("(Classic Edition) Friendly HUD v1.2"), "Friendly HUD")
        self.assertEqual(searchTerms("ModLiveGraphicsPreview - Remastered 2.0"), "Live Graphics Preview")
        self.assertEqual(searchTerms("Complete Animations Redux (2026)"), "Animations Redux")
        self.assertEqual(searchTerms("v1.0"), "")

    def graphql(self, *pages):
        graphql = Mock()
        graphql.search.side_effect = [(len(page), page) for page in pages]
        return graphql

    def test_suggestions(self):
        port = ModDetails(2, "(Remastered) Friendly HUD", updated=AFTER)
        graphql = self.graphql(
            [
                ModDetails(1, "Friendly HUD", updated=AFTER),  # the mod itself
                ModDetails(3, "Friendly HUD - French translation", updated=AFTER),
                ModDetails(4, "Friendly HUD Old", updated=BEFORE),
                ModDetails(5, "Something Else Entirely", updated=AFTER),
                port,
            ]
        )
        self.assertEqual(remasteredSuggestions(graphql, "(Classic) Friendly HUD", 1), [port])
        graphql.search.assert_called_once_with("Friendly HUD", "relevance", 15, 0, REMASTERED_RELEASE)
        self.assertEqual(remasteredSuggestions(self.graphql([]), "v2", 1), [])

    def test_long_names_are_retried_with_their_two_most_distinctive_words(self):
        found = ModDetails(9, "Thousand Flowers Retexture", updated=AFTER)
        graphql = self.graphql([], [found])
        self.assertEqual(remasteredSuggestions(graphql, "Armor of a Thousand Flowers Recolor", 7), [found])
        self.assertEqual(graphql.search.call_args_list[1].args[0], "Thousand Flowers")

    def test_translations_are_suggested_for_translations(self):
        other = ModDetails(3, "Friendly HUD - French translation (Remastered)", updated=AFTER)
        self.assertEqual(remasteredSuggestions(self.graphql([other]), "Friendly HUD - French translation", 1), [other])


class CacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = temporary.name

    def test_round_trip_and_ages(self):
        cache = DetailsCache(self.folder)
        now = int(time.time())
        cache.update(
            {
                1: ModDetails(1, "A", fetched=now, schema=DETAILS_SCHEMA),
                2: ModDetails(2, "B", fetched=now - 100000, schema=DETAILS_SCHEMA),
                4: ModDetails(4, "D", fetched=now),  # stored before requirements were kept
            }
        )
        cache.save()
        again = DetailsCache(self.folder)
        self.assertEqual(again.get(1), ModDetails(1, "A", fetched=now, schema=DETAILS_SCHEMA))
        self.assertIsNone(again.get(0))
        self.assertEqual(again.missing([1, 2, 3, 4, 0]), [2, 3, 4])
        Path(self.folder, "nexus_details.json").write_text("{broken")
        self.assertEqual(DetailsCache(self.folder).mods, {})

    def test_images(self):
        cache = DetailsCache(self.folder)
        url = NODE["thumbnailUrl"]
        self.assertEqual(Path(cache.imagePath(url)).name, "a b.jpg")
        self.assertEqual(cache.imagePath(""), "")
        details = ModDetails.fromGraphQL(NODE)
        self.assertEqual(cache.image(details), "")
        open_, requests = opener(b"image bytes")
        path = cache.downloadImage(url, open_)
        self.assertEqual(Path(path).read_bytes(), b"image bytes")
        self.assertEqual(cache.downloadImage(url, open_), path)
        self.assertEqual(len(requests), 1)
        self.assertEqual(cache.image(details), path)
        self.assertEqual(cache.image(None), "")
        open_, _ = opener(OSError("offline"))
        with self.assertRaises(NexusError):
            cache.downloadImage("https://x/other.png", open_)
        self.assertFalse(os.path.exists(cache.imagePath("https://x/other.png")))


class HealthRemasteredTests(unittest.TestCase):
    def test_outdated_mods_are_flagged_for_remastered_only(self):
        old = Mod(nexus=NexusInfo(5))
        old._name = "Old"
        new = Mod(nexus=NexusInfo(6))
        new._name = "New"
        model = SimpleNamespace(all=lambda: [old, new, Mod()])
        details = {5: ModDetails(5, updated=BEFORE), 6: ModDetails(6, updated=AFTER)}.get
        config = SimpleNamespace(mods=None, dlc=None, menu=None, gameversion="re")
        remastered = lambda: [i for i in checkHealth(model, config, details=details) if "Remastered" in i.problem]  # noqa: E731
        issues = remastered()
        self.assertEqual([(i.severity, i.mod, i.problem, i.url) for i in issues],
                         [(WARNING, "Old", "Not updated since Remastered", "https://www.nexusmods.com/witcher3/mods/5")])  # fmt: skip
        config.gameversion = "ng"
        self.assertEqual(remastered(), [])


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = temporary.name
        self.config = SimpleNamespace(gameversion="re")
        config_patch = patch("src.gui.main_widget.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.widget = Mock()
        self.widget.nexusDetails = DetailsCache(self.folder)
        self.widget.detailsWorker = None
        linked = Mod(nexus=NexusInfo(13130))
        linked._name = "HUD"
        plain = Mod()
        plain._name = "Plain"
        self.mods = {"HUD": linked, "Plain": plain}
        self.widget.model = SimpleNamespace(modList=self.mods, all=lambda: self.mods.values(), write=Mock())
        self.widget.modDetails = lambda mod: CustomMainWidget.modDetails(self.widget, mod)
        self.widget.linkedModIds = lambda: CustomMainWidget.linkedModIds(self.widget)

    def outputs(self):
        return "\n".join(str(call.args[0]) for call in self.widget.output.call_args_list)

    def test_category_and_preview(self):
        self.widget.nexusDetails.update({13130: ModDetails.fromGraphQL(NODE)})
        self.assertEqual(CustomMainWidget.modCategory(self.widget, self.mods["HUD"]), "User Interface")
        self.assertEqual(CustomMainWidget.modCategory(self.widget, self.mods["Plain"]), "")
        self.widget.getSelectedMods.return_value = ["HUD"]
        CustomMainWidget.updatePreview(self.widget)
        mod, details, image, remastered, sections = self.widget.preview.showMod.call_args.args
        self.assertEqual((mod.name, details.modId, image, remastered), ("HUD", 13130, "", True))
        self.assertIs(sections, self.widget.previewSections.return_value)
        self.widget.getSelectedMods.return_value = []
        CustomMainWidget.updatePreview(self.widget)
        self.assertEqual(self.widget.preview.showMod.call_args.args[:2], (None, None))

    def test_refresh_fetches_details_and_pictures(self):
        found = {13130: ModDetails.fromGraphQL(NODE)}
        with patch("src.gui.main_widget.NexusWorker") as worker, patch("src.gui.main_widget.NexusGraphQL") as graphql:
            CustomMainWidget.refreshNexusDetails(self.widget)
            work = worker.call_args.args[0]
            graphql.return_value.details.return_value = found
            with patch.object(DetailsCache, "downloadImage", side_effect=NexusError("offline")) as download:
                self.assertEqual(work(Mock()), found)
            graphql.return_value.details.assert_called_once_with([13130])
            download.assert_called_once_with(NODE["thumbnailUrl"])
            worker.return_value.start.assert_called_once()
            self.assertIn("Loading Nexus details for 1 mod", self.outputs())

            worker.reset_mock()
            self.widget.nexusDetails.update({13130: ModDetails(13130, fetched=int(time.time()))})
            CustomMainWidget.refreshNexusDetails(self.widget, silent=True, onlyMissing=True)
            worker.assert_not_called()

            self.widget.detailsWorker = Mock()
            self.widget.detailsWorker.isRunning.return_value = True
            CustomMainWidget.refreshNexusDetails(self.widget)
            worker.assert_not_called()

    def test_fetched_details_are_saved_and_shown(self):
        CustomMainWidget.detailsFetched(self.widget, {13130: ModDetails.fromGraphQL(NODE)}, False)
        self.assertTrue(Path(self.folder, "nexus_details.json").exists())
        self.widget.refreshList.assert_called_once()
        self.widget.updatePreview.assert_called_once()
        self.assertIn("Loaded Nexus details for 1 mod", self.outputs())

    def test_remastered_report(self):
        mods = {
            5: SimpleNamespace(name="Old Mod", nexus=NexusInfo(5)),
            6: SimpleNamespace(name="Gone", nexus=NexusInfo(6)),
        }
        port = ModDetails(9, "Old Mod (Remastered)", updated=AFTER)
        found = {5: ModDetails(5, "Old Mod", updated=BEFORE), 7: ModDetails(7, "Fine", updated=AFTER)}
        with patch("src.gui.main_widget.HealthDialog") as dialog:
            CustomMainWidget.remasteredChecked(self.widget, mods, found, {5: [port]})
        issues = dialog.call_args.args[1]
        self.assertEqual(
            [(i.mod, i.problem, i.url) for i in issues],
            [("Old Mod", "Not updated since Remastered", port.url), ("Gone", "Not found on Nexus", NexusInfo(6).url)],
        )
        self.assertIn("Possible Remastered versions: Old Mod (Remastered) (9)", issues[0].detail)
        self.assertEqual(dialog.call_args.kwargs["title"], "Remastered Versions")
        self.assertIn("1 of 2 mods hasn't been updated since Remastered", self.outputs())
        self.widget.detailsFetched.assert_called_once_with(found, True)

    def test_check_remastered_work(self):
        with (
            patch("src.gui.main_widget.NexusWorker") as worker,
            patch("src.gui.main_widget.NexusGraphQL") as graphql,
            patch("src.gui.main_widget.remasteredSuggestions", return_value=["s"]) as suggest,
        ):
            CustomMainWidget.checkRemastered(self.widget)
            work = worker.call_args.args[0]
            graphql.return_value.details.return_value = {13130: ModDetails(13130, "HUD", updated=BEFORE)}
            found, suggestions = work(Mock())
        suggest.assert_called_once_with(graphql.return_value, "HUD", 13130)
        self.assertEqual(suggestions, {13130: ["s"]})

    def test_preview_widget(self):
        preview = ModPreview()
        preview.resize(320, 500)
        preview.showMod(None, None, "")
        self.assertIn("Select a mod", preview.title.text())
        mod = self.mods["HUD"]
        preview.showMod(mod, None, "")
        self.assertIn("haven't been loaded", preview.summary.text())
        self.assertIn("mods/13130", preview.link.text())
        preview.showMod(self.mods["Plain"], None, "")
        self.assertIn("Not linked to Nexus", preview.summary.text())
        image = Path(self.folder, "pic.png")
        pixmap = QPixmap(1200, 900)
        pixmap.fill(QColor("red"))
        pixmap.save(str(image))
        outdated = ModDetails.fromGraphQL({**NODE, "updatedAt": "2025-01-01T00:00:00Z"})
        preview.showMod(mod, outdated, str(image), remastered=True)
        self.assertEqual(preview.title.text(), "(Remastered) Friendly HUD")
        self.assertEqual(preview.meta.text(), "User Interface  ·  wghost81  ·  v0.9.3R  ·  updated 2025-01-01")
        self.assertIn("Not updated on Nexus since Remastered", preview.warning.text())
        self.assertLessEqual(preview.image.height(), IMAGE_HEIGHT)
        self.assertFalse(preview.image.pixmap().isNull())
        preview.showMod(mod, outdated, str(image), remastered=False)
        self.assertEqual(preview.warning.text(), "")


if __name__ == "__main__":
    unittest.main()
