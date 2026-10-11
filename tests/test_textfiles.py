import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.util import util


class OpenTextFileTests(unittest.TestCase):
    def test_settings_files_open_in_notepad_on_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Path(folder, "mods.settings")
            settings.write_text("[mod0000_MergedFiles]\nEnabled=1\n")
            with (
                patch.object(util.sys, "platform", "win32"),
                patch.object(util.subprocess, "Popen") as popen,
                patch.object(util, "openFile") as fallback,
            ):
                util.openTextFile(str(settings))
                popen.assert_called_once_with(["notepad.exe", str(settings)])
                fallback.assert_not_called()
                popen.side_effect = OSError("no notepad")
                util.openTextFile(str(settings))
                fallback.assert_called_once_with(str(settings))
            with patch.object(util.sys, "platform", "linux"), patch.object(util, "openFile") as fallback:
                util.openTextFile(str(settings))
                fallback.assert_called_once_with(str(settings))
            with patch.object(util.sys, "platform", "win32"), patch.object(util, "openFile") as fallback:
                util.openTextFile(str(Path(folder, "missing.settings")))  # lets openFile report it
                fallback.assert_called_once()

    def test_the_toolbar_buttons(self):
        source = Path("src/gui/main_widget.py").read_text(encoding="utf-8")
        line = next(line for line in source.splitlines() if '"/input.xml"' in line)
        self.assertIn("openTextFile", line, "input.xml opens in Notepad")
        self.assertIn("self.showGameSettings(KEYS_TAB)", source)
        self.assertIn("self.showGameSettings(USER_TAB)", source)
        self.assertIn("actionTemp.triggered.connect(self.showLoadOrderPanel)", source)


if __name__ == "__main__":
    unittest.main()
