'''Tests for the Nexus Mods zip made by the release build'''

import hashlib
import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('build_release', ROOT / 'packaging' / 'build_release.py')
assert spec and spec.loader
build_release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_release)


class NexusZipTests(unittest.TestCase):
    def test_zip_holds_installer_licence_and_readme_with_hash(self) -> None:
        with tempfile.TemporaryDirectory() as dist:
            installer = Path(dist, 'W3MM-casualfork-1.2-setup.exe')
            installer.write_bytes(b'installer')
            target = build_release.nexusZip('1.2', dist)
            self.assertEqual(Path(target).name, 'W3MM-casualfork-1.2-nexus.zip')
            with zipfile.ZipFile(target) as package:
                self.assertEqual(
                    sorted(package.namelist()), ['LICENSE.txt', 'README.txt', 'W3MM-casualfork-1.2-setup.exe']
                )
                self.assertEqual(package.read('W3MM-casualfork-1.2-setup.exe'), b'installer')
                self.assertEqual(package.read('LICENSE.txt'), (ROOT / 'LICENSE').read_bytes())
                readme = package.read('README.txt').decode()
            self.assertIn(hashlib.sha256(b'installer').hexdigest(), readme)
            self.assertIn('FMTweak Form 1.2', readme)
            self.assertIn('\r\n', readme)

    def test_refuses_to_replace_an_existing_zip(self) -> None:
        with tempfile.TemporaryDirectory() as dist:
            Path(dist, 'W3MM-casualfork-1.2-setup.exe').write_bytes(b'installer')
            existing = Path(dist, 'W3MM-casualfork-1.2-nexus.zip')
            existing.write_bytes(b'old')
            with self.assertRaises(SystemExit):
                build_release.nexusZip('1.2', dist)
            self.assertEqual(existing.read_bytes(), b'old')

    def test_needs_the_installer(self) -> None:
        with tempfile.TemporaryDirectory() as dist, self.assertRaises(SystemExit):
            build_release.nexusZip('1.2', dist)


if __name__ == '__main__':
    unittest.main()
