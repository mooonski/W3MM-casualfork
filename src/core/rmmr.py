'''Importing mods and Nexus details from Relapse's Mod Manager Redux (RMMR). RMMR's files are only read.'''

import configparser
import json
import os
import xml.etree.ElementTree as XML
from base64 import b64decode
from dataclasses import dataclass, field
from os import path
from time import gmtime, strftime

from src.core.model import Model
from src.core.nexus import (
    GAME_DOMAIN,
    NexusInfo,
    archiveUploadTime,
    installTimestamp,
    nameKey,
    parseArchiveName,
    similarity,
)
from src.domain.key import Key
from src.domain.mod import Mod
from src.domain.usersetting import Usersetting
from src.globals import data
from src.util.binfiles import safeBinPath
from src.util.util import normalizePath

MENU_PREFIX = "bin/config/r4game/user_config_matrix/pc/"
BASE_INI_PREFIX = "bin/config/base/"

# what the import will do with an RMMR mod
ADD = "add"
LINK = "link"
SKIP = "skip"


class RmmrError(Exception):
    pass


@dataclass
class RmmrMod:
    '''An RMMR inventory entry translated to this manager's format'''

    mod: Mod
    installedAt: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class ImportItem:
    name: str
    action: str
    reason: str = ''
    mod: Mod | None = None
    nexus: NexusInfo | None = None
    target: str = ''
    notes: list[str] = field(default_factory=list)


@dataclass
class ImportPlan:
    items: list[ImportItem]
    warnings: list[str] = field(default_factory=list)

    def count(self, action: str) -> int:
        return sum(1 for item in self.items if item.action == action)


def isRmmrFolder(folder: str) -> bool:
    return path.isfile(path.join(folder, "installed.xml")) and path.isfile(path.join(folder, "mod_metadata.json"))


def readInventory(folder: str) -> list[RmmrMod]:
    '''Reads RMMR's installed.xml'''
    try:
        root = XML.parse(path.join(folder, "installed.xml")).getroot()
    except (OSError, XML.ParseError) as error:
        raise RmmrError(f"Could not read RMMR's installed.xml: {error}") from error
    if root.tag != "installed":
        raise RmmrError("RMMR's installed.xml has an unexpected format")
    mods = []
    for element in root.findall("mod"):
        name = element.get("name") or ''
        if name:
            mods.append(translateEntry(element))
    return mods


def translateEntry(element: XML.Element) -> RmmrMod:
    mod = Mod()
    mod._name = element.get("name") or ''
    mod.enabled = element.get("enabled") == "True"
    mod.date = element.get("date") or mod.date
    priority = element.get("priority") or ''
    if priority.isdecimal():
        mod._priority = priority
    notes: list[str] = []

    def text(child: XML.Element) -> str:
        return (child.text or '').strip()

    for child in element:
        value = text(child)
        if child.tag == "data" and value:
            mod.files.append(value)
        elif child.tag == "dlc" and value:
            mod.dlcs.append(value)
        elif child.tag == "menu":
            menu = normalizePath(child.get("path") or value)
            if menu.casefold().startswith(MENU_PREFIX):
                menu = menu[len(MENU_PREFIX) :]
            if menu and "/" not in menu and menu not in mod.menus:
                mod.menus.append(menu)
        elif child.tag == "ini":
            ini = normalizePath(child.get("path") or '')
            if ini.casefold().startswith(BASE_INI_PREFIX):
                name = ini[len(BASE_INI_PREFIX) :]
                if name not in mod.inis:
                    mod.inis.append(name)
            note = f"RMMR merged settings into {ini}; they stay as they are"
            if ini and note not in notes:
                notes.append(note)
        elif child.tag == "bin" and value:
            relative = normalizePath(value)
            relative = safeBinPath(relative[4:] if relative.casefold().startswith("bin/") else "")
            if relative and relative not in mod.bins:
                mod.bins.append(relative)
            elif not relative:
                notes.append(f"{value} isn't tracked here")
        elif child.tag == "xmlkey" and value:
            mod.xmlkeys.append(value)
        elif child.tag == "hidden" and value:
            mod.hidden.append(value)
        elif child.tag == "key" and value and child.get("context"):
            mod.inputsettings.append(Key(str(child.get("context")), value))
        elif child.tag == "setting" and "=" in value and child.get("context"):
            mod.usersettings.append(Usersetting(str(child.get("context")), value))
        elif child.tag == "readmeb64" and value:
            try:
                mod.readmes.append(b64decode(value.encode("ascii")).decode("utf-8"))
            except Exception:
                pass
        elif child.tag == "readme" and value:
            mod.readmes.append(value)
    return RmmrMod(mod, installTimestamp(mod.date), notes)


def readMetadata(folder: str) -> dict[str, dict]:
    try:
        with open(path.join(folder, "mod_metadata.json"), encoding="utf-8") as file:
            metadata = json.load(file)
    except (OSError, ValueError) as error:
        raise RmmrError(f"Could not read RMMR's mod_metadata.json: {error}") from error
    return {str(name): value for name, value in metadata.items() if isinstance(value, dict)}


def readGamePath(folder: str) -> str:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path.join(folder, "config.ini"), encoding="utf-8")
    except (OSError, configparser.Error):
        return ''
    return parser.get("PATHS", "gameexe", fallback='')


# an archive or download record only counts for a mod if its name is at least this similar
EVIDENCE_SIMILARITY = 0.6


@dataclass
class Download:
    '''A Nexus file the user had on disk, used to work out which file RMMR installed'''

    modId: int
    name: str
    time: int
    archive: str
    fileId: int = 0
    version: str = ''


def nexusFromMetadata(metadata: dict, entry: RmmrMod, downloads: list[Download]) -> NexusInfo | None:
    modId = str(metadata.get("nexus_mod_id") or '')
    if not modId.isdecimal() or int(modId) <= 0:
        return None
    game = str(metadata.get("nexus_game_domain") or GAME_DOMAIN)
    fileId = str(metadata.get("nexus_file_id") or '')
    info = NexusInfo(
        modId=int(modId),
        fileId=int(fileId) if fileId.isdecimal() else 0,
        version=str(metadata.get("installed_version") or ''),
        game=game,
    )
    if info.fileId:
        return info
    installedAt = max(entry.installedAt, int(metadata.get("installed_at") or 0))
    download = pickDownload(downloads, info.modId, entry.mod.name, installedAt)
    if download:
        info.archive = download.archive
        info.fileId = download.fileId
        info.version = info.version or download.version
    return info


def listDownloads(archiveFolders: list[str], rmmrFolder: str = '') -> list[Download]:
    '''Nexus-named archives in the given folders, plus the records of the Nexus Updater companion if present'''
    downloads: list[Download] = []
    for folder in archiveFolders:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            parsed = parseArchiveName(name)
            if parsed:
                downloads.append(Download(parsed[1], parsed[0], archiveUploadTime(name), name, version=parsed[2]))
    state = path.join(rmmrFolder, "NexusUpdater", "state.json") if rmmrFolder else ''
    if state and path.isfile(state):
        try:
            with open(state, encoding="utf-8-sig") as file:
                records = json.load(file).get("downloads") or {}
        except (OSError, ValueError, AttributeError):
            records = {}
        for fileId, record in records.items() if isinstance(records, dict) else []:
            try:
                downloads.append(
                    Download(
                        int(record["modId"]),
                        str(record.get("name") or ''),
                        int(record.get("downloadedAt") or 0),
                        path.basename(str(record.get("path") or '').replace("\\", "/")),
                        int(fileId),
                        str(record.get("version") or ''),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    return downloads


def pickDownload(downloads: list[Download], modId: int, name: str, installedAt: int) -> Download | None:
    '''The download most likely installed: same mod, similar name, newest from before the install'''
    candidates = []
    for download in downloads:
        if download.modId != modId or (installedAt and download.time > installedAt):
            continue
        match = similarity(nameKey(download.name), nameKey(name))
        if match < EVIDENCE_SIMILARITY:
            continue
        candidates.append((round(match, 1), download.time, bool(download.fileId), download))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[:3])[3]


def planImport(folder: str, model: Model, archiveFolders: list[str]) -> ImportPlan:
    '''Works out what importing from the RMMR folder would do, without changing anything'''
    if not isRmmrFolder(folder):
        raise RmmrError("This folder has no RMMR installed.xml and mod_metadata.json")
    config = data.getConfig()
    entries = readInventory(folder)
    metadata = readMetadata(folder)
    downloads = listDownloads(archiveFolders, folder)
    warnings = []

    rmmrGame = readGamePath(folder)
    if rmmrGame and config.game:
        rmmrRoot = normalizePath(path.dirname(path.dirname(path.dirname(rmmrGame)))).casefold()
        if rmmrRoot != normalizePath(config.game).casefold():
            warnings.append(
                f"RMMR manages a different game folder ({rmmrRoot}). Only mods whose files are in "
                f"{normalizePath(config.game)} can be added."
            )

    owned: dict[str, str] = {}
    for existing in model.all():
        for folderName in existing.files + existing.dlcs:
            owned[folderName.casefold()] = existing.name

    items = []
    for entry in entries:
        mod = entry.mod
        nexus = nexusFromMetadata(metadata.get(mod.name, {}), entry, downloads)
        item = ImportItem(mod.name, SKIP, nexus=nexus, notes=list(entry.notes))
        items.append(item)
        existing = model.modList.get(mod.name) or sameFolders(model, mod)
        if existing is not None:
            item.target = existing.name
            if existing.nexus and existing.nexus.modId:
                item.reason = "Already linked to Nexus"
            elif nexus is None:
                item.reason = "Already here; RMMR has no Nexus ID for it"
            else:
                item.action = LINK
                item.reason = "Already here; adds its Nexus ID" + (
                    f" (as {existing.name})" if existing.name != mod.name else ''
                )
            continue
        claimed = sorted({owned[name.casefold()] for name in mod.files + mod.dlcs if name.casefold() in owned})
        if claimed:
            item.reason = "Its folders belong to " + ", ".join(claimed)
            continue
        present, enabled = installedState(mod)
        if not present:
            item.reason = "Not installed in this game folder"
            continue
        if enabled is not None and enabled != mod.enabled:
            item.notes.append(
                "RMMR lists it as " + ("enabled" if mod.enabled else "disabled") + " but the files are "
                + ("enabled" if enabled else "disabled") + "; using the files"
            )  # fmt: skip
            mod.enabled = enabled
        mod.nexus = nexus
        item.action = ADD
        item.mod = mod
        item.reason = "Adds it" + ("" if nexus else " (RMMR has no Nexus ID for it)")
    return ImportPlan(items, warnings)


def sameFolders(model: Model, mod: Mod) -> Mod | None:
    folders = {name.casefold() for name in mod.files + mod.dlcs}
    if not folders:
        return None
    for existing in model.all():
        if {name.casefold() for name in existing.files + existing.dlcs} == folders:
            return existing
    return None


def installedState(mod: Mod) -> tuple[bool, bool | None]:
    '''Whether any of the mod's files are in the game folder, and whether they're enabled (None if unclear)'''
    config = data.getConfig()
    states: set[bool] = set()
    for name in mod.files if config.mods else []:
        if path.isdir(path.join(config.mods, name)):
            states.add(True)
        if path.isdir(path.join(config.mods, "~" + name)):
            states.add(False)
    for name in mod.dlcs if config.dlc else []:
        folder = path.join(config.dlc, name)
        if path.isdir(folder):
            # disabled DLC folders keep their name, only the files inside get ".disabled"
            states.add(not hasDisabledFiles(folder))
    if not mod.files and not mod.dlcs:
        # a menu-only mod; mods with folders are judged by their folders, since menu files can be shared
        for menu in mod.menus if config.menu else []:
            if Mod.isProtectedMenu(menu):
                continue
            for filename, enabled in ((menu, True), (menu + ".disabled", False)):
                if path.isfile(path.join(config.menu, filename)):
                    states.add(enabled)
    if not states and not mod.files and not mod.dlcs and not mod.menus and mod.inis and config.game:
        # a mod that only changes settings in the game's ini files is installed as long as those files exist;
        # whether it's enabled can't be told from them, so RMMR's record is kept
        base = path.join(config.game, "bin", "config", "base")
        if any(path.isfile(path.join(base, ini)) for ini in mod.inis):
            return True, None
    return bool(states), (states.pop() if len(states) == 1 else None)


def hasDisabledFiles(folder: str) -> bool:
    for _, _, files in os.walk(folder):
        if any(file.endswith(".disabled") for file in files):
            return True
    return False


def applyImport(plan: ImportPlan, model: Model) -> tuple[int, int]:
    '''Adds and links the planned mods. Returns (added, linked).'''
    added = linked = 0
    priorities = False
    for item in plan.items:
        if item.action == ADD and item.mod is not None and item.name not in model.modList:
            item.mod.date = item.mod.date or strftime("%Y-%m-%d %H:%M:%S", gmtime())
            model.modList[item.name] = item.mod
            if item.mod._priority:
                item.mod.priority = item.mod._priority
                priorities = True
            item.mod.checkPriority()
            added += 1
        elif item.action == LINK and item.nexus is not None and item.target in model.modList:
            model.modList[item.target].nexus = item.nexus
            linked += 1
    if priorities:
        data.getConfig().write_priority()
    if added or linked:
        model.write()
    return added, linked
