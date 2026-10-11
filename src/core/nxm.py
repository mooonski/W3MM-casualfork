'''Handling nxm:// links from the Nexus Mods website ("Mod Manager Download")'''

import hashlib
import os
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Protocol

REGISTRY_KEY = r"Software\Classes\nxm"
COMMAND_KEY = REGISTRY_KEY + r"\shell\open\command"


@dataclass
class NxmLink:
    game: str
    modId: int
    fileId: int
    key: str = ''
    expires: str = ''
    url: str = ''


def parseNxm(url: str) -> NxmLink | None:
    '''Parses nxm://<game>/mods/<mod id>/files/<file id>?key=...&expires=...'''
    match = re.match(r"^nxm://([^/]+)/mods/(\d+)/files/(\d+)/?(?:\?(.*))?$", url.strip(), re.IGNORECASE)
    if not match:
        return None
    query = urllib.parse.parse_qs(match.group(4) or '')
    return NxmLink(
        game=match.group(1).lower(),
        modId=int(match.group(2)),
        fileId=int(match.group(3)),
        key=(query.get('key') or [''])[0],
        expires=(query.get('expires') or [''])[0],
        url=url.strip(),
    )


def handlerCommand(arguments: list[str]) -> str:
    '''The command line Windows should run for nxm links: this program with its start overrides and the link'''
    if getattr(sys, 'frozen', False):
        program = [sys.executable]
    else:
        python = sys.executable
        windowed = os.path.join(os.path.dirname(python), 'pythonw.exe')
        main = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'main.py')
        program = [windowed if os.path.isfile(windowed) else python, os.path.normpath(main)]
    return subprocess.list2cmdline(program + arguments) + ' --nxm "%1"'


def splitCommand(command: str, url: str) -> list[str]:
    '''Turns a registered handler command into arguments for opening url'''
    match = re.match(r'^\s*(?:"([^"]+)"|(\S+))\s*(.*)$', command)
    if not match:
        return []
    program = match.group(1) or match.group(2)
    rest = match.group(3)
    arguments = re.findall(r'"([^"]*)"|(\S+)', rest)
    parts = [quoted or plain for quoted, plain in arguments]
    replaced = [part.replace('%1', url) for part in parts]
    if '%1' not in rest:
        replaced.append(url)
    return [program] + replaced


# mod managers that also handle nxm links, by their program file
KNOWN_HANDLERS = {
    'vortex.exe': 'Vortex',
    'modorganizer.exe': 'Mod Organizer 2',
    'nxmhandler.exe': 'Mod Organizer 2',
    'zcom-mod-manager.exe': 'ZCOM Mod Manager',
    'relapsesmodmanagerredux.exe': "Relapse's Mod Manager Redux",
    'nexusmodsapp.exe': 'the Nexus Mods app',
    'nexusmods.app.exe': 'the Nexus Mods app',
}


def handlerName(command: str) -> str:
    """A readable name for the program a nxm handler command runs"""
    if not command:
        return 'no program'
    if command.rstrip().endswith('--nxm "%1"'):
        return 'another copy of W3MM'
    arguments = splitCommand(command, '')
    program = os.path.basename(arguments[0]) if arguments else command
    return KNOWN_HANDLERS.get(program.casefold(), program)


def forward(command: str, url: str) -> bool:
    '''Opens url with another program's handler command'''
    arguments = splitCommand(command, url)
    if not arguments:
        return False
    try:
        subprocess.Popen(arguments, close_fds=True)
        return True
    except OSError as error:
        print('could not forward nxm link:', error, file=sys.stderr)
        return False


def instanceName(configuration: str) -> str:
    '''Name of the local socket a running instance listens on, one per configuration folder'''
    digest = hashlib.sha1(os.path.normcase(os.path.abspath(configuration)).encode('utf-8')).hexdigest()[:12]
    return f"TW3MM-{digest}"


class Registry(Protocol):
    def get(self) -> str: ...

    def set(self, command: str) -> None: ...


class WindowsRegistry:
    '''The current user's nxm handler (HKEY_CURRENT_USER only, no admin rights needed)'''

    def get(self) -> str:
        import winreg

        for root, key in (
            (winreg.HKEY_CURRENT_USER, COMMAND_KEY),
            (winreg.HKEY_CLASSES_ROOT, r"nxm\shell\open\command"),
        ):
            try:
                with winreg.OpenKey(root, key) as handle:
                    return str(winreg.QueryValueEx(handle, '')[0])
            except OSError:
                continue
        return ''

    def set(self, command: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as handle:
            winreg.SetValueEx(handle, '', 0, winreg.REG_SZ, 'URL:NXM Protocol')
            winreg.SetValueEx(handle, 'URL Protocol', 0, winreg.REG_SZ, '')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, COMMAND_KEY) as handle:
            winreg.SetValueEx(handle, '', 0, winreg.REG_SZ, command)


class NxmRegistration:
    '''Makes this program the nxm handler and puts the previous one back'''

    def __init__(self, registry: Registry, config):
        self.registry = registry
        self.config = config

    def command(self) -> str:
        return handlerCommand(self.config.startarguments)

    def isRegistered(self) -> bool:
        return self.registry.get() == self.command()

    def previous(self) -> str:
        return self.config.previousnxm

    def register(self) -> None:
        current = self.registry.get()
        ours = self.command()
        if current and current != ours and not current.endswith('--nxm "%1"'):
            self.config.previousnxm = current
        self.registry.set(ours)

    def unregister(self) -> bool:
        '''Puts the previous handler back. Returns False if there was none to put back.'''
        previous = self.previous()
        if not previous:
            return False
        self.registry.set(previous)
        return True
