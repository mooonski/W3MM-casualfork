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

    def test_the_toolbar_uses_it_for_the_four_files(self):
        source = Path("src/gui/main_widget.py").read_text(encoding="utf-8")
        for name in ('"/input.xml"', '"/input.settings"', "data.getConfig().usersettings", '"/mods.settings"'):
            line = next(line for line in source.splitlines() if name in line and "openTextFile" in line)
            self.assertIn("openTextFile", line)


if __name__ == "__main__":
    unittest.main()
