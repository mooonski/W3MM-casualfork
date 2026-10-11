'''A record of the files downloaded from Nexus and what became of them'''

import json
import os
import time
from dataclasses import asdict, dataclass, fields
from typing import Any

from src.core.nexus import GAME_DOMAIN

# what happened to a download
INSTALLED = 'installed'
UPDATED = 'updated'
KEPT = 'kept'  # downloaded but not installed
FAILED = 'failed'
WEBSITE = 'website'  # sent to the website to download there

# where it came from
UPDATE = 'update'
LINK = 'link'

MAX_ENTRIES = 1000


@dataclass
class Download:
    time: int
    modId: int
    fileId: int = 0
    name: str = ''
    version: str = ''
    archive: str = ''
    source: str = LINK
    result: str = KEPT
    message: str = ''
    mod: str = ''  # the installed entry it went into

    @classmethod
    def fromJson(cls, value: dict[str, Any]) -> 'Download':
        known = {item.name for item in fields(cls)}
        return cls(**{key: item for key, item in value.items() if key in known})

    @property
    def url(self) -> str:
        return f"https://www.nexusmods.com/{GAME_DOMAIN}/mods/{self.modId}" if self.modId else ''

    @property
    def archiveExists(self) -> bool:
        return bool(self.archive) and os.path.isfile(self.archive)


class DownloadHistory:
    '''Kept as download_history.json in the configuration folder, newest first'''

    def __init__(self, folder: str):
        self.file = os.path.join(folder, 'download_history.json')
        self.entries: list[Download] = []
        self.load()

    def load(self) -> None:
        try:
            with open(self.file, encoding='utf-8') as file:
                stored = json.load(file)
            self.entries = [Download.fromJson(item) for item in stored if isinstance(item, dict)]
        except (OSError, ValueError, TypeError):
            self.entries = []

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.file), exist_ok=True)
        temporary = self.file + '.new'
        with open(temporary, 'w', encoding='utf-8') as file:
            json.dump([asdict(entry) for entry in self.entries[:MAX_ENTRIES]], file, indent=1)
        os.replace(temporary, self.file)

    def add(self, download: Download) -> Download:
        '''Records a download and saves the history; problems saving are printed, never raised'''
        if not download.time:
            download.time = int(time.time())
        self.entries.insert(0, download)
        del self.entries[MAX_ENTRIES:]
        self.trySave()
        return download

    def finish(self, download: Download, result: str, message: str = '', mod: str = '') -> None:
        download.result = result
        if message:
            download.message = message
        if mod:
            download.mod = mod
        self.trySave()

    def trySave(self) -> None:
        try:
            self.save()
        except OSError as error:
            print('could not save the download history:', error)
