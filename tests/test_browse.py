import io
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.nexus import ModFiles, NexusClient, NexusError, NexusFile, encodeUrl
from src.core.nexusdetails import REMASTERED_RELEASE, DetailsCache, ModDetails
from src.core.nxm import NxmLink
from src.gui.browse_widget import PAGE_SIZE, BrowseWidget
from src.gui.main_widget import CustomMainWidget


def details(modId, name="Mod", updated=REMASTERED_RELEASE + 1, **extra):
    return ModDetails(modId, name, category="Armour", author="me", version="1.0", updated=updated, **extra)


class EncodeTests(unittest.TestCase):
    def test_download_links_are_encoded_once(self):
        self.assertEqual(
            encodeUrl("https://cdn/952/8732/Cat Hood Fix-8732.rar?expires=1&md5=a-b_c&user_id=3"),
            "https://cdn/952/8732/Cat%20Hood%20Fix-8732.rar?expires=1&md5=a-b_c&user_id=3",
        )
        self.assertEqual(encodeUrl("https://x/a%20b.zip?x=1%2B2"), "https://x/a%20b.zip?x=1%2B2")
        self.assertEqual(encodeUrl("https://x/(Remastered) Mod.7z"), "https://x/(Remastered)%20Mod.7z")

    def test_downloads_request_the_encoded_link(self):
        requested = []

        class Response(io.BytesIO):
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        def opener(request, timeout):
            requested.append(request.full_url)
            return Response(b"archive")

        with tempfile.TemporaryDirectory() as directory:
            NexusClient("key", opener=opener).download(
                "https://cdn/Cat Hood Fix.rar?a=1", directory, "Cat Hood Fix.rar"
            )
        self.assertEqual(requested, ["https://cdn/Cat%20Hood%20Fix.rar?a=1"])


class BrowseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = DetailsCache(temporary.name)
        self.graphql = Mock()
        self.installed = {2}
        self.browse = BrowseWidget(None, self.cache, lambda: self.installed, True, lambda: self.graphql)
        worker = patch("src.gui.browse_widget.NexusWorker")
        self.worker = worker.start()
        self.addCleanup(worker.stop)
        self.worker.return_value.isRunning.return_value = False

    def rows(self):
        tree = self.browse.tree
        return [[tree.topLevelItem(i).text(c) for c in range(8)] for i in range(tree.topLevelItemCount())]

    def test_search_uses_the_controls(self):
        self.browse.searchBox.setText("  cat hood ")
        self.browse.sortBox.setCurrentIndex(2)  # most downloaded, which also starts a search
        self.worker.reset_mock()
        self.browse.search()
        work = self.worker.call_args.args[0]
        self.graphql.search.return_value = (0, [])
        work(Mock())
        self.graphql.search.assert_called_once_with("cat hood", "downloads", PAGE_SIZE, 0, REMASTERED_RELEASE)
        self.browse.remasteredBox.setChecked(False)
        work = self.worker.call_args.args[0]
        work(Mock())
        self.assertEqual(self.graphql.search.call_args.args[4], 0)
        self.worker.return_value.start.assert_called()

    def test_results_paging_and_installed_column(self):
        self.browse.showResults(30, [details(1, "First", endorsements=5, downloads=9), details(2, "Second")])
        self.assertEqual(
            self.rows(),
            [
                ["First", "Armour", "me", "1.0", "5", "9", "2026-09-29", ""],
                ["Second", "Armour", "me", "1.0", "0", "0", "2026-09-29", "Yes"],
            ],
        )
        self.assertEqual(self.browse.status.text(), "2 of 30 mods")
        self.assertTrue(self.browse.moreButton.isEnabled())
        self.assertEqual(self.browse.selected().modId, 1)
        self.worker.reset_mock()
        self.browse.loadMore()
        self.browse.query = ("x", "relevance", 0)
        work = self.worker.call_args.args[0]
        self.graphql.search.return_value = (30, [])
        work(Mock())
        self.assertEqual(self.graphql.search.call_args.args[3], 2)
        self.browse.showResults(30, [details(2, "Second"), details(3, "Third")])
        self.assertEqual([row[0] for row in self.rows()], ["First", "Second", "Third"])
        self.installed = {1}
        self.browse.markInstalled()
        self.assertEqual([row[7] for row in self.rows()], ["Yes", "", ""])
        self.browse.showResults(0, [])
        self.assertEqual(self.browse.status.text(), "No mods found.")

    def test_while_searching_nothing_else_starts(self):
        self.worker.return_value.isRunning.return_value = True
        self.browse.worker = self.worker.return_value
        self.worker.reset_mock()
        self.browse.fetch(0)
        self.worker.assert_not_called()

    def test_selection_preview_picture_and_buttons(self):
        with patch.object(self.browse.preview, "showDetails") as shown:
            self.browse.showResults(1, [details(1, "First", thumbnailUrl="https://x/t.png")])
        shown.assert_called_with(self.browse.results[0], "", True)
        work = self.worker.call_args.args[0]
        with patch.object(self.cache, "downloadImage", return_value="t.png") as download:
            work(Mock())
        download.assert_called_once_with("https://x/t.png")
        self.assertTrue(self.browse.installButton.isEnabled())
        received = []
        self.browse.installRequested.connect(received.append)
        self.browse.requestInstall()
        self.assertEqual([d.modId for d in received], [1])
        with patch("src.gui.browse_widget.openUrl") as opened:
            self.browse.openPage()
        opened.assert_called_once_with("https://www.nexusmods.com/witcher3/mods/1")
        self.browse.tree.clearSelection()
        self.assertFalse(self.browse.installButton.isEnabled())
        self.browse.requestInstall()
        self.assertEqual(len(received), 1)

    def test_preview_shows_search_results(self):
        self.browse.preview.showDetails(details(1, "Old", summary="About it", updated=1), "", True)
        self.assertEqual(self.browse.preview.title.text(), "Old")
        self.assertEqual(self.browse.preview.summary.text(), "About it")
        self.assertIn("since Remastered", self.browse.preview.warning.text())


def file(fileId, category="MAIN", primary=False, uploaded=1, name="Mod"):
    return NexusFile(fileId, name, "1.0", category, primary, uploaded, f"f{fileId}.zip", 10)


class InstallFromNexusTests(unittest.TestCase):
    def setUp(self):
        self.widget = Mock()
        self.widget.nexusBusy.return_value = False
        self.client = self.widget.nexusClient.return_value
        self.config = SimpleNamespace(archives="C:/archives")
        config_patch = patch("src.gui.main_widget.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)

    def test_files_are_fetched_then_chosen(self):
        mod = details(8732, "Cat Hood Fix")
        CustomMainWidget.installFromNexus(self.widget, mod)
        work, done = self.widget.runNexus.call_args.args
        work(Mock())
        self.client.modFiles.assert_called_once_with(8732)
        done("files")
        self.widget.chooseNexusFile.assert_called_once_with(self.client, mod, "files")

    def test_choosing(self):
        mod = details(5, "Mod")
        CustomMainWidget.chooseNexusFile(self.widget, self.client, mod, ModFiles({1: file(1, "OLD_VERSION")}))
        self.assertIn("no files to download", self.widget.output.call_args.args[0])

        CustomMainWidget.chooseNexusFile(self.widget, self.client, mod, ModFiles({7: file(7)}))
        self.widget.downloadNxm.assert_called_once_with(self.client, NxmLink("witcher3", 5, 7))

        files = ModFiles(
            {
                1: file(1, "OPTIONAL", uploaded=9),
                2: file(2, uploaded=1),
                3: file(3, primary=True),
                4: file(4, uploaded=5),
            }
        )
        with patch("src.gui.main_widget.QInputDialog.getItem", side_effect=lambda *a: (a[3][1], True)) as asked:
            CustomMainWidget.chooseNexusFile(self.widget, self.client, mod, files)
        order = [label for label in asked.call_args.args[3]]
        self.assertEqual(
            order,
            [f"{f.label()}  ({f.fileName})" for f in (files.files[3], files.files[4], files.files[2], files.files[1])],
        )
        self.assertEqual(self.widget.downloadNxm.call_args.args[1], NxmLink("witcher3", 5, 4))
        self.widget.downloadNxm.reset_mock()
        with patch("src.gui.main_widget.QInputDialog.getItem", return_value=("", False)):
            CustomMainWidget.chooseNexusFile(self.widget, self.client, mod, files)
        self.widget.downloadNxm.assert_not_called()

    def test_free_account_gets_the_website(self):
        CustomMainWidget.downloadNxm(self.widget, self.client, NxmLink("witcher3", 5, 7))
        work, _ = self.widget.runNexus.call_args.args
        self.client.modFiles.return_value = ModFiles({7: file(7)})
        self.client.downloadLinks.side_effect = NexusError("premium only", 403)
        self.assertEqual(work(Mock()), (NxmLink("witcher3", 5, 7), file(7), ""))
        CustomMainWidget.downloadNxm(self.widget, self.client, NxmLink("witcher3", 5, 7, key="k", expires="1"))
        work, _ = self.widget.runNexus.call_args.args
        with self.assertRaises(NexusError):
            work(Mock())
        CustomMainWidget.nxmDownloaded(self.widget, (NxmLink("witcher3", 5, 7), file(7, name="Cat"), ""))
        self.widget.openNexusDownload.assert_called_once_with(5, 7, "Cat")
        self.widget.installModFiles.assert_not_called()

    def test_installing_updates_the_browse_tab(self):
        self.widget.model = SimpleNamespace(all=lambda: [], write=Mock())
        with patch("src.gui.main_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            CustomMainWidget.nxmDownloaded(self.widget, (NxmLink("witcher3", 5, 7), file(7), "C:/archives/f7.zip"))
        self.widget.installModFiles.assert_called_once_with(["C:/archives/f7.zip"])
        self.widget.browse.markInstalled.assert_called_once()


if __name__ == "__main__":
    unittest.main()
