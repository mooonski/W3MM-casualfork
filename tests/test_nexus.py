import configparser
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.core.installer import Installer
from src.core.model import Model
from src.core.nexus import (
    FAILED,
    UP_TO_DATE,
    UPDATE_AVAILABLE,
    ModFiles,
    NexusClient,
    NexusError,
    NexusInfo,
    changelogLines,
    changesBetween,
    checkForUpdate,
    parseArchiveName,
    parseModReference,
    protectSecret,
    uniquePath,
    unprotectSecret,
)
from src.core.updater import ModUpdater


def apiFile(fileId, name, version, category, uploaded, fileName='', primary=False, changelog=''):
    return {
        'changelog_html': changelog or None,
        'file_id': fileId,
        'name': name,
        'version': version,
        'category_name': category,
        'uploaded_timestamp': uploaded,
        'file_name': fileName or f"{name} {fileId}.zip",
        'size_in_bytes': 100,
        'is_primary': primary,
    }


def modFiles(files, updates=()):
    return ModFiles.fromApi(
        {'files': files, 'file_updates': [{'old_file_id': a, 'new_file_id': b} for a, b in updates]}
    )


class ArchiveNameTests(unittest.TestCase):
    def test_current_nexus_download_names(self):
        self.assertEqual(
            parseArchiveName("ModLiveGraphicsPreview 2.1 13169 2 2026-10-08T12-42Z q0xDJBnIy.zip"),
            ("ModLiveGraphicsPreview 2.1", 13169, "2"),
        )
        self.assertEqual(
            parseArchiveName("C:/x/Extra Random Gwent Card 12969 5.01.00 2026-10-08T17-13Z sedaZoAdO.7z"),
            ("Extra Random Gwent Card", 12969, "5.01.00"),
        )

    def test_legacy_nexus_download_names(self):
        self.assertEqual(parseArchiveName("Friendly HUD-365-12-3-1600000000.zip"), ("Friendly HUD", 365, "12.3"))

    def test_other_names_are_ignored(self):
        for name in ("modExample.zip", "Some Mod 1.2.rar", "13169.zip"):
            with self.subTest(name=name):
                self.assertIsNone(parseArchiveName(name))

    def test_mod_reference(self):
        self.assertEqual(parseModReference(" 820 "), 820)
        self.assertEqual(parseModReference("https://www.nexusmods.com/witcher3/mods/820?tab=files"), 820)
        self.assertEqual(parseModReference("https://next.nexusmods.com/witcher3/mods/13169"), 13169)
        self.assertIsNone(parseModReference("https://www.nexusmods.com/skyrim/mods/820"))
        self.assertIsNone(parseModReference("abc"))


class UpdateResolutionTests(unittest.TestCase):
    def test_follows_update_chain_from_installed_file(self):
        files = modFiles(
            [
                apiFile(1, "Example", "1.0", "OLD_VERSION", 100),
                apiFile(2, "Example", "1.1", "OLD_VERSION", 200),
                apiFile(3, "Example", "1.2", "MAIN", 300, primary=True),
                apiFile(9, "Example Lite", "1.2", "OPTIONAL", 300),
            ],
            [(1, 2), (2, 3)],
        )
        check = checkForUpdate(NexusInfo(5, fileId=1), files, "Example", "2020-01-01 00:00:00")
        self.assertEqual(check.state, UPDATE_AVAILABLE)
        self.assertTrue(check.confident)
        self.assertEqual(check.installed.fileId, 1)
        self.assertEqual(check.target.fileId, 3)
        self.assertEqual({f.fileId for f in check.candidates}, {3, 9})

        current = checkForUpdate(NexusInfo(5, fileId=3), files, "Example", "")
        self.assertEqual(current.state, UP_TO_DATE)
        self.assertTrue(current.confident)

    def test_chain_cycles_and_missing_files_are_tolerated(self):
        files = modFiles(
            [apiFile(1, "A", "1", "OLD_VERSION", 1), apiFile(2, "A", "2", "MAIN", 2)], [(1, 2), (2, 7), (7, 1)]
        )
        self.assertEqual(files.latestInChain(1), 2)

    def test_unlinked_new_upload_needs_a_choice(self):
        files = modFiles([apiFile(1, "Example", "1.0", "OLD_VERSION", 100), apiFile(2, "Example", "2.0", "MAIN", 200)])
        check = checkForUpdate(NexusInfo(5, fileId=1), files, "Example", "")
        self.assertEqual(check.state, UPDATE_AVAILABLE)
        self.assertFalse(check.confident)
        self.assertTrue(check.needsChoice)
        self.assertEqual(check.target.fileId, 2)

    def test_installed_file_is_identified_by_archive_name(self):
        files = modFiles(
            [
                apiFile(1, "Example", "1.0", "OLD_VERSION", 100, fileName="Example 5 1.0 x.zip"),
                apiFile(2, "Example", "2.0", "MAIN", 200, fileName="Example 5 2.0 y.zip"),
            ],
            [(1, 2)],
        )
        check = checkForUpdate(NexusInfo(5, archive="example 5 1.0 X.ZIP"), files, "Example", "")
        self.assertTrue(check.confident)
        self.assertEqual((check.installed.fileId, check.target.fileId), (1, 2))

    def test_unknown_file_is_guessed_from_install_date(self):
        files = modFiles(
            [
                apiFile(1, "Cool Mod", "1.0", "OLD_VERSION", 1_600_000_000),
                apiFile(2, "Cool Mod", "2.0", "MAIN", 1_700_000_000),
                apiFile(3, "Cool Mod Patch For Something", "1.0", "OPTIONAL", 1_800_000_000),
            ]
        )
        before = checkForUpdate(NexusInfo(5), files, "Cool Mod", "2022-01-01 00:00:00")
        self.assertEqual(before.state, UPDATE_AVAILABLE)
        self.assertFalse(before.confident)
        self.assertEqual(before.target.fileId, 2)
        self.assertEqual(before.installed.fileId, 1)

        after = checkForUpdate(NexusInfo(5), files, "Cool Mod", "2024-01-01 00:00:00")
        self.assertEqual(after.state, UP_TO_DATE)
        self.assertFalse(after.confident)

    def test_no_current_files_is_an_error(self):
        files = modFiles([apiFile(1, "A", "1", "ARCHIVED", 1)])
        self.assertEqual(checkForUpdate(NexusInfo(5, fileId=1), files, "A", "").state, FAILED)


class ChangelogTests(unittest.TestCase):
    def files(self):
        return modFiles(
            [
                apiFile(1, 'Example', '1.0', 'OLD_VERSION', 100, changelog='First release'),
                apiFile(2, 'Example', '1.1', 'OLD_VERSION', 200, changelog='Fixed a crash<br>Faster loading'),
                apiFile(3, 'Example', '1.2', 'MAIN', 300, changelog='Remastered support\n- New menu'),
                apiFile(4, 'Example Lite', '1.2', 'OPTIONAL', 300, changelog='Remastered support'),
                apiFile(5, 'Unrelated Textures', '9.0', 'OPTIONAL', 250, changelog='Sharper textures'),
                apiFile(6, 'Example', '1.3', 'MAIN', 400),
            ],
            updates=[(1, 2), (2, 3)],
        )

    def test_lines_from_html(self):
        self.assertEqual(
            changelogLines('<p>Fixed &amp; improved</p><ul><li>One</li><li>- Two</li></ul>\n\n* Three'),
            ['Fixed & improved', 'One', 'Two', 'Three'],
        )
        self.assertEqual(changelogLines(''), [])

    def test_versions_after_installed_up_to_target_newest_first(self):
        files = self.files()
        changes = changesBetween(files, files.files[1], files.files[3])
        self.assertEqual(
            changes,
            [('1.2', ['Remastered support', 'New menu']), ('1.1', ['Fixed a crash', 'Faster loading'])],
        )

    def test_files_of_the_same_version_are_merged(self):
        files = self.files()
        changes = changesBetween(files, files.files[2], files.files[4])
        self.assertEqual(changes, [('1.2', ['Remastered support', 'New menu'])])

    def test_unknown_installed_file_shows_only_the_target(self):
        files = self.files()
        self.assertEqual(changesBetween(files, None, files.files[2]), [('1.1', ['Fixed a crash', 'Faster loading'])])

    def test_no_changelog_and_older_target(self):
        files = self.files()
        self.assertEqual(changesBetween(files, files.files[3], files.files[6]), [])
        self.assertEqual(changesBetween(files, files.files[3], files.files[1]), [])

    def test_check_keeps_the_files(self):
        files = self.files()
        check = checkForUpdate(NexusInfo(modId=7, fileId=2), files, 'Example', '')
        self.assertIs(check.files, files)


class SecretTests(unittest.TestCase):
    def test_round_trip(self):
        stored = protectSecret("my-key")
        self.assertNotIn("my-key", stored)
        self.assertEqual(unprotectSecret(stored), "my-key")
        self.assertEqual(unprotectSecret(""), "")
        self.assertEqual(unprotectSecret("dpapi:not-base64!"), "")


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, headers=None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ClientTests(unittest.TestCase):
    def test_files_request_and_headers(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            body = {'files': [apiFile(4, "A", "1", "MAIN", 1)], 'file_updates': []}
            return FakeResponse(json.dumps(body).encode(), {'X-RL-Hourly-Remaining': '99'})

        files = NexusClient("secret", opener=opener).modFiles(820)
        self.assertEqual(list(files.files), [4])
        self.assertTrue(requests[0].full_url.endswith("/v1/games/witcher3/mods/820/files.json"))
        self.assertEqual(requests[0].get_header("Apikey"), "secret")

    def test_errors_are_explained(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))

        with self.assertRaises(NexusError) as raised:
            NexusClient("bad", opener=opener).validate()
        self.assertEqual(raised.exception.status, 401)
        self.assertIn("API key", str(raised.exception))
        with self.assertRaises(NexusError):
            NexusClient("", opener=opener).validate()

    def test_download_never_overwrites_and_reuses_identical_file(self):
        with tempfile.TemporaryDirectory() as directory:
            client = NexusClient("key", opener=lambda request, timeout: FakeResponse(b"new archive"))
            Path(directory, "Mod.zip").write_bytes(b"something else")
            target = client.download("https://cdn/x", directory, "Mod.zip", expectedSize=len(b"new archive"))
            self.assertEqual(Path(target).name, "Mod (2).zip")
            self.assertEqual(Path(target).read_bytes(), b"new archive")
            self.assertEqual(Path(directory, "Mod.zip").read_bytes(), b"something else")
            again = client.download("https://cdn/x", directory, "Mod (2).zip", expectedSize=len(b"new archive"))
            self.assertEqual(again, target)
            self.assertEqual(sorted(os.listdir(directory)), ["Mod (2).zip", "Mod.zip"])

    def test_failed_download_leaves_nothing_behind(self):
        class Broken(FakeResponse):
            def read(self, *args):
                raise OSError("connection reset")

        with tempfile.TemporaryDirectory() as directory:
            client = NexusClient("key", opener=lambda request, timeout: Broken(b""))
            with self.assertRaises(NexusError):
                client.download("https://cdn/x", directory, "Mod.zip")
            self.assertEqual(os.listdir(directory), [])

    def test_unique_path(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "a.zip.part").write_text("")
            self.assertEqual(Path(uniquePath(directory, "a.zip.part")).name, "a (2).zip.part")

    def test_download_reports_progress_to_the_end(self):
        body = b"x" * (600 * 1024)
        progress = []
        with tempfile.TemporaryDirectory() as directory:
            client = NexusClient(
                "key", opener=lambda request, timeout: FakeResponse(body, {"Content-Length": str(len(body))})
            )
            target = client.download("https://cdn/x", directory, "Big.7z", progress=lambda *p: progress.append(p))
            self.assertEqual(Path(target).stat().st_size, len(body))
        self.assertEqual(progress[-1], (len(body), len(body)))
        self.assertTrue(all(done <= total for done, total in progress))

    def test_short_or_cancelled_download_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            short = NexusClient("key", opener=lambda request, timeout: FakeResponse(b"12", {"Content-Length": "10"}))
            with self.assertRaisesRegex(NexusError, "incomplete"):
                short.download("https://cdn/x", directory, "Mod.zip")
            full = NexusClient("key", opener=lambda request, timeout: FakeResponse(b"12"))
            with self.assertRaisesRegex(NexusError, "cancelled"):
                full.download("https://cdn/x", directory, "Mod.zip", cancelled=lambda: True)
            self.assertEqual(os.listdir(directory), [])


class FakeConfig(SimpleNamespace):
    '''Just enough of Configuration for installs, with priorities kept in mods.settings'''

    def __init__(self, root: Path):
        game = root / "The Witcher 3"
        for directory in ("content", "Mods", "DLC", "bin/config/r4game/user_config_matrix/pc", "bin/config/base"):
            (game / directory).mkdir(parents=True)
        settings = root / "Documents"
        settings.mkdir()
        manager = root / "Manager"
        manager.mkdir()
        super().__init__(
            game=str(game),
            mods=str(game / "Mods"),
            dlc=str(game / "DLC"),
            menu=str(game / "bin/config/r4game/user_config_matrix/pc"),
            settings=str(settings),
            configuration=str(manager),
            backups=str(root / "Backups"),
            gameversion='re',
        )
        self.priority = configparser.ConfigParser()

    def getPriority(self, section):
        return self.priority.get(section, 'priority', fallback=None)

    def setPriority(self, section, value):
        if not self.priority.has_section(section):
            self.priority.add_section(section)
            self.priority.set(section, 'enabled', '1')
        self.priority.set(section, 'priority', value)

    def removePriority(self, section):
        self.priority.remove_section(section)
        self.write_priority()

    def write_priority(self):
        with open(os.path.join(self.settings, 'mods.settings'), 'w') as file:
            self.priority.write(file)

    def readPriority(self):
        self.priority = configparser.ConfigParser()
        self.priority.read(os.path.join(self.settings, 'mods.settings'))


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = FakeConfig(self.root)
        config_patch = patch("src.globals.data.config", self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.game = Path(self.config.game)
        self.menu = Path(self.config.menu)
        self.settings = Path(self.config.settings)
        (self.menu / "input.xml").write_text("<input/>", encoding="utf-16")
        self.model = Model(ignorelock=True)

    def package(self, name: str, version: str, menus=("example.xml",), options="OptionA=1", folder="modExample"):
        source = self.root / "packages" / name
        (source / folder / "content").mkdir(parents=True)
        (source / folder / "content" / "asset.txt").write_bytes(version.encode())
        menu = source / "bin/config/r4game/user_config_matrix/pc"
        menu.mkdir(parents=True)
        for filename in menus:
            (menu / filename).write_text(f"<{version}/>")
        (source / "settings.txt").write_text("[Example]\n" + options.replace(",", "\n") + "\n")
        return str(source)

    def installV1(self, enabled=True, priority=None):
        Installer(self.model).installMod(self.package("v1", "v1", menus=("example.xml", "old.xml")), name="Example")
        mod = self.model.get("Example")
        mod.nexus = NexusInfo(42, fileId=1, version="1.0", archive="v1.zip")
        if priority is not None:
            mod.priority = priority
            self.config.write_priority()
        if not enabled:
            mod.disable()
        self.model.write()
        # the user changes a setting in game
        for filename in ("user.settings", "dx12user.settings"):
            path = self.settings / filename
            path.write_text(path.read_text().replace("OptionA=1", "OptionA=7"))
        return mod

    def asset(self, folder="modExample"):
        return (self.game / "Mods" / folder / "content" / "asset.txt").read_text()

    def test_update_replaces_files_and_keeps_entry_state_and_settings(self):
        mod = self.installV1(priority=3)
        result = ModUpdater(self.model).update(
            mod,
            self.package("v2", "v2", options="OptionA=1,OptionB=2"),
            NexusInfo(42, 2, "2.0", "Example", 200, "v2.zip"),
        )
        self.assertTrue(result.success)
        self.assertEqual(list(self.model.list()), ["Example"])
        updated = Model(ignorelock=True).get("Example")
        self.assertEqual(updated.priority, "3")
        self.assertTrue(updated.enabled)
        self.assertEqual((updated.nexus.fileId, updated.nexus.version, updated.nexus.archive), (2, "2.0", "v2.zip"))
        self.assertEqual(self.asset(), "v2")
        self.assertEqual((self.menu / "example.xml").read_text(), "<v2/>")
        self.assertFalse((self.menu / "old.xml").exists())
        user = (self.settings / "user.settings").read_text()
        self.assertIn("OptionA=7", user)
        self.assertIn("OptionB=2", user)
        self.assertIn("OptionA=7", (self.settings / "dx12user.settings").read_text())
        self.config.readPriority()
        self.assertEqual(self.config.getPriority("modExample"), "3")
        backup = Path(result.backup)
        self.assertEqual((backup / "data/mods/modExample/content/asset.txt").read_text(), "v1")
        self.assertEqual((backup / "data/bin/config/r4game/user_config_matrix/pc/old.xml").read_text(), "<v1/>")

    def test_disabled_mod_stays_disabled(self):
        mod = self.installV1(enabled=False)
        result = ModUpdater(self.model).update(mod, self.package("v2", "v2"), NexusInfo(42, 2, "2.0"))
        self.assertTrue(result.success)
        self.assertFalse(self.model.get("Example").enabled)
        self.assertEqual(self.asset("~modExample"), "v2")
        self.assertFalse((self.game / "Mods/modExample").exists())
        self.assertTrue((self.menu / "example.xml.disabled").exists())
        self.assertFalse((self.menu / "example.xml").exists())

    def test_failed_update_restores_everything(self):
        mod = self.installV1(priority=5)
        broken = self.root / "packages" / "broken"
        broken.mkdir(parents=True)
        (broken / "readme.txt").write_text("no mod data here")
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch("src.core.model.MessageAlertWritingFailed"):
            result = ModUpdater(self.model).update(mod, str(broken), NexusInfo(42, 2, "2.0"))
        self.assertFalse(result.success)
        self.assertIs(self.model.get("Example"), mod)
        self.assertEqual(self.model.get("Example").nexus.fileId, 1)
        after = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and "Backups" not in path.parts
        }
        for name in ("installed.xml", "installed.xml.old"):
            before.pop(Path("Manager") / name, None)
            after.pop(Path("Manager") / name, None)
        self.assertEqual(after, before)

    def test_failed_update_of_disabled_mod_restores_disabled_layout(self):
        mod = self.installV1(enabled=False)
        broken = self.root / "packages" / "broken"
        broken.mkdir(parents=True)
        result = ModUpdater(self.model).update(mod, str(broken), NexusInfo(42, 2, "2.0"))
        self.assertFalse(result.success)
        self.assertFalse(self.model.get("Example").enabled)
        self.assertEqual(self.asset("~modExample"), "v1")
        self.assertFalse((self.game / "Mods/modExample").exists())
        self.assertTrue((self.menu / "example.xml.disabled").exists())

    def test_restore_puts_back_previous_version_and_backs_up_current(self):
        mod = self.installV1(priority=2)
        updater = ModUpdater(self.model)
        self.assertTrue(updater.update(mod, self.package("v2", "v2"), NexusInfo(42, 2, "2.0")).success)
        backups = updater.listBackups("Example")
        self.assertEqual(len(backups), 1)
        result = updater.restore(backups[0])
        self.assertTrue(result.success)
        restored = self.model.get("Example")
        self.assertEqual(self.asset(), "v1")
        self.assertEqual((self.menu / "old.xml").read_text(), "<v1/>")
        self.assertEqual((restored.nexus.fileId, restored.nexus.version), (1, "1.0"))
        self.assertEqual(restored.priority, "2")
        self.assertEqual([setting.option for setting in restored.usersettings], ["OptionA"])
        self.assertIn("OptionA=7", (self.settings / "user.settings").read_text())
        self.assertEqual(len(updater.listBackups("Example")), 2)

    def test_nexus_details_round_trip_through_inventory(self):
        mod = self.installV1()
        mod.nexus = NexusInfo(42, 7, "1.2.3", "Example Main", 1700000000, "Example 42 1.2.3 a.zip")
        self.model.write()
        self.assertEqual(Model(ignorelock=True).get("Example").nexus, mod.nexus)
        mod.nexus = None
        self.model.write()
        self.assertIsNone(Model(ignorelock=True).get("Example").nexus)

    def test_install_reads_nexus_details_from_archive_name(self):
        base = self.root / "packages" / "Cool Mod 77 1.5 2026-10-08T12-42Z abcDEF123"
        archive = Path(shutil.make_archive(str(base), "zip", self.package("tmp", "v1")))
        self.assertTrue(Installer(self.model).installMod(str(archive))[0])
        mod = self.model.get("Cool Mod")
        self.assertEqual((mod.nexus.modId, mod.nexus.version, mod.nexus.archive), (77, "1.5", archive.name))

    def test_restore_after_the_mod_was_uninstalled(self):
        mod = self.installV1()
        updater = ModUpdater(self.model)
        self.assertTrue(updater.update(mod, self.package("v2", "v2"), NexusInfo(42, 2, "2.0")).success)
        self.assertTrue(Installer(self.model).uninstallMod(self.model.get("Example")))
        self.assertEqual(list(self.model.list()), [])
        backups = updater.listBackups("Example")
        result = updater.restore(backups[0])
        self.assertTrue(result.success)
        self.assertEqual(result.backup, "")  # nothing was installed, so nothing needed backing up
        restored = Model(ignorelock=True).get("Example")
        self.assertEqual(self.asset(), "v1")
        self.assertEqual((restored.nexus.fileId, restored.nexus.version), (1, "1.0"))
        self.assertEqual([setting.option for setting in restored.usersettings], ["OptionA"])
        self.assertCountEqual(restored.menus, ["example.xml", "old.xml"])

    def test_enabled_and_disabled_copies_of_a_folder_are_both_handled(self):
        mod = self.installV1()
        stale = self.game / "Mods" / "~modExample" / "content"
        stale.mkdir(parents=True)
        (stale / "asset.txt").write_text("stale")
        broken = self.root / "packages" / "broken"
        broken.mkdir(parents=True)
        updater = ModUpdater(self.model)

        self.assertFalse(updater.update(mod, str(broken), NexusInfo(42, 2, "2.0")).success)
        self.assertEqual(self.asset(), "v1")
        self.assertEqual(self.asset("~modExample"), "stale")

        result = updater.update(mod, self.package("v2", "v2"), NexusInfo(42, 2, "2.0"))
        self.assertTrue(result.success)
        self.assertEqual(self.asset(), "v2")
        self.assertFalse((self.game / "Mods" / "~modExample").exists())
        self.assertEqual((Path(result.backup) / "data/mods/modExample/content/asset.txt").read_text(), "v1")
        self.assertEqual((Path(result.backup) / "extra/mods/~modExample/content/asset.txt").read_text(), "stale")

    def test_list_backups_filters_by_mod_and_skips_unreadable_folders(self):
        mod = self.installV1()
        updater = ModUpdater(self.model)
        first = updater.createBackup(mod, "update")
        second = updater.createBackup(mod, "restore")
        Installer(self.model).installMod(self.package("other", "o", menus=(), folder="modOther"), name="Other")
        updater.createBackup(self.model.get("Other"), "update")
        (Path(self.config.backups) / "Example" / "junk").mkdir()
        (Path(self.config.backups) / "stray.txt").write_text("")

        self.assertNotEqual(first, second)
        backups = updater.listBackups("Example")
        self.assertEqual({backup.directory for backup in backups}, {first, second})
        self.assertEqual({backup.reason for backup in backups}, {"update", "restore"})
        self.assertEqual(backups[0].version, "1.0")
        self.assertEqual(len(updater.listBackups()), 3)
        self.assertEqual(updater.listBackups("Missing"), [])


if __name__ == "__main__":
    unittest.main()
