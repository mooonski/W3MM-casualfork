'''Global constants'''

import sys

from PySide6.QtCore import QCoreApplication

translate = QCoreApplication.translate

VERSION = "0.10.5"
# this fork of W3MM (Nexus updates, RMMR import, Script Merger checks); shown next to the upstream version
FORK_VERSION = "FMTweak Form 1.0.2"


def forkLabel() -> str:
    '''FORK_VERSION plus which build this is: "installed" for the exe the installer puts in place, "source" when run
    with Python, so the two can be told apart in the window'''
    return f"{FORK_VERSION}, {'installed' if getattr(sys, 'frozen', False) else 'source'}"


# Windows groups taskbar buttons by this, so the app gets its own icon instead of Python's
APP_ID = "W3MM.casualfork"
TITLE = translate("GLOBALS", "The Witcher 3 Mod Manager")
AUTHORS = [
    "Stefan Kostic (stefan3372)",
    "Christian Sdunek (Systemcluster)",
    "Adam Sunderman (madman asunder)",
    "Henry Hsieh (henry-hsieh)",
    "George Burduli (burdulixda)",
]
AUTHORS_MAIL = [
    "stekos@live.com",
    "me@systemcluster.me",
    "amsunderman@gmail.com",
    "r901042004@yahoo.com.tw",
    "burduli01@pm.me",
]

URL_WEB = "https://www.nexusmods.com/witcher3/mods/2678"
URL_GIT = "https://github.com/Systemcluster/The-Witcher-3-Mod-manager.git"
