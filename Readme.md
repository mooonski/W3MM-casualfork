# The Witcher 3 Mod Manager

**Mod Manager for The Witcher 3.**

Supports the Steam and GOG releases on Windows, Steam Proton installations on Linux and CrossOver installations on macOS.

Works with Original, Next-Gen, and Remastered installations.

## About This Fork

This is "casual fork", an unofficial fork of [Systemcluster's The Witcher 3 Mod Manager](https://github.com/Systemcluster/The-Witcher-3-Mod-manager), built on version 0.10.5 and developed on the `nexus-updates` branch. It adds Nexus Mods updates and downloads, plus tools that were previously spread over several other programs. The rest of this readme describes the upstream manager, which all of this builds on.

It isn't made or supported by the upstream authors. Please report problems with the fork in this repository's issues, not upstream. It is developed and tested on Windows with Witcher 3 Remastered; the new features haven't been tried on Linux or macOS, and some of them (API key storage, Recycle Bin, the `nxm://` handler) are Windows-only.

#### Getting Started

Download `W3MM-casualfork-<version>-setup.exe` from the [releases](https://github.com/mooonski/W3MM-casualfork/releases) and run it. Python isn't needed. It installs for your Windows account only, without an admin prompt, unless you choose to install for all users. Uninstall it from Windows Settings > Apps; your settings, mod list and backups are kept.

To run it from source instead, install Python 3.10 to 3.12 and [PDM](https://pdm-project.org/en/latest/#recommended-installation-method), clone this repository, install the dependencies with `pdm install --prod` and start it with `pdm run start`. The fork also runs on Python 3.13, but PDM won't install for it until `pyproject.toml` allows it.

The fork uses the same configuration location as the upstream manager, so it picks up an existing setup, including `installed.xml`. Back up the configuration directory before trying it, and don't run the fork and the upstream manager at the same time.

The Nexus features need a Nexus Mods API key, set with **Nexus > Set API Key...**. Get your personal key from the bottom of the [API keys page](https://next.nexusmods.com/settings/api-keys) on Nexus. The key is stored encrypted with Windows DPAPI and is only sent to Nexus. Direct downloads need a Premium account; for other accounts, the download page opens in the browser.

### Setup

- A setup page on the first run, and under **Settings > Setup...** later: the game folder (found through Steam or GOG where possible), Script Merger (found next to the game or the download folder), the download folder and the Nexus API key, with a button that checks the key.

### Nexus Mods

- **Check for Updates** marks every mod with a Nexus ID that has a newer file, and **Update** installs it in place of the old version. The update keeps the mod's priority, enabled state and separator, backs up the old version first, and records the exact Nexus file installed. **Restore Previous Version...** brings a backup back.
- Updates follow the file update chain on Nexus, and the updates dialog shows each mod's changelog.
- **Set Nexus Mod...** links a mod to its Nexus page by ID or URL. **Choose Nexus File...** records which file of the page is installed, or switches to another file.
- Nexus details: thumbnails, categories, summaries and last-updated dates for linked mods.
- **Browse Nexus** tab to search Witcher 3 mods, pick a file and **Download & Install** it.
- **Check Requirements...** lists mods, expansions and tools that your mods' Nexus pages say they need, and the check also runs after installing.
- **Check Remastered Versions...** finds mods not updated since Remastered, and other versions on Nexus that were.
- Endorse mods from a thumbs-up column in the mod list.
- **Download History...** lists everything downloaded from Nexus, what became of it, and lets you install it again.
- Optional `nxm://` link handler ("Mod Manager Download" on Nexus). Links for other games are passed on to the previously registered program, and **Make W3MM the Default for Nexus Links** takes them back if another manager claims them.

### Installing and Archives

- Downloaded archives go to a configurable download folder, and copies of installed archives can be kept there.
- Warns when a mod is installed twice under different names, or reinstalled over itself.
- **Reinstall** a mod from its archive.
- **Not Installed** tab for downloaded archives that nothing was installed from. Empty archives from unfinished browser downloads are explained.
- **Clean Up Archives...** finds old versions and other archives you may not need, and sends them to the Recycle Bin or an "Old archives" folder.
- Installs files for the game's `bin` folder, such as ReShade add-ons, and tracks them so uninstalling removes them. Files the manager didn't install are never overwritten.
- **Import from RMMR...** brings installed mods and their Nexus IDs over from Relapse's Mod Manager Redux, including mods that only change ini settings.

### Mod List and Load Order

- Tick several mods and toggle them with Space, plus **Enable All** and **Disable All**.
- Separators: named headings to group mods. Add them from the blank space below the list, drag mods onto them, drag separators to reorder them, and collapse or expand them all at once.
- Drag mods into your own display order in the mod list. This is cosmetic only, and clicking a column header returns to column sorting.
- Drag mods in the load order list to change their priorities.
- Priorities always follow `mods.settings`, including edits made outside the manager. **Refresh** (F5) reads it and the mods again.
- Mods the game installed from mod.io are shown in blue with a `[MOD.IO]` tag (display only).
- Ctrl+C copies the names of the selected mods from the mod list or load order.

### Script Merger

- Toolbar button to run Script Merger (Ctrl+S). The manager watches it and reports any remaining script conflicts when it closes.
- Script conflict warnings, with a prompt to run Script Merger after installing or updating mods.
- Finds Script Merger automatically and reads its merge inventory.
- **Update Script Merger...** installs a newer Script Merger from Nexus. Merges and Script Merger's own settings are kept, and replaced files are backed up first.

### Health and Safety

- **Health Check** looks for problems with installed mods: missing mod, DLC, menu and `bin` files, mods whose enabled state doesn't match the game folder, mod folders not managed by the manager, leftover priorities, mods installed twice, unmerged script conflicts, mods built on Sharedutils that load after it on Remastered (with a one-click fix), and ReShade problems.
- **File Conflicts...** shows which mod wins when several mods pack the same file in their bundles.
- **Back Up Saves Before Updating** copies your newest game saves to the backup folder before updating mods. **Game Save Backups...** lists and restores them.

### Game and Tools

- **Game Settings...** window for `user.settings` and key bindings, and quick access to open the game's config files in Notepad.
- **ReShade...** shows the installed ReShade version and build (standard or Add-on), enables or disables it, and updates it from reshade.me, keeping the same build.

### Look

- Own window title and claw-marks icon, its own taskbar entry, and a log file (also when run with `pythonw`).
- Line icons on the toolbar, and fixes to the light theme and status colours.

## Description

The Witcher 3 Mod Manager is an application that simplifies installing and managing The Witcher 3 mods, originally developed by [stefan3372](https://github.com/stefan3372) and now being continued here.

See the [Nexus Mods page](https://www.nexusmods.com/witcher3/mods/2678) for releases, screenshots and more information.

## Usage

### Release Versions (Windows)

These are the upstream releases, without the fork's features. Download the latest release from Nexus Mods or from the [GitHub releases](https://github.com/Systemcluster/The-Witcher-3-Mod-manager/releases).

Existing configuration is picked up automatically when updating. Portable installations keep `config.ini` next to the executable; to update one, copy `config.ini` and `installed.xml` into the new directory.

If no configuration exists on the first run, it is created in `AppData\Local\The Witcher 3 Mod Manager`. Existing configuration is searched for in the directory of the executable first and in `Documents\The Witcher 3 Mod Manager` second for compatibility with older versions, and can be moved freely between these locations.

### File Safety

Shared game menus such as `graphics.xml` remain installed and registered when a mod is disabled or uninstalled. Their original contents are not backed up or restored; restore those separately when removing a shared-menu replacement.

INI files packaged under `bin/config/base` are copied to the same location in the game, including from INI-only packages. Folder names and the `.ini` extension are matched case-insensitively; INIs elsewhere in a package still require manual installation. Files are replaced whole, not merged. These shared base configuration files remain in place when a mod is disabled or uninstalled, and their previous contents are not backed up or restored. Back up affected files before installing a replacement.

Mod and DLC folders are only removed or replaced inside the `Mods` and `DLC` directories of the configured game, and other installed files only inside the game directory. Symbolic links and Windows junctions in the game directory are not supported and are never followed for deletion.

A replaced mod folder is kept until its new copy is in place. If it can't be restored after a failure, the error names the `.tw3mm-*` directory holding the original files. Mutable configuration and settings files are also written to sibling temporary files and atomically replaced. Linked configuration and Documents settings files are updated at their targets without replacing the links. A failed installation doesn't remove files it already copied; a complete installation or uninstall is not transactional.

### Script Merger on Linux and macOS

Script Merger is run with `wine` using the executable selected in the manager. To run it in a specific Wine prefix, set `mergerlaunchcommand` in the `[PATHS]` section of `config.ini` while the manager is closed:

```ini
[PATHS]
scriptmerger=/home/user/Tools/Script Merger/WitcherScriptMerger.exe
mergerlaunchcommand=WINEPREFIX="/home/user/Games/Witcher 3/prefix" wine "/home/user/Tools/Script Merger/WitcherScriptMerger.exe"
```

With Steam and `protontricks-launch`, the Proton prefix of the game can be used instead:

```ini
mergerlaunchcommand=protontricks-launch --appid 292030 "/home/user/Tools/Script Merger/WitcherScriptMerger.exe"
```

Replace the paths with your own, and the app ID if the game was added as a non-Steam shortcut. For CrossOver, the command has to target the bottle the game is installed in.

The Script Merger executable still has to be selected in the manager, and its directory is used as the working directory of the command. Unlike `gamelaunchcommand`, the command is run through a shell, so paths containing spaces have to be quoted and only trusted commands should be used. On Windows, Script Merger is always run directly.

### Startup Diagnostics

When startup fails, a `TW3MM-startup-*.log` file is written to the system temporary directory, even if the GUI can't open. The path of the log is also printed to the console when there is one.

- On Windows and inside Wine, the log is in `%TEMP%` of that environment.
- On Linux and macOS, the log is in the temporary directory of the Python runtime, usually `TMPDIR` or `/tmp`.

### Game Configuration

Select `witcher3.exe` in `bin/x64` for DX11 or in `bin/x64_dx12` for DX12. The `--game` command-line option also accepts the installation directory itself.

If a game update removes the configured executable, the manager uses whichever renderer version is available in the same game directory, without changing `config.ini`. Detection always follows the current files and launcher metadata, so it keeps working after a rollback. Other installations are not searched.

Remastered ships launcher metadata identifying `remasteredEdition` and the store, which is used to detect the edition. Older installations without it are detected from the executable layout. The selected executable determines which user settings file is used. Existing menu file lists are updated, missing ones are not created.

Steam installations are always launched through Steam, also on Windows, since starting the Remastered executable directly is reported to crash. Renderer and mod options for these launches are controlled by Steam and REDlauncher. Other Windows installations launch the selected executable directly.

For other launch environments, `gamelaunchcommand` in the `[PATHS]` section of `config.ini` replaces the automatic launch. The command is split into arguments and not run through a shell, so on Linux and macOS, paths and arguments containing spaces have to be quoted. With CrossOver, if the system `steam://` handler doesn't open Steam in the right bottle, the command has to do it instead. `--userdocuments` selects the Documents directory containing `The Witcher 3` in that environment, which is usually not the one of the host.

### Remastered Troubleshooting

- **Selecting the executable**\
  If `gameexe` isn't set, the selection dialog opens on startup. Select the existing DX12 executable.
- **Game crashes when launched from the manager**\
  Launch through Steam. `steam_api64.dll` is part of the game and shouldn't be deleted or renamed.
- **Mods are installed but don't load**\
  Check that mods are enabled in REDlauncher and that neither the Steam launch options nor a custom command contain `-disablemods`. With Proton or CrossOver, check that `--userdocuments` points to the settings directory the game actually uses. Older mods might not work with Remastered even if they install fine.
- **Merging fails**\
  Script Merger is a separate application, use [Fresh and Automated Edition](https://www.nexusmods.com/witcher3/mods/8405) or [Script Merger - Remastered](https://www.nexusmods.com/witcher3/mods/13076).
- **Script Merger recreates an old game directory**\
  Script Merger keeps its own `GameDirectory`, `ModsDirectory` and `VanillaScriptsDirectory` settings, which don't change with the game path in the manager. Change the game directory in Script Merger as well, and back up its configuration before resetting overrides or rebuilding merges. Use a Remastered-compatible Script Merger.
- **Finding or resetting manager settings**\
  Close the manager and back up its configuration directory first, usually in `User\AppData\Local\The Witcher 3 Mod Manager` on Windows or in one of the other locations described above. To reset paths, rename only `config.ini` and keep `installed.xml` to keep the list of installed mods. The load order of the game is stored separately in `The Witcher 3/mods.settings` in the Documents directory.

### Python (Windows, Linux, and macOS)

Dependencies are managed with [PDM](https://pdm-project.org/en/latest/). Python 3.10 to 3.12 is supported.

1. Install PDM using the [recommended installation method](https://pdm-project.org/en/latest/#recommended-installation-method)
2. Clone the repository
3. Install dependencies with `pdm install --prod`
4. Start the manager with `pdm run start`

On Linux and macOS:
- Configuration is stored in `~/.config/TheWitcher3ModManager`
- `wine` is required to run Script Merger
- Commands should be run with `pdm run` to use the project environment

### Development Checks

Development tools are installed with `pdm install`.

- `pdm run format` formats all Python files.
- `pdm run lint` checks imports and common errors without changing files.
- `pdm run ruff check --fix .` applies safe lint fixes, including import sorting.
- `pdm run check` runs the formatting and lint checks, Pyright, mypy and all tests.
- `pdm run typecheck-pyright` runs Pyright, the type checker behind Pylance, with the `standard` settings of the project.
- `pdm run typecheck` runs mypy as a second type checker.

### Build Release (Windows)

1. Install dependencies and development tools in a fresh environment with `pdm install`
2. Run the checks with `pdm run check`
3. Build the executable with `pdm run build-win`
4. The build is placed in `build/exe.[platform].[python version]`

For the fork's release, run `python packaging/build_release.py` instead of step 3. It builds into `build/W3MM-casualfork-<version>`, copies that to `dist/` without the Qt modules the manager doesn't use (about 290 MB of WebEngine, QML and Designer), and makes `dist/W3MM-casualfork-<version>-setup.exe` with [Inno Setup 6](https://jrsoftware.org/isinfo.php). The version comes from `FORK_VERSION` in `src/globals/constants.py`, and the script stops if that version's folders already exist.
