'''Nexus Mods API client and update resolution'''

import calendar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64decode, b64encode
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.globals.constants import VERSION

API_URL = "https://api.nexusmods.com/v1/"
GAME_DOMAIN = "witcher3"
APP_NAME = "TW3MM"

# Nexus file categories that are offered as the current version of a mod
# (UPDATE files are usually patches applied on top of a main file, so they're not offered as replacements)
CURRENT_CATEGORIES = ("MAIN", "OPTIONAL", "MISCELLANEOUS")

# Name similarity needed before a file is treated as the same "line" as the installed one
SAME_LINE_SIMILARITY = 0.75


class NexusError(Exception):
    '''Error returned by the Nexus API, with a message suitable for users'''

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


@dataclass
class NexusFile:
    '''A downloadable file on a Nexus mod page'''

    fileId: int
    name: str
    version: str
    category: str
    primary: bool
    uploaded: int
    fileName: str
    size: int
    description: str = ''

    @classmethod
    def fromApi(cls, value: dict[str, Any]) -> 'NexusFile':
        return cls(
            fileId=int(value['file_id']),
            name=str(value.get('name') or ''),
            version=str(value.get('version') or value.get('mod_version') or ''),
            category=str(value.get('category_name') or '').upper(),
            primary=bool(value.get('is_primary')),
            uploaded=int(value.get('uploaded_timestamp') or 0),
            fileName=str(value.get('file_name') or ''),
            size=int(value.get('size_in_bytes') or int(value.get('size_kb') or 0) * 1024),
            description=str(value.get('description') or ''),
        )

    @property
    def current(self) -> bool:
        return self.category in CURRENT_CATEGORIES

    @property
    def categoryName(self) -> str:
        '''The category as the website shows it, e.g. "Old Version"'''
        return self.category.replace('_', ' ').title()

    def label(self) -> str:
        version = self.version
        if version and not version.lower().startswith('v'):
            version = 'v' + version
        uploaded = time.strftime('%Y-%m-%d', time.gmtime(self.uploaded)) if self.uploaded else ''
        parts = [self.name, version, f"[{self.categoryName}]", uploaded]
        return '  '.join(part for part in parts if part)


@dataclass
class ModFiles:
    '''All files of a Nexus mod and the author's "replaced by" links between them'''

    files: dict[int, NexusFile] = field(default_factory=dict)
    updates: dict[int, int] = field(default_factory=dict)

    @classmethod
    def fromApi(cls, value: dict[str, Any]) -> 'ModFiles':
        files = {}
        for item in value.get('files') or []:
            try:
                file = NexusFile.fromApi(item)
            except (KeyError, TypeError, ValueError):
                continue
            files[file.fileId] = file
        updates = {}
        for item in value.get('file_updates') or []:
            try:
                updates[int(item['old_file_id'])] = int(item['new_file_id'])
            except (KeyError, TypeError, ValueError):
                continue
        return cls(files, updates)

    def latestInChain(self, fileId: int) -> int:
        '''Follows "replaced by" links from fileId and returns the newest file that still exists'''
        latest = fileId
        seen = {fileId}
        current = fileId
        while current in self.updates:
            current = self.updates[current]
            if current in seen:
                break
            seen.add(current)
            if current in self.files:
                latest = current
        return latest

    def currentFiles(self) -> list[NexusFile]:
        return sorted((file for file in self.files.values() if file.current), key=lambda f: f.uploaded, reverse=True)

    def byFileName(self, archiveName: str) -> NexusFile | None:
        wanted = os.path.basename(archiveName).casefold()
        if not wanted:
            return None
        for file in self.files.values():
            if file.fileName and file.fileName.casefold() == wanted:
                return file
        return None


@dataclass
class NexusInfo:
    '''Nexus details stored with an installed mod'''

    modId: int = 0
    fileId: int = 0
    version: str = ''
    fileName: str = ''
    uploaded: int = 0
    archive: str = ''
    game: str = GAME_DOMAIN

    @property
    def url(self) -> str:
        return f"https://www.nexusmods.com/{self.game}/mods/{self.modId}" if self.modId else ''

    @classmethod
    def fromFile(cls, modId: int, file: NexusFile, archive: str = '', game: str = GAME_DOMAIN) -> 'NexusInfo':
        return cls(modId, file.fileId, file.version, file.name, file.uploaded, archive or file.fileName, game)


# update states
UP_TO_DATE = 'uptodate'
UPDATE_AVAILABLE = 'update'
UNKNOWN = 'unknown'
FAILED = 'error'


@dataclass
class UpdateCheck:
    '''Result of comparing an installed mod against its Nexus files'''

    state: str
    installed: NexusFile | None = None
    target: NexusFile | None = None
    candidates: list[NexusFile] = field(default_factory=list)
    confident: bool = False
    message: str = ''

    @property
    def needsChoice(self) -> bool:
        return self.state == UPDATE_AVAILABLE and not self.confident


# ---------------------------------------------------------------- archive names


NEW_ARCHIVE_PATTERN = re.compile(
    r"^(?P<name>.+?) (?P<modid>\d+) (?P<version>\S+) (?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}Z) [A-Za-z0-9]+"
    r"\.(?:zip|7z|rar)$",
    re.IGNORECASE,
)
LEGACY_ARCHIVE_PATTERN = re.compile(
    r"^(?P<name>.+?)-(?P<modid>\d+)-(?P<version>[0-9a-z\-]*?)-?(?P<stamp>\d{9,11})\.(?:zip|7z|rar)$",
    re.IGNORECASE,
)


def archiveUploadTime(filename: str) -> int:
    '''Returns the upload time (unix, UTC) that Nexus puts in its download names, or 0'''
    base = os.path.basename(filename)
    match = NEW_ARCHIVE_PATTERN.match(base)
    if match:
        return calendar.timegm(time.strptime(match.group('stamp'), "%Y-%m-%dT%H-%MZ"))
    match = LEGACY_ARCHIVE_PATTERN.match(base)
    if match:
        return int(match.group('stamp'))
    return 0


def parseArchiveName(filename: str) -> tuple[str, int, str] | None:
    '''Returns (name, mod id, version) for archives named the way Nexus names downloads'''
    base = os.path.basename(filename)
    for pattern in (NEW_ARCHIVE_PATTERN, LEGACY_ARCHIVE_PATTERN):
        match = pattern.match(base)
        if match:
            version = match.group('version').replace('-', '.').strip('.')
            return match.group('name').strip(), int(match.group('modid')), version
    return None


def parseModReference(text: str) -> int | None:
    '''Accepts a mod id or a Nexus mod page URL and returns the mod id'''
    text = text.strip()
    if text.isdecimal():
        return int(text)
    match = re.search(r"nexusmods\.com/(?:[^/]+/)?(?:witcher3)/mods/(\d+)", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


# ---------------------------------------------------------------- update resolution


def nameKey(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def similarity(a: str, b: str) -> float:
    '''Sorensen-Dice similarity of character bigrams, 0..1'''
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) < 2 or len(b) < 2:
        return 0.0
    bigrams: dict[str, int] = {}
    for i in range(len(a) - 1):
        bigrams[a[i : i + 2]] = bigrams.get(a[i : i + 2], 0) + 1
    hits = 0
    for i in range(len(b) - 1):
        pair = b[i : i + 2]
        if bigrams.get(pair, 0) > 0:
            bigrams[pair] -= 1
            hits += 1
    return 2.0 * hits / (len(a) - 1 + len(b) - 1)


def installTimestamp(date: str) -> int:
    '''Converts a mod's install date (UTC, as written by the installer) to a unix timestamp'''
    try:
        return calendar.timegm(time.strptime(date, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return 0


def rankCandidates(files: ModFiles, reference: str) -> list[NexusFile]:
    '''Current files ordered by how likely they are to be the line the user has installed'''
    key = nameKey(reference)

    def score(file: NexusFile) -> float:
        value = similarity(key, nameKey(file.name))
        if file.category == 'MAIN':
            value += 0.05
        if file.primary:
            value += 0.02
        return value

    return sorted(files.currentFiles(), key=lambda f: (score(f), f.uploaded), reverse=True)


def checkForUpdate(info: NexusInfo, files: ModFiles, modName: str, installDate: str) -> UpdateCheck:
    '''Works out whether a newer version of the installed file exists'''
    current = files.currentFiles()
    if not current:
        return UpdateCheck(FAILED, message='No current files on Nexus')

    installed = files.files.get(info.fileId) if info.fileId else None
    if installed is None and info.archive:
        installed = files.byFileName(info.archive)

    if installed is not None:
        latest = files.files[files.latestInChain(installed.fileId)]
        reference = latest.name if latest.current else installed.name
        candidates = rankCandidates(files, reference)
        if latest.fileId != installed.fileId and latest.current:
            return UpdateCheck(UPDATE_AVAILABLE, installed, latest, candidates, confident=True)
        if latest.fileId == installed.fileId and installed.current:
            return UpdateCheck(UP_TO_DATE, installed, installed, candidates, confident=True)
        # the chain ends in an old or archived file, so the author didn't link the new upload
        best = candidates[0]
        if best.uploaded > installed.uploaded:
            return UpdateCheck(UPDATE_AVAILABLE, installed, best, candidates, confident=False)
        return UpdateCheck(UP_TO_DATE, installed, installed, candidates, confident=False)

    # installed file unknown: guess from the mod name and the install date
    reference = info.fileName or modName
    candidates = rankCandidates(files, reference)
    best = candidates[0]
    installedAt = installTimestamp(installDate)
    bestKey = nameKey(best.name)
    assumed = [
        file
        for file in files.files.values()
        if file.uploaded <= installedAt and similarity(nameKey(file.name), bestKey) >= SAME_LINE_SIMILARITY
    ]
    installed = max(assumed, key=lambda f: f.uploaded) if assumed else None
    if best.uploaded > installedAt:
        return UpdateCheck(UPDATE_AVAILABLE, installed, best, candidates, confident=False)
    return UpdateCheck(UP_TO_DATE, installed, best, candidates, confident=False)


# ---------------------------------------------------------------- API key storage


def protectSecret(secret: str) -> str:
    '''Encrypts a secret for the current Windows user (DPAPI); elsewhere it is only encoded'''
    raw = secret.encode('utf-8')
    if sys.platform == 'win32':
        import win32crypt  # type: ignore

        raw = win32crypt.CryptProtectData(raw, APP_NAME, None, None, None, 0)
        return 'dpapi:' + b64encode(raw).decode('ascii')
    return 'b64:' + b64encode(raw).decode('ascii')


def unprotectSecret(value: str) -> str:
    if not value:
        return ''
    scheme, _, payload = value.partition(':')
    try:
        raw = b64decode(payload.encode('ascii'))
        if scheme == 'dpapi' and sys.platform == 'win32':
            import win32crypt  # type: ignore

            raw = win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1]
        elif scheme != 'b64':
            return ''
        return raw.decode('utf-8')
    except Exception as error:
        print('could not read stored Nexus API key:', error, file=sys.stderr)
        return ''


# ---------------------------------------------------------------- HTTP client


class NexusClient:
    '''Minimal Nexus Mods v1 API client'''

    def __init__(
        self,
        apikey: str,
        game: str = GAME_DOMAIN,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout: float = 30,
    ):
        self.apikey = apikey.strip()
        self.game = game
        self.opener = opener
        self.timeout = timeout
        self.remaining: dict[str, str] = {}

    @property
    def headers(self) -> dict[str, str]:
        return {
            'apikey': self.apikey,
            'Application-Name': APP_NAME,
            'Application-Version': VERSION,
            'User-Agent': f"{APP_NAME}/{VERSION}",
            'Accept': 'application/json',
        }

    def _get(self, path: str) -> Any:
        if not self.apikey:
            raise NexusError('No Nexus API key is set. Add one under Nexus > Set API Key.')
        request = urllib.request.Request(API_URL + path, headers=self.headers)
        try:
            with self.opener(request, timeout=self.timeout) as response:
                for name, value in response.headers.items():
                    if name.lower().startswith('x-rl-'):
                        self.remaining[name.lower()] = value
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as error:
            raise NexusError(self._describe(error), error.code) from error
        except urllib.error.URLError as error:
            raise NexusError(f"Could not reach Nexus Mods: {error.reason}") from error
        except (TimeoutError, OSError) as error:
            raise NexusError(f"Could not reach Nexus Mods: {error}") from error
        except ValueError as error:
            raise NexusError(f"Nexus Mods sent an unreadable response: {error}") from error

    @staticmethod
    def _describe(error: urllib.error.HTTPError) -> str:
        detail = ''
        try:
            body = json.loads(error.read().decode('utf-8'))
            detail = str(body.get('message') or body.get('error') or '')
        except Exception:
            pass
        if error.code == 401:
            return 'Nexus rejected the API key. Check it under Nexus > Set API Key.'
        if error.code == 403:
            return detail or 'Nexus refused the request. Direct downloads need a Premium account.'
        if error.code == 404:
            return detail or 'Not found on Nexus (the mod may be hidden or removed).'
        if error.code == 429:
            return 'Nexus API rate limit reached. Try again in an hour.'
        return f"Nexus returned HTTP {error.code}" + (f": {detail}" if detail else '')

    def validate(self) -> dict[str, Any]:
        return self._get('users/validate.json')

    def modInfo(self, modId: int) -> dict[str, Any]:
        return self._get(f"games/{self.game}/mods/{int(modId)}.json")

    def modFiles(self, modId: int) -> ModFiles:
        return ModFiles.fromApi(self._get(f"games/{self.game}/mods/{int(modId)}/files.json"))

    def downloadLinks(self, modId: int, fileId: int, key: str = '', expires: str = '') -> list[str]:
        path = f"games/{self.game}/mods/{int(modId)}/files/{int(fileId)}/download_link.json"
        if key:
            path += '?' + urllib.parse.urlencode({'key': key, 'expires': expires})
        links = self._get(path)
        urls = [str(link['URI']) for link in links if isinstance(link, dict) and link.get('URI')]
        if not urls:
            raise NexusError('Nexus returned no download link.')
        return urls

    def download(
        self,
        url: str,
        directory: str,
        filename: str,
        expectedSize: int = 0,
        progress: Callable[[int, int], Any] = lambda done, total: None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> str:
        '''Downloads url into directory as filename and returns the path. Never overwrites existing files.'''
        os.makedirs(directory, exist_ok=True)
        filename = safeFileName(filename) or 'download'
        existing = os.path.join(directory, filename)
        if expectedSize and os.path.isfile(existing) and os.path.getsize(existing) == expectedSize:
            return existing
        partial = uniquePath(directory, filename + '.part')
        request = urllib.request.Request(encodeUrl(url), headers={'User-Agent': f"{APP_NAME}/{VERSION}"})
        done = 0
        try:
            with self.opener(request, timeout=self.timeout) as response, open(partial, 'wb') as output:
                total = int(response.headers.get('Content-Length') or expectedSize or 0)
                last = 0.0
                while True:
                    if cancelled():
                        raise NexusError('Download cancelled')
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last > 0.15:
                        progress(done, total)
                        last = now
                progress(done, total)
            if total and done != total:
                raise NexusError(f"Download incomplete ({done} of {total} bytes)")
        except urllib.error.HTTPError as error:
            self._discard(partial)
            raise NexusError(self._describe(error), error.code) from error
        except (urllib.error.URLError, OSError) as error:
            self._discard(partial)
            raise NexusError(f"Download failed: {error}") from error
        except BaseException:
            self._discard(partial)
            raise
        target = uniquePath(directory, filename)
        os.replace(partial, target)
        return target

    @staticmethod
    def _discard(partial: str) -> None:
        try:
            if os.path.isfile(partial):
                os.remove(partial)
        except OSError:
            pass


def encodeUrl(url: str) -> str:
    '''Percent-encodes characters such as spaces that download links sometimes contain; encoded parts stay as they are'''
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parts.query, safe="=&%/:@!$'()*+,;-._~?")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def safeFileName(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', os.path.basename(name)).strip(' .')


def uniquePath(directory: str, filename: str) -> str:
    '''Returns directory/filename, adding " (2)", " (3)"... if that name is taken'''
    target = os.path.join(directory, filename)
    if filename.endswith('.part'):
        stem, extension = os.path.splitext(filename[:-5])
        extension += '.part'
    else:
        stem, extension = os.path.splitext(filename)
    number = 2
    while os.path.lexists(target):
        target = os.path.join(directory, f"{stem} ({number}){extension}")
        number += 1
    return target
