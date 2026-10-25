'''Updating the manager itself from the fork's GitHub releases.

A release is tagged casualfork-<version> and carries W3MM-casualfork-<version>-setup.exe (the Inno Setup installer,
built by packaging/build_release.py) with the installer's SHA-256 in its notes. Updating downloads that installer,
checks the hash, and runs it silently with /RELAUNCH=1 so it starts the new version when it's done.'''

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from src.core.nexus import CLIENT_NAME, NexusError, uniquePath
from src.globals.constants import FORK_VERSION, VERSION

REPOSITORY = "mooonski/W3MM-casualfork"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
TAG_PREFIX = "casualfork-"
ASSET = re.compile(r"^W3MM-casualfork-([0-9.]+)-setup\.exe$")
SHA256 = re.compile(r"SHA-256[^`]*`([0-9a-f]{64})`", re.IGNORECASE)
# the automatic check at startup runs at most this often
CHECK_INTERVAL = 24 * 60 * 60
TIMEOUT = 20


@dataclass
class ManagerRelease:
    version: str
    tag: str
    url: str  # the release page
    notes: str = ""
    installerUrl: str = ""
    installerName: str = ""
    installerSize: int = 0
    sha256: str = ""
    published: str = ""

    @property
    def installable(self) -> bool:
        return bool(self.installerUrl)


@dataclass
class UpdateCheckResult:
    current: str
    latest: ManagerRelease | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.latest is not None and isNewer(self.latest.version, self.current)


def currentVersion() -> str:
    '''The number in FORK_VERSION ("FMTweak Form 1.0" -> "1.0")'''
    match = re.search(r"([0-9]+(?:\.[0-9]+)*)", FORK_VERSION)
    return match.group(1) if match else "0"


def versionKey(version: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in re.findall(r"[0-9]+", version))
    # 0.2 and 0.2.0 are the same version
    while parts and parts[-1] == 0:
        parts = parts[:-1]
    return parts


def isNewer(candidate: str, current: str) -> bool:
    return versionKey(candidate) > versionKey(current)


def parseRelease(payload: Any) -> ManagerRelease:
    '''A ManagerRelease from GitHub's release JSON'''
    if not isinstance(payload, dict) or not payload.get("tag_name"):
        raise NexusError("GitHub returned no release information.")
    tag = str(payload["tag_name"])
    version = tag[len(TAG_PREFIX) :] if tag.startswith(TAG_PREFIX) else tag
    notes = str(payload.get("body") or "")
    release = ManagerRelease(
        version=version,
        tag=tag,
        url=str(payload.get("html_url") or f"{RELEASES_URL}/tag/{tag}"),
        notes=notes,
        published=str(payload.get("published_at") or ""),
    )
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if ASSET.match(name) and asset.get("browser_download_url"):
            release.installerUrl = str(asset["browser_download_url"])
            release.installerName = name
            release.installerSize = int(asset.get("size") or 0)
            break
    match = SHA256.search(notes)
    if match:
        release.sha256 = match.group(1).lower()
    return release


def headers() -> dict[str, str]:
    return {"User-Agent": f"{CLIENT_NAME}/{VERSION}", "Accept": "application/vnd.github+json"}


def fetchLatest(opener: Callable[..., Any] = urllib.request.urlopen) -> ManagerRelease:
    '''The newest non-draft, non-prerelease release on GitHub'''
    request = urllib.request.Request(LATEST_API_URL, headers=headers())
    try:
        with opener(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise NexusError("No release has been published yet.") from error
        if error.code in (403, 429):
            raise NexusError("GitHub is rate-limiting update checks from this address. Try again later.") from error
        raise NexusError(f"GitHub returned HTTP {error.code} while checking for updates.") from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise NexusError(f"Could not check for updates: {error}") from error
    return parseRelease(payload)


def checkForUpdate(opener: Callable[..., Any] = urllib.request.urlopen) -> UpdateCheckResult:
    result = UpdateCheckResult(currentVersion())
    try:
        result.latest = fetchLatest(opener)
    except NexusError as error:
        result.errors.append(str(error))
    return result


def downloadInstaller(
    release: ManagerRelease,
    directory: str,
    progress: Callable[[int, int], Any] = lambda done, total: None,
    cancelled: Callable[[], bool] = lambda: False,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> str:
    '''Downloads the release's installer into directory, checks its size and hash, and returns its path.
    An existing file with the right hash is reused.'''
    if not release.installable:
        raise NexusError("This release has no Windows installer attached.")
    os.makedirs(directory, exist_ok=True)
    existing = os.path.join(directory, release.installerName)
    if os.path.isfile(existing) and release.sha256 and fileHash(existing) == release.sha256:
        return existing
    partial = uniquePath(directory, release.installerName + ".part")
    request = urllib.request.Request(release.installerUrl, headers={"User-Agent": headers()["User-Agent"]})
    done = 0
    digest = hashlib.sha256()
    try:
        with opener(request, timeout=TIMEOUT) as response, open(partial, "wb") as output:
            total = int(response.headers.get("Content-Length") or release.installerSize or 0)
            last = 0.0
            while True:
                if cancelled():
                    raise NexusError("Download cancelled")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - last > 0.15:
                    progress(done, total)
                    last = now
            progress(done, total)
        if total and done != total:
            raise NexusError(f"Download incomplete ({done} of {total} bytes)")
        if release.sha256 and digest.hexdigest() != release.sha256:
            raise NexusError("The downloaded installer doesn't match the hash in the release notes, so it wasn't kept.")
    except urllib.error.HTTPError as error:
        discard(partial)
        raise NexusError(f"GitHub returned HTTP {error.code} for the installer download.") from error
    except (urllib.error.URLError, OSError) as error:
        discard(partial)
        raise NexusError(f"Download failed: {error}") from error
    except BaseException:
        discard(partial)
        raise
    target = uniquePath(directory, release.installerName)
    os.replace(partial, target)
    return target


def fileHash(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discard(partial: str) -> None:
    try:
        if os.path.isfile(partial):
            os.remove(partial)
    except OSError:
        pass


def isInstalledBuild() -> bool:
    '''True for the frozen exe the installer put in place (as opposed to running from source)'''
    return bool(getattr(sys, "frozen", False))


def installerCommand(installer: str) -> list[str]:
    '''Runs the installer with only a progress window, closing this app if it's still open, and starts the new
    version afterwards'''
    return [installer, "/SP-", "/SILENT", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1"]


def launchInstaller(installer: str, run: Callable[[list[str]], Any] = subprocess.Popen) -> None:
    run(installerCommand(installer))


def lastCheck(config) -> float:
    try:
        return float(config.get("SETTINGS", "managerupdatecheck", "0") or 0)
    except ValueError:
        return 0.0


def dueForCheck(config, now: float | None = None) -> bool:
    if config.get("SETTINGS", "checkmanagerupdates", "1") != "1":
        return False
    return (now if now is not None else time.time()) - lastCheck(config) >= CHECK_INTERVAL


def recordCheck(config, now: float | None = None) -> None:
    config.set("SETTINGS", "managerupdatecheck", str(int(now if now is not None else time.time())))
