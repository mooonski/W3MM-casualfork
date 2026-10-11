'''Named groups the mod list can be split into (they only organise the list, the game never sees them)'''

import re
from collections.abc import Iterable

from src.domain.mod import Mod

MAX_NAME = 60


def cleanName(name: str) -> str:
    '''A group name as typed, with spaces tidied; empty if nothing usable is left'''
    return re.sub(r'\s+', ' ', name or '').strip()[:MAX_NAME].strip()


def orderedGroups(configured: Iterable[str], mods: Iterable[Mod]) -> list[str]:
    '''The groups in the order the user chose, then any group a mod names that isn't listed (A to Z)'''
    order: list[str] = []
    for name in configured:
        if name and name not in order:
            order.append(name)
    extra = sorted({mod.group for mod in mods if mod.group and mod.group not in order}, key=str.casefold)
    return order + extra


def addGroup(order: list[str], name: str) -> list[str]:
    return order if name in order else order + [name]


def renameGroup(mods: Iterable[Mod], order: list[str], old: str, new: str) -> list[str]:
    '''Renames a group everywhere; renaming onto an existing group joins the two'''
    for mod in mods:
        if mod.group == old:
            mod.group = new
    result = []
    for name in order:
        name = new if name == old else name
        if name not in result:
            result.append(name)
    return result


def removeGroup(mods: Iterable[Mod], order: list[str], name: str) -> list[str]:
    '''Removes a group; its mods stay installed and become ungrouped'''
    for mod in mods:
        if mod.group == name:
            mod.group = ''
    return [item for item in order if item != name]


def moveGroup(order: list[str], name: str, offset: int) -> list[str]:
    if name not in order:
        return order
    result = [item for item in order if item != name]
    index = max(0, min(len(result), order.index(name) + offset))
    result.insert(index, name)
    return result
