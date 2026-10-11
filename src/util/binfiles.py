"""Files a mod puts in the game's bin folder outside bin/config, such as ReShade add-ons and shaders"""

import re

# not game files: documentation that comes with them
DOCUMENTATION = re.compile(r"(?i)(readme[^/]*|.+\.(txt|md|pdf|url|html?))$")


def safeBinPath(relative: str) -> str:
    """relative ("x64_dx12/name.addon64") tidied, or "" if it would leave the bin folder or reach bin/config"""
    parts = [part for part in relative.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts or any(part == ".." or ":" in part for part in parts) or parts[0].casefold() == "config":
        return ""
    return "/".join(parts)


def binFileTarget(parts: list[str]) -> str:
    """For a file at parts (its path inside the mod, split into folders), where it goes under the game's bin
    folder, or "" when it isn't a bin file: menus and base ini files (bin/config) are handled on their own, and
    anything inside a mod or DLC folder (under a content folder) belongs to that folder"""
    lowered = [part.casefold() for part in parts]
    if "bin" not in lowered[:-1]:
        return ""
    index = lowered.index("bin")
    if "content" in lowered[:index] or DOCUMENTATION.match(parts[-1]):
        return ""
    return safeBinPath("/".join(parts[index + 1 :]))
