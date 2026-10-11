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
from src.core.scriptmerger import MERGED_FOLDER, ConflictReport
from src.domain.mod import Mod

ERROR = "error"
WARNING = "warning"
INFO = "info"
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass
class Issue:
    severity: str
    mod: str
    problem: str
    detail: str = ''
    url: str = ''


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
        if not mod.files and not mod.dlcs and not mod.menus and not mod.inis:
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
