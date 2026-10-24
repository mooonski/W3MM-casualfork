'''Mod details from Nexus (pictures, categories, summaries) and mod search, through the public GraphQL API'''

import calendar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from src.core.nexus import CLIENT_NAME, GAME_DOMAIN, NexusError, nameKey, safeFileName, similarity
from src.globals.constants import VERSION

GRAPHQL_URL = "https://api.nexusmods.com/v2/graphql"
GAME_ID = 952  # The Witcher 3 on Nexus

# The Witcher 3 Remastered (5.x) came out on this day; mods not updated since may not work with it
REMASTERED_RELEASE = calendar.timegm((2026, 9, 29, 0, 0, 0))

SORTS = ("relevance", "endorsements", "downloads", "updatedAt", "createdAt")

DETAIL_FIELDS = (
    "modId name summary category author version updatedAt thumbnailUrl pictureUrl endorsements downloads adultContent"
)
REQUIREMENT_FIELDS = (
    "modRequirements { dlcRequirements { gameExpansion { name } notes } "
    "nexusRequirements { nodes { modId modName gameId notes externalRequirement url } } }"
)

# details stored by older versions lack the requirements and are fetched again
DETAILS_SCHEMA = 2


@dataclass
class Requirement:
    '''Something a mod page lists under "Requirements": another Nexus mod, a game expansion or an outside tool'''

    name: str
    modId: int = 0
    gameId: int = 0
    notes: str = ''
    external: bool = False
    url: str = ''
    dlc: bool = False

    @classmethod
    def fromJson(cls, value: dict[str, Any]) -> 'Requirement':
        known = {item.name for item in fields(cls)}
        return cls(**{key: item for key, item in value.items() if key in known})

    @property
    def pageUrl(self) -> str:
        if self.url:
            return self.url
        if self.modId and self.gameId == GAME_ID:
            return f"https://www.nexusmods.com/{GAME_DOMAIN}/mods/{self.modId}"
        return ''


def parseRequirements(value: Any) -> list[Requirement]:
    '''The requirements part of a GraphQL mod node'''
    if not isinstance(value, dict):
        return []
    found = []
    for item in value.get('dlcRequirements') or []:
        name = str(((item or {}).get('gameExpansion') or {}).get('name') or '')
        if name:
            found.append(Requirement(name, notes=str(item.get('notes') or ''), dlc=True))
    for item in (value.get('nexusRequirements') or {}).get('nodes') or []:
        try:
            found.append(
                Requirement(
                    str(item.get('modName') or ''),
                    int(item.get('modId') or 0),
                    int(item.get('gameId') or 0),
                    str(item.get('notes') or ''),
                    bool(item.get('externalRequirement')),
                    str(item.get('url') or ''),
                )
            )
        except (AttributeError, TypeError, ValueError):
            continue
    return found


@dataclass
class ModDetails:
    modId: int
    name: str = ''
    summary: str = ''
    category: str = ''
    author: str = ''
    version: str = ''
    updated: int = 0
    thumbnailUrl: str = ''
    pictureUrl: str = ''
    endorsements: int = 0
    downloads: int = 0
    adult: bool = False
    fetched: int = 0
    requirements: list[Requirement] = field(default_factory=list)
    schema: int = 0

    @classmethod
    def fromGraphQL(cls, node: dict[str, Any], fetched: int = 0) -> 'ModDetails':
        withRequirements = 'modRequirements' in node
        return cls(
            modId=int(node['modId']),
            name=str(node.get('name') or ''),
            summary=str(node.get('summary') or ''),
            category=str(node.get('category') or ''),
            author=str(node.get('author') or ''),
            version=str(node.get('version') or ''),
            updated=parseTime(node.get('updatedAt')),
            thumbnailUrl=str(node.get('thumbnailUrl') or ''),
            pictureUrl=str(node.get('pictureUrl') or ''),
            endorsements=int(node.get('endorsements') or 0),
            downloads=int(node.get('downloads') or 0),
            adult=bool(node.get('adultContent')),
            fetched=fetched,
            requirements=parseRequirements(node.get('modRequirements')),
            schema=DETAILS_SCHEMA if withRequirements else 0,
        )

    @classmethod
    def fromJson(cls, value: dict[str, Any]) -> 'ModDetails':
        known = {item.name for item in fields(cls)}
        values = {key: item for key, item in value.items() if key in known}
        values['requirements'] = [
            Requirement.fromJson(item) for item in values.get('requirements') or [] if isinstance(item, dict)
        ]
        return cls(**values)

    @property
    def url(self) -> str:
        return f"https://www.nexusmods.com/{GAME_DOMAIN}/mods/{self.modId}"

    @property
    def updatedForRemastered(self) -> bool:
        return not self.updated or self.updated >= REMASTERED_RELEASE


def parseTime(value: Any) -> int:
    if not value:
        return 0
    try:
        return calendar.timegm(time.strptime(str(value)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0


class NexusGraphQL:
    '''Read-only queries against the public Nexus GraphQL API (no API key needed)'''

    def __init__(self, opener: Callable[..., Any] = urllib.request.urlopen, timeout: float = 30):
        self.opener = opener
        self.timeout = timeout

    def query(self, text: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps({'query': text, 'variables': variables or {}}).encode('utf-8')
        request = urllib.request.Request(
            GRAPHQL_URL,
            data=body,
            headers={
                'Content-Type': 'application/json',
                'Application-Name': CLIENT_NAME,
                'Application-Version': VERSION,
                'User-Agent': f"{CLIENT_NAME}/{VERSION}",
            },
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as error:
            raise NexusError(f"Nexus returned HTTP {error.code}", error.code) from error
        except (urllib.error.URLError, OSError) as error:
            raise NexusError(f"Could not reach Nexus Mods: {error}") from error
        except ValueError as error:
            raise NexusError(f"Nexus Mods sent an unreadable response: {error}") from error
        if result.get('errors') and not result.get('data'):
            message = str(result['errors'][0].get('message') or 'Nexus query failed')
            raise NexusError(message if len(message) <= 300 else message[:300] + '...')
        return result.get('data') or {}

    def details(self, modIds: list[int], game: str = GAME_DOMAIN, chunk: int = 50) -> dict[int, ModDetails]:
        '''Details of the given mods; mods that are hidden or removed are left out'''
        found: dict[int, ModDetails] = {}
        ids = sorted({int(modId) for modId in modIds if int(modId) > 0})
        now = int(time.time())
        text = (
            'query($ids: [CompositeDomainWithIdInput!]!, $count: Int) '
            f'{{ legacyModsByDomain(ids: $ids, count: $count) {{ nodes {{ {DETAIL_FIELDS} {REQUIREMENT_FIELDS} }} }} }}'
        )
        for start in range(0, len(ids), chunk):
            part = ids[start : start + chunk]
            data = self.query(
                text, {'ids': [{'gameDomain': game, 'modId': modId} for modId in part], 'count': len(part)}
            )
            for node in (data.get('legacyModsByDomain') or {}).get('nodes') or []:
                try:
                    details = ModDetails.fromGraphQL(node, now)
                except (KeyError, TypeError, ValueError):
                    continue
                found[details.modId] = details
        return found

    def search(
        self, text: str = '', sort: str = 'relevance', count: int = 25, offset: int = 0, updatedSince: int = 0
    ) -> tuple[int, list[ModDetails]]:
        '''Searches Witcher 3 mods by name. Returns (total matches, this page).'''
        filters: dict[str, Any] = {'gameId': [{'value': str(GAME_ID), 'op': 'EQUALS'}]}
        if text.strip():
            filters['nameStemmed'] = [{'value': text.strip(), 'op': 'MATCHES'}]
        elif sort == 'relevance':
            sort = 'endorsements'
        if updatedSince:
            # the search backend only understands unix timestamps here; ISO dates break its query parser
            filters['updatedAt'] = [{'value': str(int(updatedSince)), 'op': 'GTE'}]
        if sort not in SORTS:
            sort = 'relevance'
        query = (
            'query($filter: ModsFilter, $sort: [ModsSort!], $count: Int, $offset: Int) '
            '{ mods(filter: $filter, sort: $sort, count: $count, offset: $offset) '
            f'{{ totalCount nodes {{ {DETAIL_FIELDS} }} }} }}'
        )
        data = self.query(
            query, {'filter': filters, 'sort': [{sort: {'direction': 'DESC'}}], 'count': count, 'offset': offset}
        )
        mods = data.get('mods') or {}
        now = int(time.time())
        results = []
        for node in mods.get('nodes') or []:
            try:
                results.append(ModDetails.fromGraphQL(node, now))
            except (KeyError, TypeError, ValueError):
                continue
        return int(mods.get('totalCount') or 0), results


# ---------------------------------------------------------------- Remastered versions

NOISE = re.compile(
    r"\((?:[^)]*)\)|\[(?:[^]]*)\]|\b(?:remaster(?:ed)?|next[ -]?gen|classic|edition|version|ver|for|the|and|"
    r"complete|ultimate|mod|v?\d+(?:\.\d+)*[a-z]?)\b",
    re.IGNORECASE,
)


# translations of a mod share its name but aren't a replacement for it
TRANSLATION = re.compile(
    r"\b(?:translation|translated|locali[sz]ation|language|polish|chinese|russian|german|french|spanish|italian|"
    r"japanese|korean|portuguese|brazilian|czech|turkish|hungarian|ukrainian|arabic)\b",
    re.IGNORECASE,
)


def searchTerms(name: str) -> str:
    '''The distinctive words of a mod name, for finding other versions of it'''
    words = NOISE.sub(" ", re.sub(r"([a-z])([A-Z])", r"\1 \2", name))
    words = re.sub(r"[^A-Za-z0-9' ]+", " ", words)
    return " ".join(word for word in words.split() if len(word) > 1)


def remasteredSuggestions(graphql: NexusGraphQL, name: str, ownModId: int, limit: int = 3) -> list[ModDetails]:
    '''Other mods with a similar name that were updated for Remastered'''
    terms = searchTerms(name)
    if not terms:
        return []
    key = nameKey(terms)
    translated = bool(TRANSLATION.search(name))
    words = terms.split()
    # search needs every word to match, so long names are retried with their two most distinctive words
    attempts = [terms]
    if len(words) > 2:
        attempts.append(" ".join(sorted(words, key=len, reverse=True)[:2]))
    suggestions: list[ModDetails] = []
    for attempt in attempts:
        _, results = graphql.search(attempt, 'relevance', 15, 0, REMASTERED_RELEASE)
        for result in results:
            if (
                result.modId != ownModId
                and result.updatedForRemastered
                and all(result.modId != found.modId for found in suggestions)
                and (translated or not TRANSLATION.search(result.name))
                and similarity(key, nameKey(searchTerms(result.name))) >= 0.5
            ):
                suggestions.append(result)
        if suggestions:
            break
    return suggestions[:limit]


# ---------------------------------------------------------------- cache


class DetailsCache:
    '''Mod details and thumbnails kept in the configuration folder'''

    def __init__(self, folder: str):
        self.file = os.path.join(folder, 'nexus_details.json')
        self.images = os.path.join(folder, 'thumbnails')
        self.mods: dict[int, ModDetails] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.file, encoding='utf-8') as file:
                stored = json.load(file)
            self.mods = {int(key): ModDetails.fromJson(value) for key, value in stored.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            self.mods = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.file), exist_ok=True)
        temporary = self.file + '.new'
        with open(temporary, 'w', encoding='utf-8') as file:
            json.dump({str(key): asdict(value) for key, value in sorted(self.mods.items())}, file, indent=1)
        os.replace(temporary, self.file)

    def get(self, modId: int) -> ModDetails | None:
        return self.mods.get(int(modId)) if modId else None

    def update(self, details: dict[int, ModDetails]) -> None:
        self.mods.update(details)

    def missing(self, modIds: list[int], maxAge: int = 86400) -> list[int]:
        '''Mods with no details yet, details older than maxAge seconds, or details stored by an older version'''
        now = int(time.time())
        return sorted(
            {
                modId
                for modId in modIds
                if modId
                and (
                    (now - getattr(self.get(modId), 'fetched', 0)) > maxAge
                    or getattr(self.get(modId), 'schema', 0) < DETAILS_SCHEMA
                )
            }
        )

    def imagePath(self, url: str, full: bool = False) -> str:
        '''Where a thumbnail is kept; full pictures go in a folder of their own, as Nexus gives a mod's thumbnail
        and its picture the same file name'''
        if not url:
            return ''
        name = safeFileName(urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(url).path)))
        folder = os.path.join(self.images, 'full') if full else self.images
        return os.path.join(folder, name) if name else ''

    def image(self, details: ModDetails | None) -> str:
        '''The downloaded thumbnail of a mod, or ""'''
        if details is None:
            return ''
        image = self.imagePath(details.thumbnailUrl)
        return image if image and os.path.isfile(image) else ''

    def picture(self, details: ModDetails | None) -> str:
        '''The downloaded full-size picture of a mod, or ""'''
        if details is None:
            return ''
        picture = self.imagePath(details.pictureUrl, full=True)
        return picture if picture and os.path.isfile(picture) else ''

    def bestImage(self, details: ModDetails | None) -> str:
        '''The full-size picture when it's downloaded, else the thumbnail, else ""'''
        return self.picture(details) or self.image(details)

    def downloadPicture(
        self, url: str, opener: Callable[..., Any] = urllib.request.urlopen, timeout: float = 30
    ) -> str:
        '''Downloads a full-size picture once; later calls return the stored file'''
        return self.downloadImage(url, opener, timeout, full=True)

    def downloadImage(
        self, url: str, opener: Callable[..., Any] = urllib.request.urlopen, timeout: float = 30, full: bool = False
    ) -> str:
        '''Downloads an image once; later calls return the stored file'''
        target = self.imagePath(url, full)
        if not target or os.path.isfile(target):
            return target
        os.makedirs(os.path.dirname(target), exist_ok=True)
        request = urllib.request.Request(url, headers={'User-Agent': f"{CLIENT_NAME}/{VERSION}"})
        try:
            with opener(request, timeout=timeout) as response:
                content = response.read()
        except (urllib.error.URLError, OSError) as error:
            raise NexusError(f"Could not download {url}: {error}") from error
        temporary = target + '.part'
        with open(temporary, 'wb') as file:
            file.write(content)
        os.replace(temporary, target)
        return target
