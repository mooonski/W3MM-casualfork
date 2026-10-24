'''Finds installed copies of The Witcher 3 through Steam and GOG, for the setup dialog'''

import re
import sys
from dataclasses import dataclass
from os import path
from typing import Callable

from src.util.util import normalizePath

STEAM_APP_ID = "292030"
STEAM_FOLDER = "The Witcher 3"
# every Witcher 3 release on GOG is named like this in the registry (base game, GOTY, Remastered)
GOG_NAME = re.compile(r"witcher\s*3", re.IGNORECASE)

# a registry reader: (root name "HKCU"/"HKLM", key) -> values, or None when the key doesn't exist
RegistryReader = Callable[[str, str], "dict[str, str] | None"]
# lists the subkeys of a registry key, or [] when it doesn't exist
RegistryLister = Callable[[str, str], "list[str]"]


@dataclass(frozen=True)
class GameInstall:
    folder: str  # the game's root folder, with forward slashes
    store: str  # "Steam", "GOG" or "" when found by other means

    @property
    def label(self) -> str:
        return f"{self.store}: {self.folder}" if self.store else self.folder


def readRegistry(root: str, key: str) -> "dict[str, str] | None":
    if sys.platform != 'win32':
        return None
    import winreg

    roots = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    for access in (winreg.KEY_READ | winreg.KEY_WOW64_32KEY, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(roots[root], key, 0, access) as handle:
                values = {}
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(handle, index)
                    except OSError:
                        return values
                    values[name] = str(value)
                    index += 1
        except OSError:
            continue
    return None


def listRegistry(root: str, key: str) -> list[str]:
    if sys.platform != 'win32':
        return []
    import winreg

    roots = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    names: list[str] = []
    for access in (winreg.KEY_READ | winreg.KEY_WOW64_32KEY, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(roots[root], key, 0, access) as handle:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(handle, index)
                    except OSError:
                        break
                    if name not in names:
                        names.append(name)
                    index += 1
        except OSError:
            continue
    return names


def steamLibraries(steamPath: str) -> list[str]:
    '''The Steam library folders listed in libraryfolders.vdf, the Steam folder itself first'''
    libraries = [steamPath] if steamPath else []
    try:
        with open(path.join(steamPath, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as file:
            text = file.read()
    except OSError:
        return libraries
    for match in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', text):
        library = match.group(1).replace("\\\\", "\\")
        if library and library not in libraries:
            libraries.append(library)
    return libraries


def steamInstalls(read: RegistryReader = readRegistry) -> list[GameInstall]:
    steam = read("HKCU", r"Software\Valve\Steam") or {}
    steamPath = steam.get("SteamPath") or (read("HKLM", r"SOFTWARE\Valve\Steam") or {}).get("InstallPath", "")
    found = []
    for library in steamLibraries(steamPath):
        if not path.isfile(path.join(library, "steamapps", f"appmanifest_{STEAM_APP_ID}.acf")):
            continue
        folder = path.join(library, "steamapps", "common", STEAM_FOLDER)
        if path.isdir(folder):
            found.append(GameInstall(normalizePath(folder), "Steam"))
    return found


def gogInstalls(read: RegistryReader = readRegistry, subkeys: RegistryLister = listRegistry) -> list[GameInstall]:
    key = r"SOFTWARE\GOG.com\Games"
    found = []
    for gameId in subkeys("HKLM", key):
        values = read("HKLM", key + "\\" + gameId) or {}
        if not GOG_NAME.search(values.get("gameName", "")):
            continue
        folder = values.get("path", "")
        if folder and path.isdir(folder):
            found.append(GameInstall(normalizePath(folder), "GOG"))
    return found


def findGameInstalls(
    read: RegistryReader = readRegistry,
    subkeys: RegistryLister = listRegistry,
    valid: Callable[[str], str] = lambda folder: folder,
) -> list[GameInstall]:
    '''Installed copies of the game that `valid` accepts (it gets a folder and returns "" to reject it), without
    duplicates, Steam first'''
    found: list[GameInstall] = []
    seen = set()
    for install in steamInstalls(read) + gogInstalls(read, subkeys):
        key = path.normcase(install.folder)
        if key in seen or not valid(install.folder):
            continue
        seen.add(key)
        found.append(install)
    return found


def nexusApiKeyPage() -> str:
    return "https://next.nexusmods.com/settings/api-keys"
