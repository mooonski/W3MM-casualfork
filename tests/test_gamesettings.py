import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.gamesettings import (
    OPTIONS,
    SETTINGS_BACKUPS,
    SLIDER,
    TOGGLE,
    SettingsFile,
    modActions,
    modMenuOptions,
    readBindings,
    readMenuOptions,
    settingOwners,
)
from src.domain.key import Key
from src.domain.usersetting import Usersetting
from src.gui.main_widget import CustomMainWidget
from src.gui.settings_dialog import KEYS_TAB, USER_TAB, GameSettingsDialog

USER = (
    "[Rendering]\r\nDecalsSpawnDistanceCutoff=100\r\n"
    "[W3LivingSky]\r\nNightSky=true\r\nStarBrightness=1\r\nCloudHeight=500\r\n"
    "[fhudHUD]\r\nenabled=true\r\n"
)
MENU = """<?xml version="1.0" encoding="UTF-16"?>
<!-- <Group id="Commented"> -->
<UserConfig>
  <Group id="W3LivingSky" displayName="w3ls">
    <VisibleVars>
      <Var id="NightSky" displayName="w3ls_sky" displayType="TOGGLE"/>
      <Var id="StarBrightness" displayName="w3ls_stars" displayType="OPTIONS">
        <OptionsArray>
          <Option id="0" displayName="dim"><Entry varId="StarBrightness" value="0"/></Option>
          <Option id="1" displayName="normal"><Entry varId="StarBrightness" value="1"/></Option>
          <Option id="2" displayName="bright"><Entry varId="StarBrightness" value="2"/></Option>
        </OptionsArray>
      </Var>
      <Var id="CloudHeight" displayName="w3ls_clouds" displayType="SLIDER;-40;900;94"/>
    </VisibleVars>
  </Group>
  <Group id="W3LivingSkySeasons" displayName="seasons">
    <VisibleVars>
      <Var overrideGroup="W3LivingSky" id="SeasonalWeather" displayType="TOGGLE"/>
    </VisibleVars>
  </Group>
</UserConfig>
"""
INPUT = (
    "[Exploration]\r\nIK_J=(Action=PanelJour)\r\nIK_J=(Action=HoldToSeeQuests)\r\nIK_K=(Action=PanelChar)\r\n"
    "IK_F7=(Action=SkyToggle)\r\nIK_F7=(Action=WaterToggle)\r\nIK_None=(Action=Unused)\r\n"
    "IK_Space=(Action=Jump,State=Duration,IdleTime=0.2)\r\nIK_Space=(Action=Leap)\r\n"
)


def mod(name, menus=(), usersettings=(), inputsettings=(), xmlkeys=()):
    return SimpleNamespace(
        name=name,
        menus=list(menus),
        usersettings=list(usersettings),
        inputsettings=list(inputsettings),
        xmlkeys=list(xmlkeys),
    )


class FileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def test_changing_one_value_leaves_the_rest_byte_for_byte(self):
        path = self.folder / "dx12user.settings"
        path.write_bytes(USER.encode())
        settings = SettingsFile(str(path))
        self.assertEqual((settings.encoding, settings.newline), ("utf-8", "\r\n"))
        self.assertEqual(len(settings.settings), 5)
        settings.change(settings.find("w3livingsky", "CLOUDHEIGHT"), "300")
        settings.save()
        self.assertEqual(path.read_bytes(), USER.replace("CloudHeight=500", "CloudHeight=300").encode())

    def test_utf16_files_and_odd_lines(self):
        path = self.folder / "input.settings"
        path.write_text("; note\n[A]\nnovalue\nK=V=W\n", encoding="utf-16")
        settings = SettingsFile(str(path))
        self.assertEqual(settings.encoding, "utf-16")
        self.assertEqual([(s.section, s.key, s.value) for s in settings.settings], [("A", "K", "V=W")])
        settings.change(settings.settings[0], "X")
        settings.save()
        self.assertEqual(path.read_text(encoding="utf-16"), "; note\n[A]\nnovalue\nK=X\n")
        self.assertEqual(SettingsFile(str(self.folder / "missing.settings")).settings, [])

    def test_menu_options(self):
        options = {option.var: option for option in readMenuOptions(MENU, "Living Continent")}
        self.assertEqual(set(options), {"NightSky", "StarBrightness", "CloudHeight", "SeasonalWeather"})
        self.assertEqual(options["SeasonalWeather"].group, "W3LivingSky", "saved under the section it names")
        self.assertEqual((options["NightSky"].kind, options["NightSky"].group), (TOGGLE, "W3LivingSky"))
        self.assertEqual(
            (options["StarBrightness"].kind, options["StarBrightness"].choices), (OPTIONS, ["0", "1", "2"])
        )
        self.assertEqual((options["CloudHeight"].kind, options["CloudHeight"].allowed()), (SLIDER, "-40 to 900"))
        self.assertEqual(options["NightSky"].problem("1.0"), "", "the game writes toggles as numbers too")
        self.assertIn("true or false", options["NightSky"].problem("yes"))
        self.assertEqual(options["StarBrightness"].problem("2"), "")
        self.assertIn("one of 0, 1, 2", options["StarBrightness"].problem("5"))
        self.assertIn("from -40 to 900", options["CloudHeight"].problem("1000"))
        self.assertIn("a number", options["CloudHeight"].problem("high"))

    def test_owners_from_menus_and_installed_settings(self):
        (self.folder / "living.xml").write_text(MENU, encoding="utf-16")
        mods = [
            mod("Living Continent", menus=["living.xml", "missing.xml"]),
            mod("Friendly HUD", usersettings=[Usersetting("[fhudHUD]", "enabled=true")]),
        ]
        options = modMenuOptions(str(self.folder), mods)
        keys, sections = settingOwners(mods, options)
        self.assertEqual(keys[("w3livingsky", "nightsky")], "Living Continent")
        self.assertEqual(keys[("fhudhud", "enabled")], "Friendly HUD")
        self.assertEqual(sections["w3livingsky"], "Living Continent")

    def test_bindings_and_clashes(self):
        path = self.folder / "input.settings"
        path.write_bytes(INPUT.encode())
        mods = [
            mod("Friendly HUD", inputsettings=[Key("[Exploration]", "IK_J=(Action=HoldToSeeQuests)")]),
            mod("Sky", xmlkeys=['<Var builder="Input" id="SkyToggle" displayName="sky"/>']),
            mod("Water", inputsettings=[Key("[Exploration]", "IK_F7=(Action=WaterToggle)")]),
        ]
        actions = modActions(mods)
        self.assertEqual(actions[("action", "skytoggle")], "Sky")
        self.assertEqual(actions[("binding", "exploration", "ik_j", "holdtoseequests")], "Friendly HUD")
        bindings = {(b.key, b.action): b for b in readBindings(SettingsFile(str(path)), actions)}
        hold = bindings[("IK_J", "HoldToSeeQuests")]
        self.assertEqual((hold.mod, hold.others, hold.clash), ("Friendly HUD", ["PanelJour"], False))
        self.assertTrue(bindings[("IK_F7", "SkyToggle")].clash, "two mods on F7")
        self.assertTrue(bindings[("IK_F7", "WaterToggle")].clash)
        self.assertEqual(bindings[("IK_None", "Unused")].others, [])
        self.assertFalse(bindings[("IK_Space", "Jump")].clash)


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = self.root / "The Witcher 3"
        self.settings.mkdir()
        (self.settings / "dx12user.settings").write_bytes(USER.encode())
        (self.settings / "user.settings").write_bytes(USER.encode())
        (self.settings / "input.settings").write_bytes(INPUT.encode())
        self.menu = self.root / "menu"
        self.menu.mkdir()
        (self.menu / "living.xml").write_text(MENU, encoding="utf-16")
        self.mods = [
            mod("Living Continent", menus=["living.xml"]),
            mod("Friendly HUD", usersettings=[Usersetting("[fhudHUD]", "enabled=true")]),
        ]
        self.lines = []

    def dialog(self, running=False, tab=USER_TAB):
        return GameSettingsDialog(
            None,
            str(self.settings),
            "dx12user.settings",
            str(self.menu),
            self.mods,
            str(self.root / "backups"),
            tab,
            self.lines.append,
            running=lambda: running,
        )

    def row(self, dialog, key):
        return next(
            dialog.userTree.topLevelItem(i)
            for i in range(dialog.userTree.topLevelItemCount())
            if dialog.userTree.topLevelItem(i).text(1) == key
        )

    def visible(self, tree, column=1):
        return [
            tree.topLevelItem(i).text(column)
            for i in range(tree.topLevelItemCount())
            if not tree.topLevelItem(i).isHidden()
        ]

    def test_rows_filters_and_hints(self):
        dialog = self.dialog()
        self.assertEqual(dialog.userTree.topLevelItemCount(), 5)
        self.assertIn("5 settings, 4 from your mods", dialog.userHeading.text())
        self.assertEqual(self.row(dialog, "CloudHeight").text(3), "-40 to 900")
        self.assertEqual(self.row(dialog, "NightSky").text(4), "Living Continent")
        dialog.onlyModSettings.setChecked(True)
        self.assertNotIn("DecalsSpawnDistanceCutoff", self.visible(dialog.userTree))
        dialog.userSearch.setText("star")
        dialog.filterUser()
        self.assertEqual(self.visible(dialog.userTree), ["StarBrightness"])
        self.assertFalse(dialog.saveButton.isEnabled())

    def test_editing_and_saving_with_a_backup(self):
        dialog = self.dialog()
        self.row(dialog, "CloudHeight").setText(2, "300")
        self.row(dialog, "NightSky").setText(2, "false")
        self.assertEqual(dialog.userStatus.text(), "2 unsaved changes")
        self.assertTrue(self.row(dialog, "CloudHeight").font(2).bold())
        self.assertTrue(dialog.save())
        expected = USER.replace("CloudHeight=500", "CloudHeight=300").replace("NightSky=true", "NightSky=false")
        self.assertEqual((self.settings / "dx12user.settings").read_bytes(), expected.encode())
        self.assertEqual((self.settings / "user.settings").read_bytes(), expected.encode(), "kept the same in both")
        backups = sorted((self.root / "backups" / SETTINGS_BACKUPS).iterdir())
        self.assertEqual([path.read_bytes() for path in backups], [USER.encode(), USER.encode()])
        self.assertIn("Saved 2 settings to dx12user.settings (also in user.settings)", self.lines[-1])
        self.assertEqual(dialog.changes, {})

    def test_setting_it_back_is_not_a_change_and_undo(self):
        dialog = self.dialog()
        self.row(dialog, "CloudHeight").setText(2, "300")
        self.row(dialog, "CloudHeight").setText(2, "500")
        self.assertEqual(dialog.changes, {})
        self.row(dialog, "CloudHeight").setText(2, "300")
        dialog.undoButton.click()
        self.assertEqual((dialog.changes, self.row(dialog, "CloudHeight").text(2)), ({}, "500"))

    def test_values_the_menu_doesnt_offer_and_a_running_game_need_a_yes(self):
        dialog = self.dialog(running=True)
        self.row(dialog, "StarBrightness").setText(2, "9")
        self.assertIn("one of 0, 1, 2", self.row(dialog, "StarBrightness").toolTip(2))
        with patch("src.gui.settings_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as asked:
            self.assertFalse(dialog.save())
        self.assertIn("StarBrightness=9", asked.call_args.args[2])
        with (
            patch("src.gui.settings_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
            patch("src.gui.settings_dialog.QMessageBox.warning", return_value=QMessageBox.StandardButton.No) as warned,
        ):
            self.assertFalse(dialog.save())
        self.assertIn("is running", warned.call_args.args[1])
        self.assertEqual((self.settings / "dx12user.settings").read_bytes(), USER.encode(), "nothing written")

    def test_closing_with_changes_asks(self):
        dialog = self.dialog()
        self.row(dialog, "CloudHeight").setText(2, "300")
        with patch("src.gui.settings_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
            dialog.reject()
        self.assertTrue(dialog.isVisible() or dialog.result() == 0)
        self.assertEqual(len(dialog.changes), 1)
        with patch("src.gui.settings_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Save):
            dialog.reject()
        self.assertIn(b"CloudHeight=300", (self.settings / "dx12user.settings").read_bytes())

    def test_only_the_value_column_is_edited(self):
        dialog = self.dialog()
        item = self.row(dialog, "CloudHeight")
        with patch.object(dialog.userTree, "editItem") as edit:
            dialog.userTree.itemDoubleClicked.emit(item, 0)
        edit.assert_called_once_with(item, 2)

    def test_key_bindings_tab(self):
        self.mods.append(mod("Sky", xmlkeys=['<Var builder="Input" id="SkyToggle"/>']))
        self.mods.append(mod("Water", inputsettings=[Key("[Exploration]", "IK_F7=(Action=WaterToggle)")]))
        dialog = self.dialog(tab=KEYS_TAB)
        self.assertEqual(dialog.tabs.currentIndex(), KEYS_TAB)
        self.assertIn("8 bindings, 2 from your mods, 2 clashes between mods", dialog.keysHeading.text())
        dialog.onlyClashes.setChecked(True)
        self.assertEqual(sorted(self.visible(dialog.keyTree, 2)), ["SkyToggle", "WaterToggle"])
        dialog.onlyClashes.setChecked(False)
        dialog.keySearch.setText("journal panel")
        dialog.filterKeys()
        self.assertEqual(self.visible(dialog.keyTree, 2), [])
        dialog.keySearch.setText("ik_j")
        dialog.filterKeys()
        self.assertEqual(sorted(self.visible(dialog.keyTree, 2)), ["HoldToSeeQuests", "PanelJour"])

    def test_main_window_buttons(self):
        widget = Mock()
        config = SimpleNamespace(settings="S", usersettings="dx12user.settings", menu="M", backups="B")
        widget.model.all.return_value = self.mods
        with (
            patch("src.gui.main_widget.data.config", config),
            patch("src.gui.main_widget.GameSettingsDialog") as dialog,
        ):
            CustomMainWidget.showGameSettings(widget, KEYS_TAB)
        self.assertEqual(dialog.call_args.args[1:7], ("S", "dx12user.settings", "M", self.mods, "B", KEYS_TAB))
        dialog.return_value.exec.assert_called_once()
        widget.loadOrder.currentItem.return_value = None
        widget.loadOrder.topLevelItemCount.return_value = 3
        CustomMainWidget.showLoadOrderPanel(widget)
        widget.loadOrder.setFocus.assert_called_once()
        widget.loadOrder.setCurrentItem.assert_called_once()
        self.assertIn("mods.settings", widget.output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
