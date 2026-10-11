'''Finds mod archives in the archive folder that are no longer needed, and moves them out of the way'''

import os
import re
import shutil
import sys
from dataclasses import dataclass

from src.core.model import Model
from src.core.nexus import SAME_LINE_SIMILARITY, archiveUploadTime, nameKey, parseArchiveName, similarity, uniquePath

ARCHIVE_EXTENSIONS = ('.zip', '.7z', '.rar')

# what an archive is
IN_USE = 'inuse'  # the archive an installed mod came from
OLDER = 'older'  # an older version of the file an installed mod uses
DUPLICATE = 'duplicate'  # an identical copy saved under a numbered name
PARTIAL = 'partial'  # an unfinished download
OTHER_FILE = 'other'  # another file from the Nexus page of an installed mod
NOT_INSTALLED = 'notinstalled'  # from Nexus, but no installed mod comes from that page
UNKNOWN = 'unknown'  # not a Nexus download name and no installed mod looks like it

# Nexus pages of tools rather than mods, so their downloads are named as such
TOOLS = {12584: 'RMMR (mod manager)'}

# only these are ticked for removal to begin with
SUGGESTED = (OLDER, DUPLICATE, PARTIAL)

COPY_NUMBER = re.compile(r'^(?P<stem>.+) \(\d+\)(?P<extension>\.[^.]+)$')


@dataclass
class ArchiveInfo:
    path: str
    size: int
    state: str
    mod: str = ''  # the installed mod it belongs to
    note: str = ''
    onlyCopy: bool = False  # the installed version's archive isn't in the folder, so this is the only one left

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def suggested(self) -> bool:
        return self.state in SUGGESTED and not self.onlyCopy


def _nameLine(archive: str) -> str:
    '''The file name part of a Nexus download name, without mod ID, version and date'''
    parsed = parseArchiveName(archive)
    return nameKey(parsed[0]) if parsed else nameKey(os.path.splitext(archive)[0])


def scanArchives(folder: str, model: Model, tools: dict[int, str] | None = None) -> list[ArchiveInfo]:
    '''Sorts the archives directly in folder by whether they are still needed. Only reads.'''
    if not folder or not os.path.isdir(folder):
        return []
    tools = {**TOOLS, **(tools or {})}
    mods = list(model.all())
    inUse: dict[str, str] = {}
    byModId: dict[int, list] = {}
    for mod in mods:
        if mod.nexus and mod.nexus.archive:
            inUse.setdefault(os.path.basename(mod.nexus.archive).casefold(), mod.name)
        if mod.nexus and mod.nexus.modId:
            byModId.setdefault(mod.nexus.modId, []).append(mod)

    names = sorted(
        (entry.name for entry in os.scandir(folder) if entry.is_file()),
        key=str.casefold,
    )
    present = {name.casefold() for name in names}
    found: list[ArchiveInfo] = []
    for name in names:
        lower = name.casefold()
        full = os.path.join(folder, name)
        if lower.endswith('.part'):
            found.append(ArchiveInfo(full, _size(full), PARTIAL, note='Left over from a download that didn\'t finish'))
            continue
        if not lower.endswith(ARCHIVE_EXTENSIONS):
            continue
        size = _size(full)
        if lower in inUse:
            found.append(ArchiveInfo(full, size, IN_USE, inUse[lower]))
            continue
        copy = COPY_NUMBER.match(name)
        if copy:
            original = copy.group('stem') + copy.group('extension')
            owner = inUse.get(original.casefold(), '')
            if original.casefold() in present and _size(os.path.join(folder, original)) == size:
                found.append(ArchiveInfo(full, size, DUPLICATE, owner, f"Same as {original}"))
                continue
            if owner:
                note = f"A different file saved under the name of the archive {owner} was installed from"
                found.append(ArchiveInfo(full, size, OTHER_FILE, owner, note))
                continue
        else:
            original = name
        found.append(_classify(full, original, size, byModId, mods, tools, present))
    return found


def _classify(
    full: str, name: str, size: int, byModId: dict[int, list], mods: list, tools: dict[int, str], present: set[str]
) -> ArchiveInfo:
    '''Classifies an archive by its name (without any " (2)" copy number)'''
    parsed = parseArchiveName(name)
    if parsed is None:
        key = nameKey(os.path.splitext(name)[0])
        for mod in mods:
            if key and similarity(key, nameKey(mod.name)) >= SAME_LINE_SIMILARITY:
                return ArchiveInfo(full, size, UNKNOWN, mod.name, f"Probably what {mod.name} was installed from")
        return ArchiveInfo(full, size, UNKNOWN, note='No installed mod looks like it')
    _, modId, version = parsed
    installed = byModId.get(modId) or []
    if not installed:
        note = f"{tools[modId]} download" if modId in tools else 'No installed mod comes from this Nexus page'
        return ArchiveInfo(full, size, NOT_INSTALLED, note=note)
    line = _nameLine(name)
    uploaded = archiveUploadTime(name)
    for mod in installed:
        current = os.path.basename(mod.nexus.archive or '')
        if not current:
            continue
        sameLine = similarity(line, _nameLine(current)) >= SAME_LINE_SIMILARITY
        currentUpload = archiveUploadTime(current) or mod.nexus.uploaded
        if sameLine and uploaded and currentUpload and uploaded < currentUpload:
            installedVersion = mod.nexus.version or '?'
            note = f"Version {version}, {installedVersion} is installed"
            onlyCopy = current.casefold() not in present
            if onlyCopy:
                note += " (its archive isn't in this folder, so this is the only copy)"
            return ArchiveInfo(full, size, OLDER, mod.name, note, onlyCopy)
    return ArchiveInfo(
        full, size, OTHER_FILE, installed[0].name, 'Another file from the Nexus page of an installed mod'
    )


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def moveToFolder(paths: list[str], target: str) -> list[tuple[str, str]]:
    '''Moves files into target (never overwriting). Returns (path, error) for the ones that failed.'''
    failed = []
    os.makedirs(target, exist_ok=True)
    for path in paths:
        try:
            shutil.move(path, uniquePath(target, os.path.basename(path)))
        except OSError as error:
            failed.append((path, str(error)))
    return failed


def recycle(paths: list[str]) -> list[tuple[str, str]]:
    '''Moves files to the Windows Recycle Bin, where they can be restored. Returns (path, error) for failures.'''
    if sys.platform != 'win32':
        return [(path, 'The Recycle Bin is only available on Windows') for path in paths]
    from win32com.shell import shell, shellcon  # type: ignore

    flags = shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI
    failed = []
    for path in paths:
        try:
            result, aborted = shell.SHFileOperation((0, shellcon.FO_DELETE, os.path.abspath(path), None, flags))
        except Exception as error:
            failed.append((path, str(error)))
            continue
        if result or aborted or os.path.exists(path):
            failed.append((path, f"Windows couldn't recycle it (code {result})"))
    return failed
