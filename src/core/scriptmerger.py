'''Script Merger: finding script conflicts, reading its merge inventory, locating it and its version'''

import configparser
import os
import re
import struct
import sys
import xml.etree.ElementTree as XML
from dataclasses import dataclass, field
from os import path

EXECUTABLE = "WitcherScriptMerger.exe"
INVENTORY = "MergeInventory.xml"
MERGED_FOLDER = "mod0000_MergedFiles"

# Nexus mod ids of Script Merger, by game version ('re' is Remastered 5.x)
NEXUS_IDS = {"re": 13076}
CLASSIC_NEXUS_ID = 8405

MERGED = "merged"
NOT_MERGED = "notmerged"
OUTDATED = "outdated"
LEFTOVER = "leftover"

_P1, _P2, _P3, _P4, _P5 = 2654435761, 2246822519, 3266489917, 668265263, 374761393
_MASK = 0xFFFFFFFF


def _rotl(value: int, bits: int) -> int:
    return ((value << bits) | (value >> (32 - bits))) & _MASK


def xxh32(data: bytes, seed: int = 0) -> int:
    '''xxHash32, the hash Script Merger records for each merged file'''
    length = len(data)
    index = 0
    if length >= 16:
        lanes = [(seed + _P1 + _P2) & _MASK, (seed + _P2) & _MASK, seed & _MASK, (seed - _P1) & _MASK]
        limit = length - 16
        while index <= limit:
            for lane in range(4):
                (value,) = struct.unpack_from("<I", data, index)
                index += 4
                lanes[lane] = (_rotl((lanes[lane] + value * _P2) & _MASK, 13) * _P1) & _MASK
        result = (_rotl(lanes[0], 1) + _rotl(lanes[1], 7) + _rotl(lanes[2], 12) + _rotl(lanes[3], 18)) & _MASK
    else:
        result = (seed + _P5) & _MASK
    result = (result + length) & _MASK
    while index + 4 <= length:
        (value,) = struct.unpack_from("<I", data, index)
        result = (_rotl((result + value * _P3) & _MASK, 17) * _P4) & _MASK
        index += 4
    while index < length:
        result = (_rotl((result + data[index] * _P5) & _MASK, 11) * _P1) & _MASK
        index += 1
    result ^= result >> 15
    result = (result * _P2) & _MASK
    result ^= result >> 13
    result = (result * _P3) & _MASK
    result ^= result >> 16
    return result


def fileHash(filename: str) -> str:
    with open(filename, "rb") as file:
        return f"{xxh32(file.read()):08X}"


@dataclass
class Merge:
    '''One merged file in Script Merger's inventory'''

    relativePath: str
    included: dict[str, str]  # mod folder -> hash of its file when merged
    mergedMod: str = MERGED_FOLDER


@dataclass
class ScriptConflict:
    relativePath: str
    mods: list[str]
    state: str
    detail: str = ''
    merge: Merge | None = None


@dataclass
class ConflictReport:
    conflicts: list[ScriptConflict] = field(default_factory=list)
    inventoryFound: bool = False

    @property
    def pending(self) -> list[ScriptConflict]:
        '''Conflicts that need Script Merger to be run'''
        return [conflict for conflict in self.conflicts if conflict.state != MERGED]


def pathKey(relativePath: str) -> str:
    return relativePath.replace("/", "\\").strip("\\").casefold()


def readInventory(folder: str) -> list[Merge] | None:
    '''Reads Script Merger's MergeInventory.xml, or None if there isn't one'''
    filename = path.join(folder, INVENTORY)
    if not path.isfile(filename):
        return None
    try:
        root = XML.parse(filename).getroot()
    except (OSError, XML.ParseError) as error:
        print("could not read Script Merger inventory:", error, file=sys.stderr)
        return None
    merges = []
    for element in root.findall("Merge"):
        relative = (element.findtext("RelativePath") or '').strip()
        if not relative:
            continue
        included = {
            (mod.text or '').strip(): (mod.get("Hash") or '').upper()
            for mod in element.findall("IncludedMod")
            if (mod.text or '').strip()
        }
        merges.append(Merge(relative, included, (element.findtext("MergedModName") or MERGED_FOLDER).strip()))
    return merges


def scriptFiles(modsFolder: str, ignore: set[str]) -> dict[str, list[tuple[str, str, str]]]:
    '''Scripts of enabled mods: path key -> [(mod folder, relative path, file)]'''
    found: dict[str, list[tuple[str, str, str]]] = {}
    try:
        folders = sorted(os.listdir(modsFolder), key=str.casefold)
    except OSError:
        return found
    for folder in folders:
        if folder.startswith("~") or folder.casefold() in ignore:
            continue
        scripts = path.join(modsFolder, folder, "content", "scripts")
        if not path.isdir(scripts):
            continue
        for directory, _, files in os.walk(scripts):
            for name in files:
                if not name.casefold().endswith(".ws"):
                    continue
                full = path.join(directory, name)
                relative = path.relpath(full, scripts)
                found.setdefault(pathKey(relative), []).append((folder, relative, full))
    return found


def analyse(modsFolder: str, scriptMergerFolder: str = '') -> ConflictReport:
    '''Finds scripts changed by more than one enabled mod and compares them with Script Merger's merges'''
    merges = readInventory(scriptMergerFolder) if scriptMergerFolder else None
    report = ConflictReport(inventoryFound=merges is not None)
    merges = merges or []
    ignore = {MERGED_FOLDER.casefold()} | {merge.mergedMod.casefold() for merge in merges}
    files = scriptFiles(modsFolder, ignore)
    byPath = {pathKey(merge.relativePath): merge for merge in merges}

    for key, entries in sorted(files.items()):
        if len(entries) < 2:
            continue
        mods = [folder for folder, _, _ in entries]
        relative = entries[0][1]
        merge = byPath.get(key)
        if merge is None:
            report.conflicts.append(ScriptConflict(relative, mods, NOT_MERGED, "not merged", None))
            continue
        included = {name.casefold(): value for name, value in merge.included.items()}
        missing = [folder for folder in mods if folder.casefold() not in included]
        changed = [
            folder for folder, _, full in entries if included.get(folder.casefold(), '') not in ('', fileHash(full))
        ]
        gone = sorted(set(included) - {folder.casefold() for folder in mods})
        mergedFile = path.join(modsFolder, merge.mergedMod, "content", "scripts", merge.relativePath)
        if missing:
            state, detail = NOT_MERGED, "not in the merge: " + ", ".join(missing)
        elif changed:
            state, detail = OUTDATED, "changed since the merge: " + ", ".join(changed)
        elif not path.isfile(mergedFile):
            state, detail = OUTDATED, "the merged file is missing"
        elif gone:
            state, detail = OUTDATED, "the merge includes mods no longer enabled: " + ", ".join(gone)
        else:
            state, detail = MERGED, "merged"
        report.conflicts.append(ScriptConflict(relative, mods, state, detail, merge))

    for key, merge in sorted(byPath.items()):
        if key in files and len(files[key]) >= 2:
            continue
        remaining = [folder for folder, _, _ in files.get(key, [])]
        report.conflicts.append(
            ScriptConflict(
                merge.relativePath,
                remaining,
                LEFTOVER,
                "merged, but the mods no longer conflict; run Script Merger to remove the merge",
                merge,
            )
        )
    return report


# ---------------------------------------------------------------- finding Script Merger


def candidateFolders(game: str, archives: str, rmmrFolder: str) -> list[str]:
    folders = []
    if rmmrFolder:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(path.join(rmmrFolder, "config.ini"), encoding="utf-8")
            configured = parser.get("PATHS", "scriptmerger", fallback='')
        except (OSError, configparser.Error):
            configured = ''
        if configured:
            folders.append(path.dirname(configured))
    for base in (archives, game, path.dirname(game) if game else ''):
        if base:
            folders.append(base)
    return folders


def findScriptMerger(game: str, archives: str, rmmrFolder: str = '', depth: int = 2) -> str:
    '''Looks for WitcherScriptMerger.exe in the usual places. Returns its path or "".'''
    seen = set()
    for folder in candidateFolders(game, archives, rmmrFolder):
        found = _search(folder, depth, seen)
        if found:
            return found.replace("\\", "/")
    return ''


def _search(folder: str, depth: int, seen: set[str]) -> str:
    key = path.normcase(path.abspath(folder))
    if key in seen or not path.isdir(folder):
        return ''
    seen.add(key)
    candidate = path.join(folder, EXECUTABLE)
    if path.isfile(candidate):
        return candidate
    if depth <= 0:
        return ''
    try:
        entries = sorted(os.listdir(folder), key=str.casefold)
    except OSError:
        return ''
    # folders that obviously hold game data are skipped, they're large and never contain Script Merger
    skip = {"content", "dlc", "mods", "bin", "dlc-tombstones", "artbook", "soundtrack", "wallpapers"}
    for entry in entries:
        child = path.join(folder, entry)
        if entry.casefold() not in skip and path.isdir(child):
            found = _search(child, depth - 1, seen)
            if found:
                return found
    return ''


def installedVersion(executable: str) -> str:
    '''Script Merger's version from its executable, e.g. "1.4"'''
    if not executable or not path.isfile(executable) or sys.platform != "win32":
        return ''
    try:
        import win32api  # type: ignore

        info = win32api.GetFileVersionInfo(executable, "\\")
        major, minor = info["FileVersionMS"] >> 16, info["FileVersionMS"] & 0xFFFF
        build, revision = info["FileVersionLS"] >> 16, info["FileVersionLS"] & 0xFFFF
    except Exception:
        return ''
    parts = [major, minor, build, revision]
    while len(parts) > 2 and parts[-1] == 0:
        parts.pop()
    return ".".join(str(part) for part in parts)


def nexusId(gameversion: str) -> int:
    return NEXUS_IDS.get(gameversion, CLASSIC_NEXUS_ID)


def latestScriptMerger(files):
    '''The newest main download on Script Merger's Nexus page, leaving out its source code. None if there's none.'''
    current = [file for file in files.currentFiles() if "source code" not in file.name.casefold()]
    main = [file for file in current if file.category == "MAIN"] or current
    return max(main, key=lambda file: file.uploaded) if main else None


def versionKey(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def isNewer(available: str, installed: str) -> bool:
    if not available or not installed:
        return False
    newer, current = versionKey(available), versionKey(installed)
    if newer and current:
        return newer > current
    return available.strip().lstrip("vV") != installed.strip().lstrip("vV")
