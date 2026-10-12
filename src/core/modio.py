'''Mods the game installed from mod.io through its own in-game menu. Read only: the game owns them, the mod list just
shows they are there.

The game keeps which mods are on in modioModsConfig.json next to user.settings, and mod.io's client keeps the mods
and everything it knows about them under %PUBLIC%\\mod.io\\<game id>.'''

import json
import os
from dataclasses import dataclass, field
from time import gmtime, strftime

# The Witcher 3 on mod.io
GAME_ID = 8254
CONFIG_FILE = 'modioModsConfig.json'
TAG = '[MOD.IO]'


@dataclass
class ModioMod:
    modId: int
    name: str = ''
    enabled: bool = True
    priority: int | None = None
    version: str = ''
    author: str = ''
    summary: str = ''
    url: str = ''
    size: int = 0
    updated: int = 0
    path: str = ''
    folders: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.name or f'mod.io mod {self.modId}'

    @property
    def label(self) -> str:
        '''The name the mod list shows'''
        return f'{TAG} {self.title}'

    @property
    def date(self) -> str:
        return strftime('%Y-%m-%d %H:%M:%S', gmtime(self.updated)) if self.updated else ''


def modioFolder() -> str:
    return os.path.join(os.environ.get('PUBLIC') or r'C:\Users\Public', 'mod.io', str(GAME_ID))


def readConfig(path: str) -> list[tuple[int, bool, int | None]]:
    '''(mod ID, on, priority) for each mod in modioModsConfig.json. The game writes every mod under the same "mod"
    key, so a plain JSON read would keep only the last one.'''
    try:
        with open(path, encoding='utf-8-sig') as file:
            pairs = json.load(file, object_pairs_hook=lambda items: items)
    except (OSError, ValueError):
        return []
    mods = []
    for key, value in pairs if isinstance(pairs, list) else []:
        entry = dict(value) if key == 'mod' and isinstance(value, list) else {}
        try:
            modId = int(entry.get('modID') or 0)
        except (TypeError, ValueError):
            continue
        if modId <= 0:
            continue
        priority = entry.get('priority')
        mods.append((modId, entry.get('isEnabled') is not False, priority if isinstance(priority, int) else None))
    return mods


def readState(folder: str) -> dict[int, dict]:
    '''What mod.io's client knows about each installed mod (metadata/state.json), by mod ID'''
    try:
        with open(os.path.join(folder, 'metadata', 'state.json'), encoding='utf-8-sig') as file:
            state = json.load(file)
    except (OSError, ValueError):
        return {}
    found = {}
    entries = state.get('Mods') if isinstance(state, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict):
            try:
                found[int(entry.get('ID') or 0)] = entry
            except (TypeError, ValueError):
                continue
    found.pop(0, None)
    return found


def readInfo(path: str) -> dict:
    '''A mod folder's content/info.json, which names the mod even when state.json doesn't'''
    try:
        with open(os.path.join(path, 'content', 'info.json'), encoding='utf-8-sig') as file:
            info = json.load(file)
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def describe(mod: ModioMod, entry: dict, folder: str) -> None:
    '''Fills in the name and details from mod.io's state and the mod's own files'''
    profile = entry.get('Profile') if isinstance(entry.get('Profile'), dict) else {}
    modfile = profile.get('modfile') if isinstance(profile.get('modfile'), dict) else {}
    author = profile.get('submitted_by') if isinstance(profile.get('submitted_by'), dict) else {}
    mod.name = str(profile.get('name') or '')
    mod.version = str(modfile.get('version') or '')
    mod.author = str(author.get('display_name_portal') or author.get('username') or '')
    mod.summary = str(profile.get('summary') or '')
    mod.url = str(profile.get('profile_url') or '')
    mod.updated = int(profile.get('date_updated') or 0) if str(profile.get('date_updated') or '0').isdigit() else 0
    mod.path = str(entry.get('PathOnDisk') or '') or os.path.join(folder, 'mods', str(mod.modId))
    if not os.path.isdir(mod.path):
        mod.path = os.path.join(folder, 'mods', str(mod.modId))
    size = entry.get('SizeOnDisk')
    mod.size = size if isinstance(size, int) and size > 0 else folderSize(mod.path)
    gameMods = os.path.join(mod.path, 'mods')
    try:
        mod.folders = sorted(name for name in os.listdir(gameMods) if os.path.isdir(os.path.join(gameMods, name)))
    except OSError:
        mod.folders = []
    for name in mod.folders:
        info = readInfo(os.path.join(gameMods, name))
        mod.name = mod.name or str(info.get('modName') or info.get('name') or '')
        mod.version = mod.version or str(info.get('version') or '')
        mod.author = mod.author or str(info.get('author') or '')


def folderSize(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def installedModioMods(settingsFolder: str | None, folder: str | None = None) -> list[ModioMod]:
    '''Every mod.io mod the game has, in the game's mod.io order. Mods mod.io installed that the game's list
    doesn't mention yet are shown too, as off.'''
    folder = folder or modioFolder()
    listed = readConfig(os.path.join(settingsFolder, CONFIG_FILE)) if settingsFolder else []
    state = readState(folder)
    if not listed and not state:
        return []
    mods = [ModioMod(modId, enabled=enabled, priority=priority) for modId, enabled, priority in listed]
    known = {mod.modId for mod in mods}
    mods += [ModioMod(modId, enabled=False) for modId in sorted(state) if modId not in known]
    for mod in mods:
        describe(mod, state.get(mod.modId, {}), folder)
    return sorted(mods, key=lambda mod: (mod.priority is None, mod.priority or 0))
