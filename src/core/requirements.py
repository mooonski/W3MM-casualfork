'''Requirements listed on Nexus mod pages that the installed mods don't meet'''

from collections.abc import Callable
from dataclasses import dataclass
from os import path

from src.core.model import Model
from src.core.nexus import nameKey, similarity
from src.core.nexusdetails import GAME_ID, ModDetails, Requirement
from src.domain.mod import Mod

# what is wrong with a requirement
MISSING = 'missing'
DISABLED = 'disabled'
DLC_MISSING = 'dlc'
EXTERNAL = 'external'  # outside Nexus, can't be checked

# the game's own folders for its expansions
DLC_FOLDERS = {'hearts of stone': 'ep1', 'blood and wine': 'bob'}

# shorter names match too much by substring
MIN_PARTIAL_NAME = 6


@dataclass
class MissingRequirement:
    mod: str
    requirement: Requirement
    state: str

    @property
    def problem(self) -> str:
        name = self.requirement.name or f"Nexus mod {self.requirement.modId}"
        if self.state == DISABLED:
            return f"{name} is installed but disabled"
        if self.state == DLC_MISSING:
            return f"Needs the {name} expansion"
        if self.state == EXTERNAL:
            return f"Also needs {name} (from outside Nexus)"
        return f"Needs {name}"


def _key(name: str) -> str:
    key = nameKey(name)
    return key[3:] if key.startswith('mod') and len(key) > 3 else key


def _namesMatch(wanted: str, mod: Mod) -> bool:
    '''Whether an installed mod looks like the required one by its name or folders'''
    if not wanted:
        return False
    names = [mod.name, *mod.files, *mod.dlcs]
    for name in names:
        key = _key(name)
        if not key:
            continue
        if similarity(key, wanted) >= 0.75:
            return True
        if len(wanted) >= MIN_PARTIAL_NAME and wanted in key:
            return True
        if len(key) >= MIN_PARTIAL_NAME and key in wanted:
            return True
    return False


def findInstalled(model: Model, requirement: Requirement, exclude: str = '') -> list[Mod]:
    '''Installed mods that meet a Nexus requirement: linked to its Nexus page, or failing that, named like it'''
    mods = [mod for mod in model.all() if mod.name != exclude]
    linked = [mod for mod in mods if mod.nexus and requirement.modId and mod.nexus.modId == requirement.modId]
    if linked:
        return linked
    wanted = _key(requirement.name)
    return [mod for mod in mods if _namesMatch(wanted, mod)]


def dlcInstalled(dlcFolder: str, name: str) -> bool:
    folder = DLC_FOLDERS.get(name.strip().lower())
    if not folder or not dlcFolder or not path.isdir(dlcFolder):
        return True  # unknown expansion or unknown game folder: nothing to report
    # Remastered merges the expansions into the base game and leaves a marker in dlc-tombstones
    tombstones = path.join(path.dirname(path.normpath(dlcFolder)), 'dlc-tombstones')
    return path.isdir(path.join(dlcFolder, folder)) or path.isdir(path.join(tombstones, folder))


def missingRequirements(
    model: Model,
    details: Callable[[int], ModDetails | None],
    dlcFolder: str = '',
    names: list[str] | None = None,
    external: bool = True,
) -> list[MissingRequirement]:
    '''Requirements of the enabled mods (or the named ones) that aren't met. Uses already loaded Nexus details.'''
    found: list[MissingRequirement] = []
    for mod in sorted(model.all(), key=lambda m: m.name.lower()):
        if names is not None and mod.name not in names:
            continue
        if names is None and not mod.enabled:
            continue
        known = details(mod.nexus.modId) if mod.nexus and mod.nexus.modId else None
        if known is None:
            continue
        seen: set[tuple[str, int]] = set()
        for requirement in known.requirements:
            key = (requirement.name.casefold(), requirement.modId)
            if key in seen:
                continue
            seen.add(key)
            if requirement.dlc:
                if not dlcInstalled(dlcFolder, requirement.name):
                    found.append(MissingRequirement(mod.name, requirement, DLC_MISSING))
                continue
            if requirement.external or not requirement.modId:
                if external and requirement.name:
                    found.append(MissingRequirement(mod.name, requirement, EXTERNAL))
                continue
            if requirement.gameId != GAME_ID or requirement.modId == mod.nexus.modId:
                continue
            installed = findInstalled(model, requirement, exclude=mod.name)
            if not installed:
                found.append(MissingRequirement(mod.name, requirement, MISSING))
            elif not any(other.enabled for other in installed):
                found.append(MissingRequirement(mod.name, requirement, DISABLED))
    return found
