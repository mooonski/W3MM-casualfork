import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

import main
from src.globals.constants import APP_ID, FORK_VERSION, forkLabel
from src.util.util import getProgramRootFolder, getVersionString


class VersionTests(unittest.TestCase):
    def test_fork_is_named_in_the_version(self):
        self.assertEqual(FORK_VERSION, "FMTweak Form 1.0")
        self.assertIn("(FMTweak Form 1.0, source)", getVersionString())
        with patch.object(sys, "frozen", True, create=True):
            self.assertEqual(forkLabel(), "FMTweak Form 1.0, installed")
            self.assertIn("(FMTweak Form 1.0, installed)", getVersionString())
        self.assertIn("0.10.5", getVersionString())


class IconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fork_icon_has_every_taskbar_size(self):
        icon = QIcon(os.path.join(getProgramRootFolder(), "res", "w3mm-fork.ico"))
        sizes = {size.width() for size in icon.availableSizes()}
        self.assertTrue({16, 24, 32, 48, 256} <= sizes, sizes)

    def test_own_taskbar_identity(self):
        windll = Mock()
        with patch.object(sys, "platform", "win32"), patch("ctypes.windll", windll, create=True):
            main.useOwnTaskbarIcon(APP_ID)
        windll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with("W3MM.casualfork")
        with patch.object(sys, "platform", "linux"), patch("ctypes.windll", windll, create=True):
            main.useOwnTaskbarIcon(APP_ID)
        self.assertEqual(windll.shell32.SetCurrentProcessExplicitAppUserModelID.call_count, 1)


class LogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stdout, self.stderr = sys.stdout, sys.stderr
        self.addCleanup(self.restore)

    def restore(self):
        for stream in (sys.stdout, sys.stderr):
            if stream not in (self.stdout, self.stderr) and stream is not None:
                stream.close()
        sys.stdout, sys.stderr = self.stdout, self.stderr

    def test_nothing_changes_with_a_console(self):
        self.assertIsNone(main.logWithoutConsole(["main.py"]))
        self.assertIs(sys.stdout, self.stdout)

    def test_without_a_console_output_goes_to_a_log(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            sys.stdout = sys.stderr = None
            first = main.logWithoutConsole(["main.py"])
            print("first run")
            self.restore()
            sys.stdout = sys.stderr = None
            second = main.logWithoutConsole(["main.py"])
            print("second run")
            self.restore()
        folder = self.root / "The Witcher 3 Mod Manager"
        self.assertEqual((first, second), (str(folder / "W3MM.log"),) * 2)
        self.assertIn("second run", (folder / "W3MM.log").read_text(encoding="utf-8"))
        self.assertIn("first run", (folder / "W3MM.previous.log").read_text(encoding="utf-8"))

    def test_windowless_python_logs_even_with_stand_in_streams(self):
        stand_in = io.StringIO()
        sys.stdout = sys.stderr = stand_in
        with patch.object(sys, "executable", r"C:\Python\pythonw.exe"):
            log = main.logWithoutConsole(["main.py", "-c", str(self.root)])
        print("from pythonw")
        self.restore()
        self.assertEqual(log, str(self.root / "W3MM.log"))
        self.assertIn("from pythonw", (self.root / "W3MM.log").read_text(encoding="utf-8"))
        self.assertEqual(stand_in.getvalue(), "")

    def test_config_override_keeps_its_own_log(self):
        sys.stdout = sys.stderr = None
        log = main.logWithoutConsole(["main.py", "-c", str(self.root / "sandbox")])
        self.restore()
        self.assertEqual(log, str(self.root / "sandbox" / "W3MM.log"))
        self.assertIn("-c", (self.root / "sandbox" / "W3MM.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
