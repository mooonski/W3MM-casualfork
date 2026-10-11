'''Which mods the user endorsed on Nexus, kept between runs so the mod list can show it straight away'''

import json
import os
import time

from src.core.nexus import ENDORSED


class EndorsementCache:
    '''endorsements.json in the configuration folder: Nexus mod ID -> "Endorsed" or "Abstained"'''

    def __init__(self, folder: str):
        self.file = os.path.join(folder, 'endorsements.json')
        self.statuses: dict[int, str] = {}
        self.fetched = 0
        self.load()

    def load(self) -> None:
        try:
            with open(self.file, encoding='utf-8') as file:
                stored = json.load(file)
            self.statuses = {int(key): str(value) for key, value in dict(stored.get('mods') or {}).items()}
            self.fetched = int(stored.get('fetched') or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            self.statuses, self.fetched = {}, 0

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            temporary = self.file + '.new'
            with open(temporary, 'w', encoding='utf-8') as file:
                json.dump(
                    {'fetched': self.fetched, 'mods': {str(k): v for k, v in sorted(self.statuses.items())}}, file
                )
            os.replace(temporary, self.file)
        except OSError as error:
            print('could not save endorsements:', error)

    def endorsed(self, modId: int) -> bool:
        return self.statuses.get(int(modId or 0)) == ENDORSED

    def replace(self, statuses: dict[int, str]) -> None:
        '''Everything Nexus says, as fetched just now'''
        self.statuses = dict(statuses)
        self.fetched = int(time.time())
        self.save()

    def set(self, modId: int, status: str) -> None:
        self.statuses[int(modId)] = status
        self.save()
