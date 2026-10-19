'''Health checks: problems with installed mods that are worth knowing about'''

import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from os import path

from src.core.duplicates import duplicateEntries
from src.core.model import Model
from src.core.nexusdetails import ModDetails
from src.core.requirements import EXTERNAL, missingRequirements
from src.core.reshade import ADDON_BUILD, BIN_FOLDERS, ReShadeInstall, addonFiles, findReShade
from src.core.reshade import DISABLED as RESHADE_DISABLED
from src.core.reshade import HOME_PAGE as RESHADE_HOME
from src.core.scriptmerger import MERGED_FOLDER, ConflictReport
from src.core.sharedutils import FIXED_PRIORITY, SharedutilsCheck, checkSharedutils, fixedPriorities
from src.domain.mod import Mod

ERROR = "error"
WARNING = "warning"
INFO = "info"
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass
class Fix:
    '''A change that solves an issue, described before it's made'''

    summary: str
    apply: Callable[[], None]


@dataclass
class Issue:
    severity: str
    mod: str
    problem: str
    detail: str = ''
    url: str = ''
    fix: Fix | None = None


def checkHealth(
    model: Model,
    config,
    scripts: ConflictReport | None = None,
    details: Callable[[int], ModDetails | None] | None = None,
) -> list[Issue]:
    '''Looks for problems in the installed mods. Only reads files (and already loaded Nexus details).'''
    issues: list[Issue] = []
    if details is not None and getattr(config, "gameversion", "") == "re":
        for mod in model.all():
            known = details(mod.nexus.modId) if mod.nexus and mod.nexus.modId else None
            if known is not None and not known.updatedForRemastered:
                updated = time.strftime("%Y-%m-%d", time.gmtime(known.updated))
                issues.append(
                    Issue(
                        WARNING,
                        mod.name,
                        "Not updated since Remastered",
                        f"Last updated on Nexus {updated} (Nexus > Check Remastered Versions looks for other versions)",
                        known.url,
                    )
                )
    if details is not None:
        for missing in missingRequirements(model, details, getattr(config, "dlc", "") or ''):
            notes = missing.requirement.notes.strip()
            if missing.state == EXTERNAL:
                detail = "Listed on its Nexus page; it can't be checked from here" + (f": {notes}" if notes else "")
            else:
                detail = (notes + " " if notes else "") + "(listed as a requirement on its Nexus page)"
            issues.append(
                Issue(
                    INFO if missing.state == EXTERNAL else WARNING,
                    missing.mod,
                    missing.problem,
                    detail,
                    missing.requirement.pageUrl,
                )
            )
    mods = config.mods or ''
    dlc = config.dlc or ''
    menu = config.menu or ''
    owned: set[str] = set()

    for mod in sorted(model.all(), key=lambda m: m.name.lower()):
        for name in mod.files:
            owned.add(name.casefold())
            issues += _folderIssues(mod, mods, name)
        for name in mod.dlcs:
            if dlc and not path.isdir(path.join(dlc, name)) and not path.isdir(path.join(dlc, "~" + name)):
                issues.append(Issue(ERROR, mod.name, "DLC folder is missing", f"DLC/{name} isn't in the game folder"))
        for name in mod.menus:
            if not menu or Mod.isProtectedMenu(name):
                continue
            if not path.isfile(path.join(menu, name)) and not path.isfile(path.join(menu, name + ".disabled")):
                issues.append(
                    Issue(WARNING, mod.name, "Menu file is missing", f"{name} isn't in the game's menu folder")
                )
        game = getattr(config, "game", "") or ""
        for relative in mod.bins if game else []:
            target = Mod.binTarget(relative)
            if not path.isfile(target) and not path.isfile(target + ".disabled"):
                issues.append(
                    Issue(WARNING, mod.name, "Game file is missing", f"bin/{relative} isn't in the game folder")
                )
        if not mod.files and not mod.dlcs and not mod.menus and not mod.inis and not mod.bins:
            issues.append(Issue(WARNING, mod.name, "Entry has no files", "Nothing is installed for it"))

    for name, reasons in sorted(duplicateEntries(model).items(), key=lambda item: item[0].lower()):
        for reason in reasons:
            issues.append(Issue(WARNING, name, "Installed twice?", f"{name} {reason}"))

    if mods and path.isdir(mods):
        merged = MERGED_FOLDER.casefold()
        for name in sorted(os.listdir(mods), key=str.casefold):
            plain = name[1:] if name.startswith("~") else name
            if not path.isdir(path.join(mods, name)) or not re.match(r"^mod", plain, re.IGNORECASE):
                continue
            if plain.casefold() in owned or plain.casefold() == merged:
                continue
            issues.append(
                Issue(
                    INFO,
                    "",
                    "Folder isn't managed here",
                    f"Mods/{name} isn't part of any mod in the list (installed by hand or by another manager)",
                )
            )

    priority = getattr(config, "priority", None)
    if priority is not None and mods and path.isdir(mods):
        installed = {name.lstrip("~").casefold() for name in os.listdir(mods)}
        for section in priority.sections():
            if section.casefold() not in installed and re.match(r"^mod", section, re.IGNORECASE):
                issues.append(
                    Issue(
                        INFO,
                        "",
                        "Leftover priority",
                        f"mods.settings sets a priority for {section}, which isn't installed",
                    )
                )

    if mods and path.isdir(mods):
        issues += sharedutilsIssues(model, config)

    game = getattr(config, "game", "") or ""
    if isinstance(game, str) and game:
        issues += reshadeIssues(game)

    if scripts is not None:
        pending = scripts.pending
        if pending:
            issues.append(
                Issue(
                    WARNING,
                    "",
                    "Scripts need Script Merger",
                    f"{len(pending)} script files aren't merged or the merge is out of date (Mods > Script Conflicts)",
                )
            )
    return sorted(issues, key=lambda issue: (SEVERITY_ORDER[issue.severity], issue.mod.lower(), issue.problem))


def sharedutilsIssues(model: Model, config, involving: set[str] | None = None) -> list[Issue]:
    '''Mods built on Sharedutils that Remastered can't compile: Sharedutils is missing, or they load after it.
    With involving (mod names), only the issues about those mods, or all of them when Sharedutils is one.'''
    mods = config.mods or ''
    if not mods or not path.isdir(mods):
        return []
    priority = getattr(config, "getPriority", None) or _priorityReader(config)
    check = checkSharedutils(mods, priority)
    names = {}
    for mod in model.all():
        for folder in mod.files:
            names.setdefault(folder.casefold(), mod.name)

    def name(folder: str) -> str:
        return names.get(folder.casefold(), folder)

    issues = []
    if not check.sharedutils:
        for folder, uses in check.dependents.items():
            issues.append(
                Issue(
                    WARNING,
                    name(folder),
                    "Needs Sharedutils",
                    f"Mods/{folder} uses {_names(uses)} from Sharedutils, which isn't installed or is disabled, "
                    "so the game's scripts won't compile",
                )
            )
    elif check.late and getattr(config, "gameversion", "") == "re":
        changes = fixedPriorities(check)
        fix = Fix(_fixSummary(check, changes), lambda: _applyPriorities(config, changes)) if changes else None
        sharedutils = check.sharedutils
        own = check.priorities.get(sharedutils)
        for folder in check.late:
            number = check.priorities.get(folder)
            if number is None:
                has = "it has none, so it loads after every numbered mod"
            elif number == own:
                has = f"it has the same number, {number}, and comes after it by name"
            else:
                has = f"it has {number}"
            issues.append(
                Issue(
                    WARNING,
                    name(folder),
                    "Loads after Sharedutils",
                    f"Remastered compiles scripts in load order and Mods/{folder} builds on Sharedutils "
                    f"({_names(check.dependents[folder])}), so it needs a lower priority number than {sharedutils} "
                    f"({_numberText(own)}); {has}. Give Sharedutils the highest number (e.g. {FIXED_PRIORITY}) "
                    "and this mod a lower one (Fix does that)",
                    fix=fix,
                )
            )
    if involving is not None:
        if not check.sharedutils or name(check.sharedutils) not in involving:
            issues = [issue for issue in issues if issue.mod in involving]
    return issues


def reshadeIssues(game: str, find: Callable[[str], list[ReShadeInstall]] = findReShade) -> list[Issue]:
    '''ReShade add-ons that can't load: ReShade is missing, disabled, or the build that refuses them'''
    issues = []
    installs = {install.binFolder: install for install in find(game)}
    for binFolder in BIN_FOLDERS:
        install = installs.get(binFolder)
        addons = install.addons if install else addonFiles(path.join(game, "bin", binFolder))
        where = f"bin/{binFolder}"
        if install is not None and install.duplicate:
            issues.append(
                Issue(
                    WARNING,
                    "ReShade",
                    "ReShade is there twice",
                    f"Both {where}/{install.name} and {install.name}{RESHADE_DISABLED} exist; one is left over",
                )
            )
        if not addons:
            continue
        names = _names(addons)
        if install is None:
            issues.append(
                Issue(
                    WARNING,
                    "ReShade",
                    "ReShade add-ons can't load",
                    f"{where} has {names}, which need ReShade with add-on support; it isn't installed there",
                    RESHADE_HOME,
                )
            )
        elif not install.enabled:
            issues.append(
                Issue(
                    INFO,
                    "ReShade",
                    "ReShade is disabled",
                    f"{names} in {where} won't load until it's enabled again (Mods > ReShade...)",
                )
            )
        elif install.build != ADDON_BUILD:
            issues.append(
                Issue(
                    WARNING,
                    "ReShade",
                    "ReShade can't load these add-ons",
                    f"The standard build of ReShade in {where} only loads a few approved add-ons, so {names} "
                    "may not load. Install the build \"with full add-on support\" from reshade.me",
                    RESHADE_HOME,
                )
            )
    return issues


def _priorityReader(config) -> Callable[[str], str | None]:
    parser = getattr(config, "priority", None)

    def read(section: str) -> str | None:
        if parser is not None and parser.has_section(section) and parser.has_option(section, "priority"):
            return parser.get(section, "priority")
        return None

    return read


def _names(uses: list[str]) -> str:
    shown = ", ".join(uses[:3])
    return shown + (f" and {len(uses) - 3} more" if len(uses) > 3 else "")


def _numberText(number: int | None) -> str:
    return "no priority" if number is None else f"priority {number}"


def _fixSummary(check: SharedutilsCheck, changes: dict[str, int]) -> str:
    lines = [
        f"  {folder}: {_numberText(check.priorities.get(folder))} → {number}"
        for folder, number in sorted(changes.items(), key=lambda item: (item[1], item[0].casefold()))
    ]
    return "Sets these priorities in mods.settings, so the mods built on Sharedutils load before it:\n" + "\n".join(
        lines
    )


def _applyPriorities(config, changes: dict[str, int]) -> None:
    for folder, number in changes.items():
        config.setPriority(folder, str(number))
    # the write is debounced onto a timer; wait for it so mods.settings is written when this returns
    writing = config.write_priority()
    if hasattr(writing, "join"):
        writing.join()


def _folderIssues(mod: Mod, mods: str, name: str) -> list[Issue]:
    if not mods:
        return []
    enabledCopy = path.isdir(path.join(mods, name))
    disabledCopy = path.isdir(path.join(mods, "~" + name))
    if not enabledCopy and not disabledCopy:
        return [Issue(ERROR, mod.name, "Mod folder is missing", f"Mods/{name} isn't in the game folder")]
    if enabledCopy and disabledCopy:
        return [
            Issue(
                WARNING, mod.name, "Folder is there twice", f"Both Mods/{name} and Mods/~{name} exist; one is left over"
            )
        ]
    if mod.enabled and not enabledCopy:
        return [Issue(WARNING, mod.name, "Listed as enabled, but disabled", f"Only Mods/~{name} exists")]
    if not mod.enabled and not disabledCopy:
        return [Issue(WARNING, mod.name, "Listed as disabled, but enabled", f"Mods/{name} is still enabled")]
    return []
