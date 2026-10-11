import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

from src.configuration.config import Configuration
from src.core.nxm import NxmRegistration, handlerCommand, handlerName
from src.gui.main_widget import CustomMainWidget
from tests.test_nxm import FakeRegistry

ZCOM = r'"C:\Users\me\AppData\Local\ZCOM Mod Manager\zcom-mod-manager.exe" "%1"'
VORTEX = r'"C:\Program Files\Black Tree Gaming Ltd\Vortex\Vortex.exe" -d "%1"'


class NameTests(unittest.TestCase):
    def test_known_and_unknown_programs(self):
        self.assertEqual(handlerName(ZCOM), "ZCOM Mod Manager")
        self.assertEqual(handlerName(VORTEX), "Vortex")
        self.assertEqual(handlerName(r'"D:\MO2\nxmhandler.exe" "%1"'), "Mod Organizer 2")
        self.assertEqual(handlerName(r'"D:\Tools\Other Manager.exe" "%1"'), "Other Manager.exe")
        self.assertEqual(handlerName(handlerCommand(["-c", "C:/other"])), "another copy of W3MM")
        self.assertEqual(handlerName(""), "no program")

    def test_the_choice_is_kept_in_the_config_file(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(Configuration, "write_config"):
            (Path(temporary) / "Documents").mkdir()
            config = Configuration(str(Path(temporary) / "Documents"), configPath=str(Path(temporary) / "Manager"))
            self.assertEqual((config.nxmwanted, config.nxmnoask), ("", ""))
            config.nxmwanted, config.nxmnoask = "1", "1"
            self.assertEqual((config.nxmwanted, config.nxmnoask), ("1", "1"))


class DefaultHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.config = SimpleNamespace(startarguments=["-c", "C:/config"], previousnxm="", nxmwanted="1", nxmnoask="")
        self.registry = FakeRegistry(ZCOM)
        self.widget = Mock()
        self.widget.nxmRegistration = lambda: NxmRegistration(self.registry, self.config)
        self.widget.makeDefaultNxmHandler = lambda: CustomMainWidget.makeDefaultNxmHandler(self.widget)
        for patcher in (
            patch("src.gui.main_widget.data.config", self.config),
            patch("src.gui.main_widget.platform", "win32"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def lines(self):
        return [c.args[0] for c in self.widget.output.call_args_list]

    def answer(self, button, dontAsk=False):
        '''Stands in for the startup question: answers it and ticks "Don't ask again" if asked to'''
        asked = []

        class Box(QMessageBox):
            def __init__(self, parent=None):
                super().__init__()

            def exec(self):
                asked.append(self.text())
                self.checkBox().setChecked(dontAsk)
                return button

        return patch("src.gui.main_widget.QMessageBox", Box), asked

    def test_taking_the_links_back(self):
        CustomMainWidget.makeDefaultNxmHandler(self.widget)
        self.assertTrue(self.registry.command.endswith('-c C:/config --nxm "%1"'))
        self.assertEqual(self.config.previousnxm, ZCOM, "other games still go to ZCOM")
        self.widget.actionHandleNxm.setChecked.assert_called_with(True)
        self.assertIn("they were going to ZCOM Mod Manager", self.lines()[-1])
        CustomMainWidget.makeDefaultNxmHandler(self.widget)
        self.assertEqual(self.lines()[-1], "Nexus links already open in W3MM.")
        self.assertEqual(len(self.registry.writes), 1)

    def test_vortex_took_them_so_vortex_gets_the_other_games(self):
        NxmRegistration(self.registry, self.config).register()
        self.registry.command = VORTEX  # Vortex registered itself since
        CustomMainWidget.makeDefaultNxmHandler(self.widget)
        self.assertEqual(self.config.previousnxm, VORTEX)
        self.assertIn("Links for other games are passed to Vortex", self.lines()[-1])

    def test_startup_asks_when_another_program_took_them(self):
        exec_, asked = self.answer(QMessageBox.StandardButton.Yes)
        with exec_:
            CustomMainWidget.checkNxmHandler(self.widget)
        self.assertIn("now open with ZCOM Mod Manager", asked[0])
        self.assertTrue(self.registry.command.endswith('--nxm "%1"'))

    def test_startup_no_and_dont_ask_again(self):
        exec_, asked = self.answer(QMessageBox.StandardButton.No, dontAsk=True)
        with exec_:
            CustomMainWidget.checkNxmHandler(self.widget)
            self.assertEqual((self.registry.command, self.config.nxmnoask), (ZCOM, "1"))
            CustomMainWidget.checkNxmHandler(self.widget)
        self.assertEqual(len(asked), 1, "not asked a second time")
        self.assertIn("Make W3MM the Default for Nexus Links", self.lines()[-1])
        # choosing it from the menu later asks again next time someone else takes them
        CustomMainWidget.makeDefaultNxmHandler(self.widget)
        self.assertEqual(self.config.nxmnoask, "")

    def test_startup_stays_quiet_when_it_shouldnt_ask(self):
        exec_, asked = self.answer(QMessageBox.StandardButton.Yes)
        with exec_:
            self.config.nxmwanted = "0"  # the user gave the links back
            CustomMainWidget.checkNxmHandler(self.widget)
            self.config.nxmwanted = ""  # never turned on
            CustomMainWidget.checkNxmHandler(self.widget)
            self.config.nxmwanted = "1"
            NxmRegistration(self.registry, self.config).register()  # still ours
            CustomMainWidget.checkNxmHandler(self.widget)
            with patch("src.gui.main_widget.platform", "linux"):
                self.registry.command = ZCOM
                CustomMainWidget.checkNxmHandler(self.widget)
        self.assertEqual(asked, [])

    def test_the_toggle_remembers_the_choice_and_shows_the_truth(self):
        self.config.nxmwanted = ""
        self.widget.actionHandleNxm.isChecked.return_value = True
        CustomMainWidget.handleNxmChanged(self.widget)
        self.assertEqual(self.config.nxmwanted, "1")
        self.widget.actionHandleNxm.isChecked.return_value = False
        CustomMainWidget.handleNxmChanged(self.widget)
        self.assertEqual((self.config.nxmwanted, self.registry.command), ("0", ZCOM))
        CustomMainWidget.showNxmState(self.widget)
        self.widget.actionHandleNxm.setChecked.assert_called_with(False)


if __name__ == "__main__":
    unittest.main()
