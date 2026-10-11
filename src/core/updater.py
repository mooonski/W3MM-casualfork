'''Updating installed mods in place, with a backup that is restored if anything fails'''

import json
import os
import shutil
import xml.etree.ElementTree as XML
from dataclasses import asdict, dataclass
from os import path
from shutil import copyfileobj
from time import gmtime, strftime
from typing import Any, Callable

from src.core.installer import Installer
from src.core.model import Model
from src.core.nexus import NexusInfo, safeFileName
from src.domain.mod import Mod
from src.globals import data
from src.globals.constants import translate
from src.util.syntax import indent
from src.util.util import atomicWrite, checkInstalledPath, formatUserError, removeDirectory, removeInstalledFile

SETTINGS_FILES = ("user.settings", "dx12user.settings", "input.settings", "mods.settings")
MENU_DATA = path.join("data", "bin", "config", "r4game", "user_config_matrix", "pc")
BASE_DATA = path.join("data", "bin", "config", "base")


class UpdateFailed(Exception):
    pass


@dataclass
class UpdateResult:
    success: bool
    incomplete: bool
    backup: str


@dataclass
class Backup:
    '''A saved copy of an installed mod'''

    directory: str
    name: str
    created: str
    reason: str
    version: str

    def label(self) -> str:
        text = self.created
        if self.version:
            text += "  " + self.version
        if self.reason:
            text += f"  ({self.reason})"
        return text


class ModUpdater:
    '''Replaces an installed mod's files with a new version while keeping its entry, priority and state.

    Before anything changes, the mod's folders, menu files and the shared settings files are saved to a
    backup folder. If the update fails, everything is put back exactly as it was.'''

    def __init__(
        self,
        model: Model,
        output: Callable[[str], Any] = lambda _: None,
        progress: Callable[[float], Any] = lambda _: None,
    ):
        self.model = model
        self.output = output
        self.progress = progress

    # ------------------------------------------------------------ public

    def update(self, mod: Mod, source: str, nexus: NexusInfo | None = None, reason: str = "update") -> UpdateResult:
        '''Installs source (an archive or folder) into mod's entry'''
        return self._replace(mod, source, nexus, reason, None)

    def restore(self, backup: Backup) -> UpdateResult:
        '''Reinstalls a backed up version. The version being replaced is backed up first.'''
        entry = self.readEntry(backup.directory)
        source = path.join(backup.directory, "data")
        current = self.model.modList.get(entry.name)
        if current is None:
            return self._installRestored(entry, source)
        return self._replace(current, source, entry.nexus, "restore", entry)

    def listBackups(self, name: str | None = None) -> list[Backup]:
        '''Backups of the named mod (or all mods), newest first'''
        root = data.getConfig().backups
        backups: list[Backup] = []
        if not path.isdir(root):
            return backups
        for modDirectory in os.listdir(root):
            if name is not None and modDirectory != self._folderName(name):
                continue
            parent = path.join(root, modDirectory)
            if not path.isdir(parent):
                continue
            for stamp in os.listdir(parent):
                directory = path.join(parent, stamp)
                try:
                    with open(path.join(directory, "backup.json"), encoding="utf-8") as file:
                        meta = json.load(file)
                except (OSError, ValueError):
                    continue
                if name is not None and meta.get("name") != name:
                    continue
                backups.append(
                    Backup(
                        directory,
                        str(meta.get("name", "")),
                        str(meta.get("created", stamp)),
                        str(meta.get("reason", "")),
                        str(meta.get("version", "")),
                    )
                )
        return sorted(backups, key=lambda b: b.created, reverse=True)

    @staticmethod
    def readEntry(directory: str) -> Mod:
        root = XML.parse(path.join(directory, "entry.xml")).getroot()
        element = root.find("mod")
        if element is None:
            raise UpdateFailed("The backup has no mod entry")
        return Model.populateModFromXml(Mod(), element)

    # ------------------------------------------------------------ the update

    def _replace(self, mod: Mod, source: str, nexus: NexusInfo | None, reason: str, entry: Mod | None) -> UpdateResult:
        name = mod.name
        wasEnabled = mod.enabled
        priority = mod._priority
        backup = self.createBackup(mod, reason)
        self.output(translate("MainWindow", "Backed up") + f" {name}: {backup}")
        before: dict[str, set[str]] | None = None
        try:
            self.progress(0.1)
            if not wasEnabled:
                # stored backups are always in the enabled layout, so they can be reinstalled
                mod.enable()
            mod.uninstallXmlKeys()
            mod.uninstallMenus()
            self._stash(mod, backup)
            before = self._listing()
            self.model.modList.pop(name, None)
            self.progress(0.3)

            installer = Installer(self.model, keepUserSettings=True, output=self.output)
            installer.progress = lambda p: self.progress(0.3 + 0.6 * p)
            success, _, incomplete = installer.installMod(source, name=name)
            if not success or name not in self.model.modList:
                raise UpdateFailed(translate("MainWindow", "The new version could not be installed"))
            new = self.model.get(name)
            if entry is not None:
                incomplete = self._applyEntry(new, entry) or incomplete
            new.nexus = nexus if nexus is not None else mod.nexus
            new.group = mod.group
            new.listorder = mod.listorder
            if priority is not None and str(priority).isdecimal():
                new.priority = priority
                data.getConfig().write_priority()
            if not wasEnabled:
                new.disable()
            self.model.write()
            self.progress(1.0)
            return UpdateResult(True, bool(incomplete), backup)
        except Exception as error:
            self.output(formatUserError(error))
            self.output(translate("MainWindow", "Restoring the previous version of") + f" {name}")
            try:
                self._rollback(mod, wasEnabled, backup, before)
                self.output(translate("MainWindow", "Restored") + f" {name}")
            except Exception as rollbackError:
                self.output(formatUserError(rollbackError))
                self.output(
                    translate("MainWindow", "Could not fully restore")
                    + f" {name}. "
                    + translate("MainWindow", "The backup is kept in")
                    + f" {backup}"
                )
            return UpdateResult(False, False, backup)

    def _installRestored(self, entry: Mod, source: str) -> UpdateResult:
        installer = Installer(self.model, keepUserSettings=True, output=self.output)
        installer.progress = self.progress
        success, _, incomplete = installer.installMod(source, name=entry.name)
        if not success or entry.name not in self.model.modList:
            return UpdateResult(False, False, "")
        new = self.model.get(entry.name)
        incomplete = self._applyEntry(new, entry) or incomplete
        new.nexus = entry.nexus
        new.group = entry.group
        new.listorder = entry.listorder
        self.model.write()
        return UpdateResult(True, bool(incomplete), "")

    def _applyEntry(self, mod: Mod, entry: Mod) -> bool:
        '''Puts back the settings, keys and readmes a backup recorded (they aren't stored as files)'''
        mod.usersettings = list(entry.usersettings)
        mod.xmlkeys = list(entry.xmlkeys)
        mod.hidden = list(entry.hidden)
        mod.inputsettings = list(entry.inputsettings)
        mod.readmes = list(entry.readmes)
        incomplete = False
        for install in (mod.installXmlKeys, lambda: mod.installUserSettings(True), mod.installInputKeys):
            try:
                install()
            except Exception as error:
                incomplete = True
                self.output(formatUserError(error))
        return incomplete

    # ------------------------------------------------------------ backups

    @staticmethod
    def _folderName(name: str) -> str:
        return safeFileName(name) or "mod"

    def createBackup(self, mod: Mod, reason: str) -> str:
        config = data.getConfig()
        parent = path.normpath(path.join(config.backups, self._folderName(mod.name)))
        stamp = strftime("%Y%m%d-%H%M%S", gmtime())
        directory = path.join(parent, stamp)
        number = 2
        while path.exists(directory):
            directory = path.join(parent, f"{stamp}-{number}")
            number += 1
        os.makedirs(directory)

        installed = XML.Element("installed")
        tree = Model.writeModToXml(mod, XML.ElementTree(installed))
        indent(installed)
        with open(path.join(directory, "entry.xml"), "wb") as file:
            tree.write(file, encoding="utf-8", xml_declaration=True)

        meta = {
            "name": mod.name,
            "created": strftime("%Y-%m-%d %H:%M:%S", gmtime()),
            "reason": reason,
            "enabled": mod.enabled,
            "priority": mod._priority,
            "version": mod.nexus.version if mod.nexus else "",
            "nexus": asdict(mod.nexus) if mod.nexus else None,
        }
        with open(path.join(directory, "backup.json"), "w", encoding="utf-8") as file:
            json.dump(meta, file, indent=2)

        self._snapshotFiles(config.menu, path.join(directory, "snapshot", "menu"))
        self._snapshotFiles(self._baseDirectory(), path.join(directory, "snapshot", "base"), ".ini")
        settings = path.join(directory, "snapshot", "settings")
        os.makedirs(settings, exist_ok=True)
        for filename in SETTINGS_FILES:
            source = path.join(config.settings, filename)
            if path.isfile(source):
                shutil.copy2(source, path.join(settings, filename))
        return directory

    @staticmethod
    def _baseDirectory() -> str:
        return path.join(data.getConfig().game, "bin", "config", "base")

    @staticmethod
    def _snapshotFiles(directory: str, target: str, extension: str = "") -> None:
        os.makedirs(target, exist_ok=True)
        if not directory or not path.isdir(directory):
            return
        for filename in os.listdir(directory):
            source = path.join(directory, filename)
            if path.isfile(source) and filename.lower().endswith(extension):
                shutil.copy2(source, path.join(target, filename))

    def _stash(self, mod: Mod, backup: str) -> None:
        '''Moves the mod's folders and menu files out of the game into the backup'''
        config = data.getConfig()
        for base, names, kind in ((config.mods, mod.files, "mods"), (config.dlc, mod.dlcs, "dlc")):
            if not base:
                continue
            for name in names:
                for installed in (name, "~" + name):
                    source = path.join(base, installed)
                    if not path.isdir(source):
                        continue
                    checkInstalledPath(source, modDirectory=True)
                    target = path.join(backup, "data", kind, name)
                    if path.exists(target):
                        target = path.join(backup, "extra", kind, installed)
                    os.makedirs(path.dirname(target), exist_ok=True)
                    shutil.move(source, target)
        if config.menu:
            for menu in mod.menus:
                if Mod.isProtectedMenu(menu):
                    continue
                source = path.join(config.menu, menu)
                if path.isfile(source):
                    target = path.join(backup, MENU_DATA, menu)
                    os.makedirs(path.dirname(target), exist_ok=True)
                    shutil.copy2(source, target)
                    removeInstalledFile(source)
        for ini in mod.inis:
            source = path.join(self._baseDirectory(), ini)
            if path.isfile(source):
                target = path.join(backup, BASE_DATA, ini)
                os.makedirs(path.dirname(target), exist_ok=True)
                shutil.copy2(source, target)
        for relative in mod.bins:
            source = Mod.binTarget(relative)
            if path.isfile(source):
                target = path.join(backup, "data", "bin", *relative.split("/"))
                os.makedirs(path.dirname(target), exist_ok=True)
                shutil.copy2(source, target)
                removeInstalledFile(source)

    @staticmethod
    def _listing() -> dict[str, set[str]]:
        config = data.getConfig()
        return {
            "mods": set(os.listdir(config.mods)) if config.mods else set(),
            "dlc": set(os.listdir(config.dlc)) if config.dlc else set(),
            "bin": ModUpdater._binListing(),
        }

    @staticmethod
    def _binListing() -> set[str]:
        """Files in the game's bin folder outside bin/config, as paths like "x64_dx12/name.addon64" """
        root = path.join(data.getConfig().game or "", "bin")
        found: set[str] = set()
        if not data.getConfig().game or not path.isdir(root):
            return found
        for folder, directories, files in os.walk(root):
            if path.normcase(folder) == path.normcase(root):
                directories[:] = [name for name in directories if name.casefold() != "config"]
            for name in files:
                found.add(path.relpath(path.join(folder, name), root).replace(os.sep, "/"))
        return found

    def _rollback(self, mod: Mod, wasEnabled: bool, backup: str, before: dict[str, set[str]] | None) -> None:
        config = data.getConfig()
        bases = {"mods": config.mods, "dlc": config.dlc}
        # 1. remove whatever the failed install created
        if before is not None:
            for kind, base in bases.items():
                if not base:
                    continue
                for name in set(os.listdir(base)) - before[kind]:
                    removeDirectory(path.join(base, name))
            for relative in self._binListing() - before.get("bin", set()):
                removeInstalledFile(Mod.binTarget(relative))
        # 2. move the stashed folders back
        for folder in ("data", "extra"):
            for kind, base in bases.items():
                stashed = path.join(backup, folder, kind)
                if not base or not path.isdir(stashed):
                    continue
                for name in os.listdir(stashed):
                    target = checkInstalledPath(path.join(base, name), modDirectory=True)
                    if path.exists(target):
                        removeDirectory(target)
                    shutil.copytree(path.join(stashed, name), target)
        stashed = path.join(backup, "data", "bin")
        if path.isdir(stashed):
            for folder, directories, files in os.walk(stashed):
                if path.normcase(folder) == path.normcase(stashed):
                    directories[:] = [name for name in directories if name.casefold() != "config"]
                for name in files:
                    relative = path.relpath(path.join(folder, name), stashed).replace(os.sep, "/")
                    target = checkInstalledPath(Mod.binTarget(relative), modDirectory=False)
                    os.makedirs(path.dirname(target), exist_ok=True)
                    with open(path.join(folder, name), "rb") as original, atomicWrite(target, "wb") as copy:
                        copyfileobj(original, copy)
        # 3. put the entry back the way it was
        self.model.modList[mod.name] = mod
        if not wasEnabled:
            mod.disable()
        # 4. shared files exactly as they were before the update
        self._restoreFiles(path.join(backup, "snapshot", "menu"), config.menu)
        self._restoreFiles(path.join(backup, "snapshot", "base"), self._baseDirectory(), ".ini")
        settings = path.join(backup, "snapshot", "settings")
        for filename in SETTINGS_FILES:
            source = path.join(settings, filename)
            if path.isfile(source):
                with (
                    open(source, "rb") as original,
                    atomicWrite(path.join(config.settings, filename), "wb", follow_symlinks=True) as target,
                ):
                    copyfileobj(original, target)
        config.readPriority()
        self.model.write()

    @staticmethod
    def _restoreFiles(snapshot: str, directory: str, extension: str = "") -> None:
        if not directory or not path.isdir(snapshot):
            return
        saved = set(os.listdir(snapshot))
        if path.isdir(directory):
            for filename in os.listdir(directory):
                current = path.join(directory, filename)
                if filename not in saved and path.isfile(current) and filename.lower().endswith(extension):
                    removeInstalledFile(current)
        os.makedirs(directory, exist_ok=True)
        for filename in saved:
            target = checkInstalledPath(path.join(directory, filename), modDirectory=False)
            with open(path.join(snapshot, filename), "rb") as original, atomicWrite(target, "wb") as copy:
                copyfileobj(original, copy)
