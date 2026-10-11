import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.core.fetcher import isValidModFolder
from src.core.health import checkHealth
from src.core.installer import Installer
from src.core.model import Model
from src.core.nexus import NexusInfo
from src.core.updater import ModUpdater
from src.util.binfiles import binFileTarget, safeBinPath
from tests.test_nexus import FakeConfig

ADDON = "x64_dx12/W3LivingSky.addon64"
SHADER = "x64_dx12/w3_living_sky.hlsl"
DEEP = "x64_dx12/reshade-shaders/Shaders/living.fx"


class RuleTests(unittest.TestCase):
    def test_which_files_are_bin_files(self):
        self.assertEqual(binFileTarget(["bin", "x64_dx12", "W3LivingSky.addon64"]), ADDON)
        self.assertEqual(binFileTarget(["The Living Continent", "Bin", "x64", "a.dll"]), "x64/a.dll")
        self.assertEqual(binFileTarget(["bin", "x64_dx12", "reshade-shaders", "Shaders", "living.fx"]), DEEP)
        self.assertEqual(binFileTarget(["bin", "config", "r4game", "user_config_matrix", "pc", "m.xml"]), "")
        self.assertEqual(binFileTarget(["bin", "config", "base", "rendering.ini"]), "")
        self.assertEqual(binFileTarget(["Mods", "modA", "content", "bin", "x.ws"]), "")  # inside a mod folder
        self.assertEqual(binFileTarget(["bin", "x64_dx12", "readme.txt"]), "")
        self.assertEqual(binFileTarget(["bin", "x64_dx12", "README"]), "")
        self.assertEqual(binFileTarget(["bin"]), "")
        self.assertEqual(binFileTarget(["x64_dx12", "a.dll"]), "")

    def test_safe_paths(self):
        self.assertEqual(safeBinPath("x64_dx12\\a.dll"), "x64_dx12/a.dll")
        self.assertEqual(safeBinPath("./x64//a.dll"), "x64/a.dll")
        for unsafe in ("../x.dll", "x64/../../x.dll", "C:/Windows/x.dll", "config/base/a.ini", ""):
            self.assertEqual(safeBinPath(unsafe), "", unsafe)


class InstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = FakeConfig(self.root)
        patcher = patch("src.globals.data.config", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.game = Path(self.config.game)
        self.bin = self.game / "bin"
        self.menu = Path(self.config.menu)
        (self.menu / "input.xml").write_text("<input/>", encoding="utf-16")
        (self.bin / "x64_dx12").mkdir(parents=True)
        (self.bin / "x64_dx12" / "witcher3.exe").write_bytes(b"the game")
        self.model = Model(ignorelock=True)
        self.lines = []

    def package(self, name, version="1", bins=(ADDON, SHADER, DEEP), mod="modW3LivingSky", extra=()):
        '''Laid out like The Living Continent: a mod folder, a menu, files for bin/x64_dx12 and a readme'''
        source = self.root / "packages" / name
        (source / "Mods" / mod / "content" / "scripts").mkdir(parents=True)
        (source / "Mods" / mod / "content" / "scripts" / "sky.ws").write_text(f"// {version}")
        menu = source / "bin/config/r4game/user_config_matrix/pc"
        menu.mkdir(parents=True)
        (menu / "mod00LivingContinent.xml").write_text("<menu/>")
        for relative in bins:
            target = source / "bin" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"{relative} {version}".encode())
        (source / "bin" / "x64_dx12" / "readme.txt").write_text("how to use the add-on")
        (source / "The Living Continent - Readme.md").write_text("# readme")
        for relative, content in extra:
            target = source / "bin" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return str(source)

    def install(self, source, name="The Living Continent"):
        installer = Installer(self.model, output=self.lines.append)
        return installer.installMod(source, name=name)

    def game_file(self, relative):
        return self.bin / Path(relative)

    def test_installs_into_the_bin_folder_and_records_it(self):
        result = self.install(self.package("v1"))
        self.assertEqual(result, (True, 1, 0))
        mod = self.model.get("The Living Continent")
        self.assertEqual(sorted(mod.bins), sorted([ADDON, SHADER, DEEP]))
        self.assertEqual(self.game_file(ADDON).read_bytes(), f"{ADDON} 1".encode())
        self.assertTrue(self.game_file(DEEP).is_file())
        self.assertFalse(self.game_file("x64_dx12/readme.txt").exists(), "documentation isn't installed")
        self.assertEqual(mod.menus, ["mod00LivingContinent.xml"])
        self.assertTrue((self.menu / "mod00LivingContinent.xml").is_file())
        self.assertTrue(any("files in the game's bin folder" in line for line in self.lines))
        self.model.write()
        self.assertEqual(sorted(Model(ignorelock=True).get("The Living Continent").bins), sorted(mod.bins))

    def test_xml_files_for_bin_are_not_taken_for_menus(self):
        self.install(self.package("v1", bins=(ADDON, "x64_dx12/addon_settings.xml")))
        mod = self.model.get("The Living Continent")
        self.assertIn("x64_dx12/addon_settings.xml", mod.bins)
        self.assertEqual(mod.menus, ["mod00LivingContinent.xml"])
        self.assertFalse((self.menu / "addon_settings.xml").exists())

    def test_a_mod_with_only_bin_files_is_a_mod(self):
        source = self.root / "addon-only" / "bin" / "x64_dx12"
        source.mkdir(parents=True)
        (source / "Only.addon64").write_bytes(b"x")
        self.assertTrue(isValidModFolder(str(self.root / "addon-only")))
        self.assertEqual(self.install(str(self.root / "addon-only"), "Only Addon")[0], True)
        self.assertEqual(self.model.get("Only Addon").bins, ["x64_dx12/Only.addon64"])

    def test_never_overwrites_files_that_are_not_its_own(self):
        self.game_file(ADDON).write_bytes(b"something installed by hand")
        self.game_file(SHADER).write_bytes(f"{SHADER} 1".encode())  # identical: taken over
        result = self.install(self.package("v1", extra=(("x64_dx12/witcher3.exe", b"replaced!"),)))
        self.assertEqual(result, (True, 1, 1), "reported as incomplete")
        mod = self.model.get("The Living Continent")
        self.assertEqual(sorted(mod.bins), sorted([SHADER, DEEP]))
        self.assertEqual(self.game_file(ADDON).read_bytes(), b"something installed by hand")
        self.assertEqual(self.game_file("x64_dx12/witcher3.exe").read_bytes(), b"the game")
        self.assertTrue(any("isn't from this mod" in line for line in self.lines))

    def test_another_mods_file_is_left_alone(self):
        self.install(self.package("first", bins=(ADDON,), mod="modFirst"), name="First")
        self.install(self.package("second", version="2", bins=(ADDON, SHADER), mod="modSecond"), name="Second")
        self.assertEqual(self.model.get("First").bins, [ADDON])
        self.assertEqual(self.model.get("Second").bins, [SHADER])
        self.assertEqual(self.game_file(ADDON).read_bytes(), f"{ADDON} 1".encode())
        self.assertTrue(any("belongs to First" in line for line in self.lines))

    def test_disable_enable_and_uninstall(self):
        self.install(self.package("v1"))
        mod = self.model.get("The Living Continent")
        mod.disable()
        self.assertFalse(self.game_file(ADDON).exists())
        self.assertTrue(Path(str(self.game_file(ADDON)) + ".disabled").is_file())
        mod.enable()
        self.assertTrue(self.game_file(ADDON).is_file())
        mod.disable()
        self.assertTrue(Installer(self.model).uninstallMod(mod))
        for relative in (ADDON, SHADER, DEEP):
            self.assertFalse(self.game_file(relative).exists())
            self.assertFalse(Path(str(self.game_file(relative)) + ".disabled").exists())
        self.assertEqual(self.game_file("x64_dx12/witcher3.exe").read_bytes(), b"the game")

    def test_reinstalling_a_version_without_a_file_takes_it_out(self):
        from PySide6.QtWidgets import QMessageBox

        self.install(self.package("v1"))
        with patch("src.core.installer.MessageOverwrite", return_value=QMessageBox.StandardButton.Yes):
            self.install(self.package("v2", version="2", bins=(ADDON,)))
        mod = self.model.get("The Living Continent")
        self.assertEqual(mod.bins, [ADDON])
        self.assertEqual(self.game_file(ADDON).read_bytes(), f"{ADDON} 2".encode())
        self.assertFalse(self.game_file(SHADER).exists())

    def test_update_backs_up_and_restores_bin_files(self):
        self.install(self.package("v1"))
        mod = self.model.get("The Living Continent")
        mod.nexus = NexusInfo(13650, 1, "1.4.0", archive="v1.zip")
        updater = ModUpdater(self.model)
        result = updater.update(
            mod, self.package("v2", version="2", bins=(ADDON, SHADER)), NexusInfo(13650, 2, "1.4.1")
        )
        self.assertTrue(result.success)
        self.assertEqual(self.game_file(ADDON).read_bytes(), f"{ADDON} 2".encode())
        self.assertFalse(self.game_file(DEEP).exists())
        self.assertEqual((Path(result.backup) / "data/bin" / ADDON).read_bytes(), f"{ADDON} 1".encode())
        self.assertEqual(sorted(self.model.get("The Living Continent").bins), sorted([ADDON, SHADER]))
        restored = updater.restore(updater.listBackups("The Living Continent")[0])
        self.assertTrue(restored.success)
        self.assertEqual(self.game_file(ADDON).read_bytes(), f"{ADDON} 1".encode())
        self.assertEqual(self.game_file(DEEP).read_bytes(), f"{DEEP} 1".encode())
        self.assertEqual(sorted(self.model.get("The Living Continent").bins), sorted([ADDON, SHADER, DEEP]))

    def test_failed_update_puts_the_old_bin_files_back(self):
        self.install(self.package("v1", bins=(ADDON, SHADER)))
        mod = self.model.get("The Living Continent")
        real = Installer.installMod

        def installsThenFails(installer, source, name=None):
            real(installer, source, name=name)
            return False, 0, 0

        with (
            patch("src.core.updater.Installer.installMod", installsThenFails),
            patch("src.core.model.MessageAlertWritingFailed"),
        ):
            result = ModUpdater(self.model).update(
                mod, self.package("v2", version="2", bins=(ADDON, "x64_dx12/new.addon64")), NexusInfo(1, 2, "2")
            )
        self.assertFalse(result.success)
        self.assertEqual(self.game_file(ADDON).read_bytes(), f"{ADDON} 1".encode())
        self.assertEqual(self.game_file(SHADER).read_bytes(), f"{SHADER} 1".encode())
        self.assertFalse(self.game_file("x64_dx12/new.addon64").exists())
        self.assertEqual(self.game_file("x64_dx12/witcher3.exe").read_bytes(), b"the game")

    def test_health_check_notices_missing_files(self):
        self.install(self.package("v1", bins=(ADDON,)))
        self.game_file(ADDON).unlink()
        config = SimpleNamespace(**{**vars(self.config), "priority": None})
        issues = [(i.mod, i.problem, i.detail) for i in checkHealth(self.model, config)]
        self.assertIn(("The Living Continent", "Game file is missing", f"bin/{ADDON} isn't in the game folder"), issues)


if __name__ == "__main__":
    unittest.main()
