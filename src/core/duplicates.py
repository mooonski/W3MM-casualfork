'''Detecting mods that are installed twice'''

from dataclasses import dataclass

from src.core.model import Model
from src.core.nexus import SAME_LINE_SIMILARITY, archiveUploadTime, nameKey, similarity
from src.domain.mod import Mod


@dataclass
class Match:
    '''An installed entry that a mod being installed probably duplicates'''

    mod: Mod
    reason: str
    strength: int  # 4 same name and folders, 3 same folders, 2 same name, 1 same Nexus mod


def folderKeys(mod: Mod) -> set[str]:
    return {name.casefold() for name in mod.files + mod.dlcs}


def matchingEntries(model: Model, mod: Mod) -> list[Match]:
    '''Installed entries the given (not yet installed) mod looks like another copy or version of, strongest first'''
    incoming = folderKeys(mod)
    matches = []
    for existing in model.all():
        shared = sorted(folderKeys(existing) & incoming)
        sameName = existing.name == mod.name
        if sameName and folderKeys(existing) == incoming:
            matches.append(Match(existing, "is already installed", 4))
        elif shared:
            names = ", ".join(name for name in existing.files + existing.dlcs if name.casefold() in shared)
            matches.append(Match(existing, f"uses the same folders ({names})", 3))
        elif sameName:
            matches.append(Match(existing, "has the same name", 2))
        elif (
            mod.nexus
            and existing.nexus
            and mod.nexus.modId
            and mod.nexus.modId == existing.nexus.modId
            and similarity(nameKey(mod.name), nameKey(existing.name)) >= SAME_LINE_SIMILARITY
        ):
            matches.append(Match(existing, f"is the same Nexus mod ({mod.nexus.modId})", 1))
    return sorted(matches, key=lambda match: (-match.strength, match.mod.name.lower()))


def versionNote(mod: Mod, existing: Mod) -> str:
    '''Describes the incoming version against the installed one, warning about downgrades'''
    incoming = mod.nexus.version if mod.nexus else ''
    installed = existing.nexus.version if existing.nexus else ''
    if not incoming and not installed:
        return ''
    note = f"Installed: {installed or '?'}, this archive: {incoming or '?'}"
    newTime = archiveUploadTime(mod.nexus.archive) if mod.nexus else 0
    oldTime = 0
    if existing.nexus:
        oldTime = existing.nexus.uploaded or archiveUploadTime(existing.nexus.archive)
    if newTime and oldTime and newTime < oldTime:
        note += " (older than the installed version)"
    return note


def duplicateEntries(model: Model) -> dict[str, list[str]]:
    '''Installed entries that clash with another entry, with a description of each clash'''
    found: dict[str, list[str]] = {}
    mods = sorted(model.all(), key=lambda m: m.name.lower())
    for index, first in enumerate(mods):
        for second in mods[index + 1 :]:
            shared = folderKeys(first) & folderKeys(second)
            reasons = []
            if shared:
                names = ", ".join(name for name in first.files + first.dlcs if name.casefold() in shared)
                reasons.append(f"shares {names} with")
            if (
                first.nexus
                and second.nexus
                and first.nexus.fileId
                and (first.nexus.modId, first.nexus.fileId) == (second.nexus.modId, second.nexus.fileId)
            ):
                reasons.append("is the same Nexus file as")
            for reason in reasons:
                found.setdefault(first.name, []).append(f"{reason} {second.name}")
                found.setdefault(second.name, []).append(f"{reason} {first.name}")
    return found
