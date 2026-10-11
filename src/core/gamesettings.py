'''The game's settings files (user.settings, input.settings) read line by line, so saving changes one value and
leaves everything else exactly as it was, and what mods' menus say about each setting'''

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field

from src.util.util import atomicWrite

# a menu option's type, from its displayType
TOGGLE = 'toggle'
SLIDER = 'slider'
OPTIONS = 'options'
OTHER = 'other'

SETTINGS_BACKUPS = '~Game settings'

# the game writes toggles as true/false or as numbers
TOGGLE_VALUES = ('true', 'false', '1', '0', '1.0', '0.0')


@dataclass
class Setting:
    section: str
    key: str
    value: str
    line: int  # where it is in the file


class SettingsFile:
    '''A .settings file: [Section] headers and key=value lines. Sections may repeat keys (input.settings does).'''

    def __init__(self, path: str):
        self.path = path
        self.lines: list[str] = []
        self.settings: list[Setting] = []
        self.encoding = 'utf-8'
        self.newline = '\r\n'
        self.endsWithNewline = True
        if os.path.isfile(path):
            self._read()

    def _read(self) -> None:
        with open(self.path, 'rb') as file:
            raw = file.read()
        if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
            self.encoding = 'utf-16'
        elif raw.startswith(b'\xef\xbb\xbf'):
            self.encoding = 'utf-8-sig'
        try:
            text = raw.decode(self.encoding)
        except UnicodeDecodeError:
            self.encoding = 'latin-1'
            text = raw.decode(self.encoding)
        self.newline = '\r\n' if '\r\n' in text else '\n'
        self.endsWithNewline = text.endswith(('\n', '\r'))
        self.lines = text.splitlines()
        section = ''
        for index, line in enumerate(self.lines):
            stripped = line.strip()
            if stripped.startswith('[') and stripped.endswith(']'):
                section = stripped[1:-1]
            elif '=' in stripped and section and not stripped.startswith((';', '#')):
                key, value = stripped.split('=', 1)
                self.settings.append(Setting(section, key.strip(), value.strip(), index))

    def find(self, section: str, key: str) -> Setting | None:
        for setting in self.settings:
            if setting.section.casefold() == section.casefold() and setting.key.casefold() == key.casefold():
                return setting
        return None

    def change(self, setting: Setting, value: str) -> None:
        self.lines[setting.line] = f"{setting.key}={value}"
        setting.value = value

    def save(self) -> None:
        text = self.newline.join(self.lines) + (self.newline if self.endsWithNewline else '')
        with atomicWrite(self.path, 'w', encoding=self.encoding, newline='', follow_symlinks=True) as file:
            file.write(text)


def backupSettingsFile(path: str, backups: str) -> str:
    '''Copies a settings file into <backups>/~Game settings before it is changed. Returns the copy.'''
    folder = os.path.join(backups, SETTINGS_BACKUPS)
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime('%Y-%m-%d %H%M%S')
    target = os.path.join(folder, f"{stamp} {os.path.basename(path)}")
    number = 2
    while os.path.exists(target):
        target = os.path.join(folder, f"{stamp} ({number}) {os.path.basename(path)}")
        number += 1
    shutil.copy2(path, target)
    return target


# ---------------------------------------------------------------- what mods' menus say


@dataclass
class MenuOption:
    group: str
    var: str
    kind: str = OTHER
    low: float | None = None
    high: float | None = None
    choices: list[str] = field(default_factory=list)
    mod: str = ''

    def allowed(self) -> str:
        '''The values the menu offers, for showing next to the setting'''
        if self.kind == TOGGLE:
            return 'on / off (true, false, 1 or 0)'
        if self.kind == SLIDER and self.low is not None and self.high is not None:
            return f"{number(self.low)} to {number(self.high)}"
        if self.kind == OPTIONS and self.choices:
            return ', '.join(self.choices)
        return ''

    def problem(self, value: str) -> str:
        '''Why value isn't one the menu offers, or ""'''
        if self.kind == TOGGLE and value.casefold() not in TOGGLE_VALUES:
            return 'Use true or false (or 1 or 0)'
        if self.kind == SLIDER and self.low is not None and self.high is not None:
            try:
                amount = float(value)
            except ValueError:
                return 'Use a number'
            if not self.low <= amount <= self.high:
                return f"Use a number from {number(self.low)} to {number(self.high)}"
        if self.kind == OPTIONS and self.choices and value not in self.choices:
            return 'Use one of ' + ', '.join(self.choices)
        return ''


def number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


TAG = re.compile(r'<(/?)(Group|Var|Option)\b([^>]*?)(/?)>', re.IGNORECASE)
ATTRIBUTE = re.compile(r'([A-Za-z_]+)\s*=\s*"([^"]*)"')


def readMenuOptions(text: str, mod: str = '') -> list[MenuOption]:
    '''The options of a menu .xml (read leniently: mods' menus aren't always well-formed XML)'''
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    options: list[MenuOption] = []
    groups: list[str] = []
    current: MenuOption | None = None
    for match in TAG.finditer(text):
        closing, tag, attributes, selfClosing = match.groups()
        tag = tag.casefold()
        values = dict(ATTRIBUTE.findall(attributes))
        if tag == 'group':
            if closing:
                if groups:
                    groups.pop()
            elif not selfClosing:
                groups.append(values.get('id', ''))
        elif tag == 'var' and not closing and groups:
            # overrideGroup saves an option under another page's section
            current = MenuOption(values.get('overrideGroup') or groups[-1], values.get('id', ''), mod=mod)
            kind = values.get('displayType', '').split(';')
            if kind[0].upper() == 'TOGGLE':
                current.kind = TOGGLE
            elif kind[0].upper() == 'SLIDER':
                current.kind = SLIDER
                try:
                    current.low, current.high = float(kind[1]), float(kind[2])
                except (IndexError, ValueError):
                    pass
            elif kind[0].upper() == 'OPTIONS':
                current.kind = OPTIONS
            if current.var:
                options.append(current)
            if selfClosing:
                current = None
        elif tag == 'var' and closing:
            current = None
        elif tag == 'option' and not closing and current is not None and 'id' in values:
            current.choices.append(values['id'])
    return options


def readMenuFile(path: str, mod: str = '') -> list[MenuOption]:
    try:
        with open(path, 'rb') as file:
            raw = file.read()
    except OSError:
        return []
    for encoding in ('utf-16', 'utf-8-sig') if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else ('utf-8-sig', 'latin-1'):
        try:
            return readMenuOptions(raw.decode(encoding), mod)
        except UnicodeDecodeError:
            continue
    return []


def modMenuOptions(menuFolder: str, mods) -> dict[tuple[str, str], MenuOption]:
    '''(section, setting) in lower case -> what the mod's menu says about it, for every installed mod's menus'''
    found: dict[tuple[str, str], MenuOption] = {}
    for mod in mods:
        for menu in mod.menus:
            for candidate in (menu, menu + '.disabled'):
                path = os.path.join(menuFolder, candidate)
                if os.path.isfile(path):
                    for option in readMenuFile(path, mod.name):
                        found.setdefault((option.group.casefold(), option.var.casefold()), option)
                    break
    return found


def settingOwners(mods, options: dict[tuple[str, str], MenuOption]) -> tuple[dict, dict]:
    '''Which mod each setting and each section of user.settings comes from'''
    keys: dict[tuple[str, str], str] = {key: option.mod for key, option in options.items()}
    sections: dict[str, str] = {}
    for (section, _), option in options.items():
        sections.setdefault(section, option.mod)
    for mod in mods:
        for setting in mod.usersettings:
            keys.setdefault((setting.context.casefold(), setting.option.casefold()), mod.name)
            sections.setdefault(setting.context.casefold(), mod.name)
    return keys, sections


# ---------------------------------------------------------------- key bindings


@dataclass
class Binding:
    context: str
    key: str
    action: str
    details: str
    mod: str = ''
    clash: bool = False  # another mod uses the same key here the same way
    others: list[str] = field(default_factory=list)  # other actions on the same key here


ACTION = re.compile(r'Action\s*=\s*([^,)]+)', re.IGNORECASE)
STATE = re.compile(r'State\s*=\s*([^,)]+)', re.IGNORECASE)
XML_ACTION = re.compile(r'\bid\s*=\s*"([^"]+)"', re.IGNORECASE)


def modActions(mods) -> dict:
    '''What the mods add to the key bindings: ("binding", context, key, action) for each binding a mod put in
    input.settings, and ("action", name) for each action a mod defines in input.xml (all in lower case) -> mod'''
    found: dict = {}
    for mod in mods:
        for key in mod.inputsettings:
            action = getattr(key, 'action', None)
            name = action['Action'] if action is not None else None
            if name:
                context = key.context.strip('[]').casefold()
                found.setdefault(('binding', context, key.key.casefold(), name.casefold()), mod.name)
        for xml in mod.xmlkeys:
            match = XML_ACTION.search(xml)
            if match:
                found.setdefault(('action', match.group(1).casefold()), mod.name)
    return found


def bindingOwner(actions: dict, context: str, key: str, action: str) -> str:
    return actions.get(('binding', context.casefold(), key.casefold(), action.casefold())) or actions.get(
        ('action', action.casefold()), ''
    )


def readBindings(settings: SettingsFile, actions: dict[str, str]) -> list[Binding]:
    '''The key bindings of input.settings, each with the other actions on the same key in the same context.
    Two different mods using a key the same way (both on a press, or both when held...) is marked as a clash.
    A mod sharing a key with the game is left unmarked: mods often do that on purpose (Friendly HUD's "hold J"
    next to the game's "J" for the journal, told apart by its scripts).'''
    bindings = []
    for setting in settings.settings:
        if not setting.key.upper().startswith('IK_'):
            continue
        match = ACTION.search(setting.value)
        action = match.group(1).strip() if match else ''
        owner = bindingOwner(actions, setting.section, setting.key, action)
        bindings.append(Binding(setting.section, setting.key, action, setting.value, owner))
    byKey: dict[tuple[str, str], list[Binding]] = {}
    for binding in bindings:
        if binding.key.casefold() != 'ik_none':
            byKey.setdefault((binding.context.casefold(), binding.key.casefold()), []).append(binding)
    for group in byKey.values():
        for binding in group:
            binding.others = sorted(
                {other.action for other in group if other.action.casefold() != binding.action.casefold()}
            )
            how = state(binding)
            rivals = {
                other.mod
                for other in group
                if other.mod
                and other.mod != binding.mod
                and state(other) == how
                and other.action.casefold() != binding.action.casefold()
            }
            binding.clash = bool(binding.mod and rivals)
    return bindings


def state(binding: Binding) -> str:
    '''How a binding is used: "" for a press, or its State (Duration for holding, Axis...)'''
    match = STATE.search(binding.details)
    return match.group(1).strip().casefold() if match else ''


def gameRunning() -> bool:
    '''Whether The Witcher 3 is running (it writes these files when it closes)'''
    if sys.platform != 'win32':
        return False
    try:
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq witcher3.exe', '/NH'],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return 'witcher3.exe' in result.stdout.casefold()
