'''Copies of the newest game saves, made before mods are updated'''

import json
import os
import shutil
import time
from dataclasses import dataclass, field

from src.core.nexus import safeFileName

# inside the backup folder; a name no mod's backup folder gets
SAVES_FOLDER = '~Game saves'
MANIFEST = 'saves.json'
NEWEST = 10  # how many saves are copied each time

# a save is its .sav file plus the picture and details the game writes next to it
COMPANIONS = ('.png', '.json')


@dataclass
class SaveBackup:
    directory: str
    created: str
    reason: str
    saves: dict[str, list[int]] = field(default_factory=dict)  # file name -> [size, modified]

    @property
    def count(self) -> int:
        return sum(1 for name in self.saves if name.lower().endswith('.sav'))

    @property
    def size(self) -> int:
        return sum(info[0] for info in self.saves.values())


def gameSavesFolder(documents: str) -> str:
    '''The game's save folder inside "Documents/The Witcher 3"'''
    return os.path.join(documents, 'gamesaves') if documents else ''


def newestSaves(folder: str, count: int = NEWEST) -> dict[str, list[int]]:
    '''The newest saves and their companion files: file name -> [size, modified time]'''
    if not folder or not os.path.isdir(folder):
        return {}
    entries = {entry.name: entry for entry in os.scandir(folder) if entry.is_file()}
    saves = sorted(
        (entry for name, entry in entries.items() if name.lower().endswith('.sav')),
        key=lambda entry: entry.stat().st_mtime,
        reverse=True,
    )[:count]
    chosen = {}
    for save in saves:
        stem = os.path.splitext(save.name)[0]
        for name in [save.name] + [stem + extension for extension in COMPANIONS]:
            entry = entries.get(name)
            if entry is not None:
                info = entry.stat()
                chosen[name] = [int(info.st_size), int(info.st_mtime)]
    return chosen


def listSaveBackups(backups: str) -> list[SaveBackup]:
    '''Save backups in the backup folder, newest first'''
    root = os.path.join(backups, SAVES_FOLDER) if backups else ''
    found = []
    if not root or not os.path.isdir(root):
        return found
    for name in os.listdir(root):
        directory = os.path.join(root, name)
        try:
            with open(os.path.join(directory, MANIFEST), encoding='utf-8') as file:
                meta = json.load(file)
            saves = {str(key): [int(value[0]), int(value[1])] for key, value in dict(meta.get('saves') or {}).items()}
        except (OSError, ValueError, TypeError, IndexError, AttributeError):
            continue
        found.append(SaveBackup(directory, str(meta.get('created') or name), str(meta.get('reason') or ''), saves))
    return sorted(found, key=lambda backup: backup.created, reverse=True)


def backupSaves(savesFolder: str, backups: str, reason: str, count: int = NEWEST) -> SaveBackup | None:
    '''Copies the newest saves into a new folder in the backup folder. Returns None when there are no saves, or
    when the newest backup already holds exactly these saves.'''
    saves = newestSaves(savesFolder, count)
    if not saves:
        return None
    previous = listSaveBackups(backups)
    if previous and previous[0].saves == saves:
        return None
    created = time.strftime('%Y-%m-%d %H:%M:%S')
    root = os.path.normpath(os.path.join(backups, SAVES_FOLDER))
    directory = os.path.join(root, safeFileName(f"{created.replace(':', '')} {reason}"))
    number = 2
    while os.path.exists(directory):
        directory = os.path.join(root, safeFileName(f"{created.replace(':', '')} {reason} ({number})"))
        number += 1
    partial = directory + '.part'
    os.makedirs(partial)
    try:
        for name in saves:
            shutil.copy2(os.path.join(savesFolder, name), os.path.join(partial, name))
        with open(os.path.join(partial, MANIFEST), 'w', encoding='utf-8') as file:
            json.dump({'created': created, 'reason': reason, 'saves': saves}, file, indent=1)
    except OSError:
        shutil.rmtree(partial, ignore_errors=True)  # only the incomplete copy this call just made
        raise
    os.replace(partial, directory)
    return SaveBackup(directory, created, reason, saves)


def restoreSaves(backup: SaveBackup, savesFolder: str) -> tuple[list[str], list[str]]:
    '''Copies the backed-up saves back into the game's save folder, never replacing a file that is there.
    Returns (copied, skipped).'''
    os.makedirs(savesFolder, exist_ok=True)
    copied, skipped = [], []
    for name in sorted(backup.saves):
        source = os.path.join(backup.directory, name)
        target = os.path.join(savesFolder, name)
        if os.path.exists(target) or not os.path.isfile(source):
            skipped.append(name)
            continue
        shutil.copy2(source, target)
        copied.append(name)
    return copied, skipped
