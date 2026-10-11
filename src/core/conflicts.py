'''Files that more than one mod packs into its bundles, and which mod the game takes each one from'''

import os
import struct
from collections.abc import Callable
from dataclasses import dataclass, field

from src.core.scriptmerger import MERGED_FOLDER

BUNDLE_MAGIC = b'POTATO70'
HEADER_SIZE = 32
ENTRY_SIZE = 304  # a 256 byte path, then sizes, offsets and checksums
NAME_SIZE = 256

# every bundle has these, they never override anything
IGNORED = {'strings.list', 'metadata.store'}

# mods without a priority in mods.settings load after all that have one
NO_PRIORITY = 16777215

# text files Script Merger can merge when they come from bundles
MERGEABLE = ('.xml', '.csv')


class BundleError(Exception):
    pass


def bundleFiles(path: str) -> list[str]:
    '''The game paths packed into a .bundle file (read from its table of contents only)'''
    try:
        with open(path, 'rb') as file:
            header = file.read(HEADER_SIZE)
            if len(header) < HEADER_SIZE or header[:8] != BUNDLE_MAGIC:
                raise BundleError(f"{path} is not a Witcher 3 bundle")
            tocSize = struct.unpack('<I', header[16:20])[0]
            toc = file.read(tocSize)
    except OSError as error:
        raise BundleError(f"Couldn't read {path}: {error}") from error
    names = []
    for offset in range(0, len(toc) - ENTRY_SIZE + 1, ENTRY_SIZE):
        name = toc[offset : offset + NAME_SIZE].split(b'\0', 1)[0].decode('utf-8', 'replace').strip()
        if name:
            names.append(name.replace('/', '\\'))
    return names


def loadOrder(modsFolder: str, priority: Callable[[str], str | None]) -> list[str]:
    '''Enabled mod folders in the order the game loads them; the first one wins a conflict'''
    if not modsFolder or not os.path.isdir(modsFolder):
        return []
    folders = [
        name
        for name in os.listdir(modsFolder)
        if name.lower().startswith('mod') and os.path.isdir(os.path.join(modsFolder, name))
    ]

    def key(name: str) -> tuple[int, str]:
        value = priority(name)
        try:
            number = int(value) if value is not None and str(value).strip() != '' else NO_PRIORITY
        except ValueError:
            number = NO_PRIORITY
        return number, name.casefold()

    return sorted(folders, key=key)


@dataclass
class FileConflict:
    path: str
    folders: list[str]  # in load order, the first one is used by the game
    merged: bool = False  # Script Merger's merged files include it

    @property
    def winner(self) -> str:
        return self.folders[0]

    @property
    def losers(self) -> list[str]:
        return self.folders[1:]

    @property
    def mergeable(self) -> bool:
        return self.path.lower().endswith(MERGEABLE)


@dataclass
class ConflictScan:
    conflicts: list[FileConflict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    bundles: int = 0

    def pairs(self) -> dict[tuple[str, str], list[FileConflict]]:
        '''(winning folder, losing folder) -> the files it takes from the winner instead'''
        found: dict[tuple[str, str], list[FileConflict]] = {}
        for conflict in self.conflicts:
            for loser in conflict.losers:
                found.setdefault((conflict.winner, loser), []).append(conflict)
        return found


def findConflicts(modsFolder: str, order: list[str]) -> ConflictScan:
    '''Reads the bundles of the given mod folders and lists the game paths that more than one of them packs'''
    scan = ConflictScan()
    owners: dict[str, tuple[str, list[str]]] = {}
    merged: set[str] = set()
    for folder in order:
        content = os.path.join(modsFolder, folder, 'content')
        if not os.path.isdir(content):
            continue
        seen: set[str] = set()
        for name in sorted(os.listdir(content)):
            if not name.lower().endswith('.bundle'):
                continue
            scan.bundles += 1
            try:
                files = bundleFiles(os.path.join(content, name))
            except BundleError as error:
                scan.errors.append(str(error))
                continue
            for path in files:
                key = path.lower()
                if key in IGNORED or key in seen:
                    continue
                seen.add(key)
                if folder.casefold() == MERGED_FOLDER.casefold():
                    merged.add(key)
                    continue
                owners.setdefault(key, (path, []))[1].append(folder)
    for key, (path, folders) in sorted(owners.items()):
        if len(folders) > 1:
            scan.conflicts.append(FileConflict(path, folders, key in merged))
    return scan
