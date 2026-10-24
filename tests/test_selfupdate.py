'''Tests for updating the manager itself from GitHub releases'''

import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from src.core.nexus import NexusError
from src.core.selfupdate import (
    CHECK_INTERVAL,
    ManagerRelease,
    UpdateCheckResult,
    checkForUpdate,
    currentVersion,
    downloadInstaller,
    dueForCheck,
    fetchLatest,
    installerCommand,
    isNewer,
    parseRelease,
    recordCheck,
)
from src.gui.main_widget import CustomMainWidget
from src.gui.manager_update_dialog import INSTALL, LATER, OPEN_PAGE, ManagerUpdateDialog

INSTALLER = b"MZ installer bytes" * 1000
HASH = hashlib.sha256(INSTALLER).hexdigest()


def payload(version="0.3", withAsset=True, notes=None):
    body = f"Notes\n\nSHA-256 of the installer: `{HASH}`" if notes is None else notes
    return {
        "tag_name": f"casualfork-{version}",
        "html_url": f"https://github.com/mooonski/W3MM-casualfork/releases/tag/casualfork-{version}",
        "body": body,
        "published_at": "2026-10-24T12:00:00Z",
        "assets": [
            {"name": "Source.zip", "browser_download_url": "https://x/src.zip", "size": 1},
            {
                "name": f"W3MM-casualfork-{version}-setup.exe",
                "browser_download_url": f"https://x/W3MM-casualfork-{version}-setup.exe",
                "size": len(INSTALLER),
            },
        ]
        if withAsset
        else [],
    }


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, headers=None):
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def opener(data: bytes, headers=None, error=None):
    def open_(request, timeout=0):
        if error:
            raise error
        return FakeResponse(data, headers)

    return open_


class VersionTests(unittest.TestCase):
    def test_current_version_is_the_number_in_fork_version(self):
        with patch("src.core.selfupdate.FORK_VERSION", "casual fork 0.2"):
            self.assertEqual(currentVersion(), "0.2")

    def test_comparison(self):
        self.assertTrue(isNewer("0.3", "0.2"))
        self.assertTrue(isNewer("0.10", "0.9"))
        self.assertTrue(isNewer("1.0", "0.99.9"))
        self.assertFalse(isNewer("0.2", "0.2"))
        self.assertFalse(isNewer("0.2.0", "0.2"))
        self.assertFalse(isNewer("0.1", "0.2"))


class ReleaseTests(unittest.TestCase):
    def test_parse_release_picks_the_installer_and_hash(self):
        release = parseRelease(payload())
        self.assertEqual(release.version, "0.3")
        self.assertEqual(release.tag, "casualfork-0.3")
        self.assertEqual(release.installerName, "W3MM-casualfork-0.3-setup.exe")
        self.assertEqual(release.installerSize, len(INSTALLER))
        self.assertEqual(release.sha256, HASH)
        self.assertTrue(release.installable)
        self.assertFalse(parseRelease(payload(withAsset=False)).installable)
        self.assertEqual(parseRelease(payload(notes="no hash here")).sha256, "")
        with self.assertRaises(NexusError):
            parseRelease({})

    def test_fetch_latest_and_errors(self):
        release = fetchLatest(opener(json.dumps(payload()).encode()))
        self.assertEqual(release.version, "0.3")
        for code, text in ((404, "No release"), (403, "rate-limiting"), (500, "HTTP 500")):
            error = urllib.error.HTTPError("u", code, "x", {}, None)  # type: ignore[arg-type]
            with self.assertRaisesRegex(NexusError, text):
                fetchLatest(opener(b"", error=error))
        with self.assertRaisesRegex(NexusError, "Could not check"):
            fetchLatest(opener(b"", error=urllib.error.URLError("offline")))
        with self.assertRaisesRegex(NexusError, "Could not check"):
            fetchLatest(opener(b"not json"))

    def test_check_for_update(self):
        with patch("src.core.selfupdate.FORK_VERSION", "casual fork 0.2"):
            result = checkForUpdate(opener(json.dumps(payload("0.3")).encode()))
            self.assertTrue(result.available)
            self.assertEqual(result.errors, [])
            result = checkForUpdate(opener(json.dumps(payload("0.2")).encode()))
            self.assertFalse(result.available)
            result = checkForUpdate(opener(b"", error=urllib.error.URLError("offline")))
            self.assertFalse(result.available)
            self.assertIsNone(result.latest)
            self.assertIn("offline", result.errors[0])


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.release = parseRelease(payload())

    def test_download_checks_hash_and_reuses_a_good_file(self):
        progress = Mock()
        path = downloadInstaller(self.release, str(self.folder), progress=progress, opener=opener(INSTALLER))
        self.assertEqual(Path(path).name, "W3MM-casualfork-0.3-setup.exe")
        self.assertEqual(Path(path).read_bytes(), INSTALLER)
        self.assertEqual(progress.call_args.args, (len(INSTALLER), len(INSTALLER)))
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["W3MM-casualfork-0.3-setup.exe"])
        # a second call doesn't download again
        again = downloadInstaller(self.release, str(self.folder), opener=opener(b"different"))
        self.assertEqual(again, path)

    def test_wrong_hash_or_short_download_is_discarded(self):
        with self.assertRaisesRegex(NexusError, "doesn't match"):
            downloadInstaller(self.release, str(self.folder), opener=opener(INSTALLER[:-1] + b"X"))
        self.assertEqual(list(self.folder.iterdir()), [])
        with self.assertRaisesRegex(NexusError, "incomplete"):
            downloadInstaller(
                self.release, str(self.folder), opener=opener(INSTALLER[:10], {"Content-Length": str(len(INSTALLER))})
            )
        self.assertEqual(list(self.folder.iterdir()), [])
        with self.assertRaisesRegex(NexusError, "no Windows installer"):
            downloadInstaller(parseRelease(payload(withAsset=False)), str(self.folder))

    def test_installer_command_is_silent_and_relaunches(self):
        command = installerCommand(r"C:\x\setup.exe")
        self.assertEqual(command[0], r"C:\x\setup.exe")
        self.assertIn("/SILENT", command)
        self.assertIn("/RELAUNCH=1", command)
        self.assertIn("/CLOSEAPPLICATIONS", command)


class ScheduleTests(unittest.TestCase):
    def test_due_once_a_day_unless_turned_off(self):
        values = {}
        config = SimpleNamespace(
            get=lambda section, option, default=None: values.get(option, default),
            set=lambda section, option, value: values.__setitem__(option, value),
        )
        start = 10 * CHECK_INTERVAL
        self.assertTrue(dueForCheck(config, now=start))
        recordCheck(config, now=start)
        self.assertFalse(dueForCheck(config, now=start + CHECK_INTERVAL - 1))
        self.assertTrue(dueForCheck(config, now=start + CHECK_INTERVAL))
        values["checkmanagerupdates"] = "0"
        self.assertFalse(dueForCheck(config, now=1e9))
        values["managerupdatecheck"] = "garbage"
        values["checkmanagerupdates"] = "1"
        self.assertTrue(dueForCheck(config, now=5.0 * CHECK_INTERVAL))


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def widget(self):
        widget = Mock()
        widget.nexusWorker = None
        # runNexus is synchronous here: work(progress) then done(result)
        widget.runNexus = lambda work, done: done(work(lambda a, b: None))
        widget.nexusBusy.return_value = False
        # the methods under test call each other, so they must be the real ones
        for name in ("managerChecked", "installManagerUpdate", "managerInstallerReady"):
            setattr(widget, name, lambda *args, name=name: getattr(CustomMainWidget, name)(widget, *args))
        return widget

    def config(self, **values):
        store = dict(values)
        return SimpleNamespace(
            archives="D:/downloads",
            get=lambda section, option, default=None: store.get(option, default),
            set=lambda section, option, value: store.__setitem__(option, value),
            store=store,
        )

    def test_manual_check_when_up_to_date_or_offline(self):
        widget = self.widget()
        result = UpdateCheckResult("0.2", ManagerRelease("0.2", "casualfork-0.2", "u"))
        with (
            patch("src.gui.main_widget.data.config", self.config()),
            patch("src.gui.main_widget.checkManagerRelease", return_value=result),
        ):
            CustomMainWidget.checkManagerUpdate(widget, automatic=False)
        self.assertIn("up to date", widget.output.call_args.args[0])
        widget = self.widget()
        result = UpdateCheckResult("0.2", None, ["Could not check: offline"])
        with (
            patch("src.gui.main_widget.data.config", self.config()),
            patch("src.gui.main_widget.checkManagerRelease", return_value=result),
        ):
            CustomMainWidget.checkManagerUpdate(widget, automatic=False)
        self.assertIn("offline", widget.output.call_args.args[0])

    def test_automatic_check_is_silent_and_records_the_time(self):
        widget = self.widget()
        config = self.config()
        result = UpdateCheckResult("0.2", None, ["offline"])
        with (
            patch("src.gui.main_widget.data.config", config),
            patch("src.gui.main_widget.checkManagerRelease", return_value=result),
        ):
            CustomMainWidget.checkManagerUpdate(widget, automatic=True)
        widget.output.assert_not_called()
        self.assertIn("managerupdatecheck", config.store)

    def dialogRun(self, action, installable=True, autoCheck=False):
        widget = self.widget()
        config = self.config(checkmanagerupdates="1")
        release = parseRelease(payload("0.3"))
        result = UpdateCheckResult("0.2", release)
        with (
            patch("src.gui.main_widget.data.config", config),
            patch("src.gui.main_widget.checkManagerRelease", return_value=result),
            patch("src.gui.main_widget.isInstalledBuild", return_value=installable),
            patch("src.gui.main_widget.ManagerUpdateDialog") as dialog,
            patch("src.gui.main_widget.openUrl") as openUrl_,
            patch("src.gui.main_widget.downloadInstaller", return_value="D:/downloads/setup.exe") as download,
            patch("src.gui.main_widget.launchInstaller") as launch,
            patch("src.gui.main_widget.QTimer.singleShot", side_effect=lambda ms, slot: slot()),
        ):
            dialog.return_value.action = action
            dialog.return_value.autoCheck.isChecked.return_value = autoCheck
            CustomMainWidget.checkManagerUpdate(widget, automatic=True)
        self.assertEqual(dialog.call_args.args[1:], (release, "0.2", installable, True))
        return widget, config, openUrl_, download, launch

    def test_later_only_mentions_the_update(self):
        widget, config, openUrl_, download, launch = self.dialogRun(LATER, autoCheck=False)
        self.assertEqual(config.store["checkmanagerupdates"], "0")
        self.assertIn("0.3 is available", widget.output.call_args.args[0])
        openUrl_.assert_not_called()
        download.assert_not_called()

    def test_open_page(self):
        widget, config, openUrl_, download, launch = self.dialogRun(OPEN_PAGE, autoCheck=True)
        self.assertEqual(config.store["checkmanagerupdates"], "1")
        openUrl_.assert_called_once_with("https://github.com/mooonski/W3MM-casualfork/releases/tag/casualfork-0.3")

    def test_install_downloads_launches_and_closes(self):
        widget, config, openUrl_, download, launch = self.dialogRun(INSTALL)
        self.assertEqual(download.call_args.args[1], "D:/downloads")
        launch.assert_called_once_with("D:/downloads/setup.exe")
        widget.mainWindow.close.assert_called_once()

    def test_source_checkout_gets_no_install_button(self):
        release = parseRelease(payload("0.3"))
        dialog = ManagerUpdateDialog(None, release, "0.2", installable=False, autoCheck=True)
        self.assertFalse(hasattr(dialog, "installButton"))
        self.assertIn("runs from source", dialog.findChildren(type(dialog.autoCheck))[0].parent().children()[3].text())
        dialog = ManagerUpdateDialog(None, release, "0.2", installable=True, autoCheck=False)
        self.assertTrue(dialog.installButton.isDefault())
        dialog.choose(INSTALL)
        self.assertEqual(dialog.action, INSTALL)
        self.assertEqual(dialog.result(), ManagerUpdateDialog.DialogCode.Accepted)


if __name__ == "__main__":
    unittest.main()
