'''ReShade in the game's bin folders: which build and version is installed, turning it off and on by renaming
its DLL, and updating that DLL from the setup program on reshade.me'''

import os
import re
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from os import path
from typing import Any

from src.core.scriptmerger import installedVersion, isNewer
from src.globals.constants import VERSION

HOME_PAGE = "https://reshade.me/"
DISABLED = ".disabled"
BACKUP_FOLDER = "~ReShade"
BIN_FOLDERS = ("x64_dx12", "x64")
# the names ReShade's setup gives its DLL, depending on the graphics API it was installed for
PROXY_NAMES = ("dxgi.dll", "d3d11.dll", "d3d12.dll", "d3d10.dll", "d3d9.dll", "opengl32.dll")
ADDON_SUFFIXES = (".addon64", ".addon")
SETUP_DLL = "ReShade64.dll"

# ReShade's version resource names its author; plain proxy DLLs from other mods don't
MARKER = "crosire's ReShade".encode("utf-16-le")
# only in the standard build, which refuses to load add-ons other than a few approved ones
LIMITED_ADDONS = b"only limited add-on functionality"

ADDON_BUILD = "addon"
STANDARD_BUILD = "standard"

USER_AGENT = f"TW3MM/{VERSION}"


class ReShadeError(Exception):
    pass


@dataclass
class ReShadeInstall:
    folder: str  # the bin folder, e.g. <game>/bin/x64_dx12
    name: str  # the DLL's name when enabled, e.g. dxgi.dll
    enabled: bool
    version: str
    build: str
    addons: list[str] = field(default_factory=list)  # add-on files beside it, which only load through it
    duplicate: bool = False  # both name and name.disabled exist

    @property
    def path(self) -> str:
        return path.join(self.folder, self.name if self.enabled else self.name + DISABLED)

    @property
    def binFolder(self) -> str:
        return path.basename(self.folder)

    @property
    def buildName(self) -> str:
        return "Add-on build" if self.build == ADDON_BUILD else "Standard build"


@dataclass
class LatestReShade:
    version: str
    standardUrl: str
    addonUrl: str

    def url(self, build: str) -> str:
        return (self.addonUrl if build == ADDON_BUILD else self.standardUrl) or self.standardUrl


@dataclass
class ReShadeUpdate:
    previous: str
    version: str
    target: str
    backup: str


def readBuild(data: bytes) -> str:
    return STANDARD_BUILD if LIMITED_ADDONS in data else ADDON_BUILD


def isReShade(file: str) -> tuple[bool, str]:
    '''Whether file is a ReShade DLL, and which build it is'''
    try:
        with open(file, "rb") as handle:
            data = handle.read()
    except OSError:
        return False, ""
    if MARKER not in data:
        return False, ""
    return True, readBuild(data)


def addonFiles(folder: str) -> list[str]:
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted((name for name in names if name.casefold().endswith(ADDON_SUFFIXES)), key=str.casefold)


def findReShade(game: str, version: Callable[[str], str] = installedVersion) -> list[ReShadeInstall]:
    '''ReShade DLLs in the game's bin folders, enabled or renamed to .disabled'''
    if not game:
        return []
    found = []
    for binFolder in BIN_FOLDERS:
        folder = path.join(game, "bin", binFolder)
        if not path.isdir(folder):
            continue
        for name in PROXY_NAMES:
            copies = []
            for enabled, file in ((True, path.join(folder, name)), (False, path.join(folder, name + DISABLED))):
                if path.isfile(file):
                    reshade, build = isReShade(file)
                    if reshade:
                        copies.append((enabled, file, build))
            if not copies:
                continue
            enabled, file, build = copies[0]
            found.append(
                ReShadeInstall(folder, name, enabled, version(file), build, addonFiles(folder), len(copies) > 1)
            )
    return found


def setEnabled(install: ReShadeInstall, enabled: bool, running: Callable[[], bool]) -> ReShadeInstall:
    '''Renames the DLL to name or name.disabled. Its settings, presets and shaders aren't touched.'''
    if install.enabled == enabled:
        return install
    if running():
        # Windows lets a loaded DLL be renamed, so the game wouldn't stop it; it just keeps using the old file
        raise ReShadeError("The game is running. Close it first.")
    source = install.path
    target = path.join(install.folder, install.name if enabled else install.name + DISABLED)
    if path.exists(target):
        raise ReShadeError(f"{path.basename(target)} already exists in {install.folder}; one copy is left over")
    try:
        os.rename(source, target)
    except OSError as error:
        raise ReShadeError(f"Couldn't rename {path.basename(source)}: {error}") from error
    install.enabled = enabled
    return install


def fetchPage(url: str = HOME_PAGE, opener: Callable[..., Any] = urllib.request.urlopen, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as error:
        raise ReShadeError(f"Couldn't reach {url}: {error}") from error


def parseLatest(page: str, base: str = HOME_PAGE) -> LatestReShade | None:
    '''The newest setup links on reshade.me's front page'''
    standard: dict[str, str] = {}
    addon: dict[str, str] = {}
    for link, number, isAddon in re.findall(r'href="([^"]*ReShade_Setup_(\d+(?:\.\d+)+)(_Addon)?\.exe)"', page):
        url = urllib.request.urljoin(base, link)
        (addon if isAddon else standard).setdefault(number, url)
    versions = set(standard) | set(addon)
    if not versions:
        return None
    newest = max(versions, key=lambda number: tuple(int(part) for part in number.split(".")))
    return LatestReShade(newest, standard.get(newest, ""), addon.get(newest, ""))


def latestReShade(fetch: Callable[[], str] = fetchPage) -> LatestReShade:
    latest = parseLatest(fetch())
    if latest is None:
        raise ReShadeError(f"{HOME_PAGE} doesn't list a ReShade download")
    return latest


def needsUpdate(install: ReShadeInstall, latest: LatestReShade) -> bool:
    return isNewer(latest.version, install.version)


def download(
    url: str,
    directory: str,
    progress: Callable[[int, int], Any] = lambda done, total: None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> str:
    '''Downloads a setup program from reshade.me into directory'''
    os.makedirs(directory, exist_ok=True)
    target = path.join(directory, url.rsplit("/", 1)[-1] or "ReShade_Setup.exe")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": HOME_PAGE})
    done = 0
    try:
        with opener(request, timeout=60) as response, open(target, "wb") as output:
            total = int(response.headers.get("Content-Length") or 0)
            while chunk := response.read(256 * 1024):
                output.write(chunk)
                done += len(chunk)
                progress(done, total)
        if total and done != total:
            raise ReShadeError(f"Download incomplete ({done} of {total} bytes)")
    except (urllib.error.URLError, OSError) as error:
        raise ReShadeError(f"Couldn't download {url}: {error}") from error
    return target


def setupDll(setup: str) -> bytes:
    '''The 64-bit ReShade DLL from a setup program, which carries its files as a zip archive at its end'''
    try:
        with zipfile.ZipFile(setup) as archive:
            names = {name.casefold(): name for name in archive.namelist()}
            if SETUP_DLL.casefold() not in names:
                raise ReShadeError(f"{path.basename(setup)} doesn't contain {SETUP_DLL}")
            return archive.read(names[SETUP_DLL.casefold()])
    except (zipfile.BadZipFile, OSError) as error:
        raise ReShadeError(f"Couldn't read {path.basename(setup)}: {error}") from error


def updateReShade(
    install: ReShadeInstall,
    setup: str,
    backups: str,
    running: Callable[[], bool],
    version: Callable[[str], str] = installedVersion,
) -> ReShadeUpdate:
    '''Replaces the installed DLL (under its own name, enabled or not) with the one in setup.
    The old DLL is copied to backups/~ReShade/<old version> <time>/<bin folder> first.'''
    if running():
        raise ReShadeError("The game is running. Close it first.")
    dll = setupDll(setup)
    if MARKER not in dll:
        raise ReShadeError(f"{SETUP_DLL} in {path.basename(setup)} isn't ReShade")
    if readBuild(dll) != install.build:
        raise ReShadeError(
            f"{path.basename(setup)} has the {'Add-on' if readBuild(dll) == ADDON_BUILD else 'Standard'} build, "
            f"but the {install.buildName} is installed"
        )
    target = install.path
    staged = target + ".w3mm-new"
    try:
        with open(staged, "wb") as file:
            file.write(dll)
    except OSError as error:
        raise ReShadeError(f"Couldn't write to {install.folder}: {error}") from error
    try:
        newVersion = version(staged)
        stamp = time.strftime("%Y-%m-%d %H-%M-%S")
        backup = path.normpath(
            path.join(backups, BACKUP_FOLDER, f"{install.version or 'unknown version'} {stamp}", install.binFolder)
        )
        try:
            os.makedirs(backup, exist_ok=True)
            shutil.copy2(target, path.join(backup, path.basename(target)))
        except OSError as error:
            raise ReShadeError(f"Couldn't back up {path.basename(target)} to {backup}: {error}") from error
        try:
            os.replace(staged, target)
        except OSError as error:
            raise ReShadeError(f"Couldn't replace {path.basename(target)}: {error}") from error
    finally:
        if path.exists(staged):
            try:
                os.remove(staged)  # this update's own temporary copy
            except OSError:
                pass
    previous = install.version
    install.version = newVersion
    return ReShadeUpdate(previous, newVersion, target, backup)
