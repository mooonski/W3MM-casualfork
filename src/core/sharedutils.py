'''Mods built on Sharedutils (modzzz_sharedutils) and the load order The Witcher 3 Remastered needs for them.

Remastered compiles mod scripts in load order, and a mod that builds on Sharedutils' SU_ classes only compiles
when it loads before Sharedutils, that is with a lower priority number in mods.settings. Mods without a priority
load after every numbered one, so with Sharedutils numbered they load after it and the game reports
"Unknown base class SU_..." errors.'''

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from os import path

from src.core.conflicts import loadOrder

SHAREDUTILS_FOLDER = "modzzz_sharedutils"
# defined by Sharedutils; another folder defining it is a copy of Sharedutils under another name
MARKER_CLASS = "SU_MenuDescriptor"
# what Sharedutils' own number becomes when a fix renumbers it, unless other mods already use higher numbers
FIXED_PRIORITY = 999

DEFINITION = re.compile(r"\b(?:class|struct|enum|function)\s+(SU_\w+)")
EXTENDS = re.compile(r"\bextends\s+(SU_\w+)")
NAME = re.compile(r"\bSU_\w+")


@dataclass
class Scripts:
    '''The SU_ names a mod folder's scripts define and use'''

    defined: set[str] = field(default_factory=set)
    used: set[str] = field(default_factory=set)
    extended: set[str] = field(default_factory=set)


@dataclass
class SharedutilsCheck:
    sharedutils: str  # the enabled Sharedutils folder, '' when there is none
    dependents: dict[str, list[str]]  # enabled folders that build on Sharedutils -> the SU_ names they use
    late: list[str]  # dependents that load after Sharedutils (in load order)
    order: list[str]  # enabled folders in load order
    priorities: dict[str, int | None]  # their mods.settings priorities


def priorityNumber(value) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except ValueError:
        return None


def readScript(file: str) -> str:
    '''A .ws file's text; the game's own scripts are UTF-16, most mods' are UTF-8'''
    with open(file, "rb") as handle:
        raw = handle.read()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    if b"\x00" in raw[:200]:
        return raw.decode("utf-16-le", "replace")
    return raw.decode("utf-8-sig", "replace")


def folderScripts(folder: str) -> Scripts:
    scripts = Scripts()
    for root, _, files in os.walk(folder):
        for name in files:
            if not name.lower().endswith(".ws"):
                continue
            try:
                text = readScript(path.join(root, name))
            except OSError:
                continue
            if "SU_" not in text:
                continue
            scripts.defined.update(DEFINITION.findall(text))
            scripts.extended.update(EXTENDS.findall(text))
            scripts.used.update(NAME.findall(text))
    return scripts


def checkSharedutils(modsFolder: str, priority: Callable[[str], str | None]) -> SharedutilsCheck:
    '''Finds the enabled mods that build on Sharedutils and those of them that load after it'''
    order = loadOrder(modsFolder, priority)
    priorities = {folder: priorityNumber(priority(folder)) for folder in order}
    scripts = {folder: folderScripts(path.join(modsFolder, folder)) for folder in order}
    named = [folder for folder in order if folder.casefold() == SHAREDUTILS_FOLDER]
    copies = [folder for folder in order if MARKER_CLASS in scripts[folder].defined]
    sharedutils = (named or copies or [""])[0]
    provided = scripts[sharedutils].defined if sharedutils else set()

    dependents: dict[str, list[str]] = {}
    for folder in order:
        if folder == sharedutils or folder in copies:
            continue
        found = scripts[folder]
        # with Sharedutils there, only its names count (and any SU_ class extended, which a newer version may
        # have); without it, every SU_ name the mod uses and doesn't define itself
        uses = ((found.used & provided) | found.extended if provided else found.used) - found.defined
        if uses:
            dependents[folder] = sorted(uses)
    late = []
    if sharedutils:
        position = order.index(sharedutils)
        late = [folder for folder in order[position + 1 :] if folder in dependents]
    return SharedutilsCheck(sharedutils, dependents, late, order, priorities)


def fixedPriorities(check: SharedutilsCheck) -> dict[str, int]:
    '''The mods.settings priorities that make every dependent load before Sharedutils: dependents without a
    priority get the next number after those in use (so they stay after the other numbered mods, as they were),
    numbered ones keep theirs, and Sharedutils gets the highest number, at least 999.'''
    if not check.sharedutils or not check.late:
        return {}
    others = [number for folder, number in check.priorities.items() if folder != check.sharedutils]
    top = max((number for number in others if number is not None), default=-1)
    changes = {}
    for folder in check.dependents:
        if check.priorities.get(folder) is None:
            changes[folder] = top + 1
    highest = max([top, *changes.values()])
    wanted = max(FIXED_PRIORITY, highest + 1)
    if check.priorities.get(check.sharedutils) != wanted:
        changes[check.sharedutils] = wanted
    return changes
