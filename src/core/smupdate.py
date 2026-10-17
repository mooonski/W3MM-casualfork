'''Updating Script Merger in its own folder from a downloaded Nexus archive, keeping its settings and merge inventory'''

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from os import path
from tempfile import TemporaryDirectory
from typing import Callable

from src.core.fetcher import extractArchive
from src.core.scriptmerger import EXECUTABLE, INVENTORY, installedVersion

BACKUP_FOLDER = '~Script Merger'
MANIFEST = 'w3mm-backup.json'

# Script Merger's own state: never replaced by an update, only added when it doesn't exist yet
KEEP_FILES = {INVENTORY.casefold(), 'witcherscriptmerger.dll.config'}
KEEP_SUFFIXES = ('.settings.xml',)
KEEP_FOLDERS = {'data'}


class ScriptMergerUpdateFailed(Exception):
    pass


@dataclass
class ScriptMergerUpdate:
    previous: str
    version: str
    backup: str
    replaced: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)


def isKept(relative: str) -> bool:
    '''True for Script Merger's settings, merge inventory and cache, which an update must not replace'''
    parts = relative.replace('\\', '/').split('/')
    name = parts[-1].casefold()
    if len(parts) > 1:
        return parts[0].casefold() in KEEP_FOLDERS
    return name in KEEP_FILES or name.endswith(KEEP_SUFFIXES)


def programRoot(folder: str) -> str:
    '''The folder in an extracted archive that holds WitcherScriptMerger.exe, the shallowest one. "" if none.'''
    found = []
    for root, _, files in os.walk(folder):
        if any(name.casefold() == EXECUTABLE.casefold() for name in files):
            found.append(root)
    return min(found, key=lambda root: (root.count(os.sep), root)) if found else ''


def programFiles(root: str) -> list[str]:
    '''Every file under root, as relative paths with forward slashes'''
    files = []
    for folder, _, names in os.walk(root):
        for name in names:
            files.append(path.relpath(path.join(folder, name), root).replace('\\', '/'))
    return sorted(files, key=str.casefold)


def inUse(folder: str) -> bool:
    '''True when Script Merger is running from folder: Windows locks a running program's files against writing'''
    for name in (EXECUTABLE, 'WitcherScriptMerger.dll'):
        target = path.join(folder, name)
        if not path.isfile(target):
            continue
        try:
            with open(target, 'r+b'):
                pass
        except PermissionError:
            return True
        except OSError:
            continue
    return False


def updateScriptMerger(
    executable: str,
    archive: str,
    backups: str,
    extract: Callable[[str, str], str] = extractArchive,
    version: Callable[[str], str] = installedVersion,
) -> ScriptMergerUpdate:
    '''Copies the program files from archive over the Script Merger that executable belongs to.
    Files it replaces are copied to backups/~Script Merger/<old version> <time> first, and put back if anything fails.
    Files the new version no longer has are left alone.'''
    folder = path.dirname(executable)
    if not executable or not path.isfile(executable):
        raise ScriptMergerUpdateFailed(f"Script Merger wasn't found at {executable or '(no path set)'}")
    if not path.isfile(archive):
        raise ScriptMergerUpdateFailed(f"The download is missing: {archive}")
    previous = version(executable)
    with TemporaryDirectory(prefix='tw3mm-sm-') as extraction:
        try:
            extract(archive, extraction)
        except Exception as error:
            raise ScriptMergerUpdateFailed(f"Couldn't extract {path.basename(archive)}: {error}") from error
        root = programRoot(extraction)
        if not root:
            raise ScriptMergerUpdateFailed(f"{path.basename(archive)} doesn't contain {EXECUTABLE}")
        if inUse(folder):
            raise ScriptMergerUpdateFailed("Script Merger is open. Close it and try again.")
        stamp = time.strftime('%Y-%m-%d %H-%M-%S')
        backup = path.normpath(path.join(backups, BACKUP_FOLDER, f"{previous or 'unknown version'} {stamp}"))
        result = ScriptMergerUpdate(previous, '', backup)
        incoming = programFiles(root)
        for relative in incoming:
            target = path.join(folder, relative)
            if path.isfile(target):
                (result.kept if isKept(relative) else result.replaced).append(relative)
            else:
                result.added.append(relative)
        try:
            for relative in result.replaced:
                destination = path.join(backup, relative)
                os.makedirs(path.dirname(destination), exist_ok=True)
                shutil.copy2(path.join(folder, relative), destination)
            writeManifest(result, folder, archive)
        except OSError as error:
            raise ScriptMergerUpdateFailed(f"Couldn't back up Script Merger to {backup}: {error}") from error
        copied: list[str] = []
        try:
            for relative in result.replaced + result.added:
                destination = path.join(folder, relative)
                os.makedirs(path.dirname(destination), exist_ok=True)
                shutil.copy2(path.join(root, relative), destination)
                copied.append(relative)
        except OSError as error:
            problems = rollback(result, folder, copied)
            message = f"Couldn't update Script Merger: {error}. The previous version was put back"
            if problems:
                message += f", except for {', '.join(problems)} (copies are in {backup})"
            raise ScriptMergerUpdateFailed(message) from error
    result.version = version(executable)
    return result


def writeManifest(result: ScriptMergerUpdate, folder: str, archive: str) -> None:
    os.makedirs(result.backup, exist_ok=True)
    manifest = {
        'version': result.previous,
        'folder': folder,
        'archive': path.basename(archive),
        'replaced': result.replaced,
        'added': result.added,
    }
    with open(path.join(result.backup, MANIFEST), 'w', encoding='utf-8') as file:
        json.dump(manifest, file, indent=1)


def rollback(result: ScriptMergerUpdate, folder: str, copied: list[str]) -> list[str]:
    '''Puts back the files an interrupted update replaced and takes out the ones it added. Returns what failed.'''
    problems = []
    added = set(result.added)
    for relative in copied:
        target = path.join(folder, relative)
        try:
            if relative in added:
                os.remove(target)  # this update's own copy, nothing was there before
            else:
                shutil.copy2(path.join(result.backup, relative), target)
        except OSError:
            problems.append(relative)
    return problems
