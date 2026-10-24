'''Builds the Windows release of the fork: the cx_Freeze build, a trimmed copy of it in dist/, and an installer.

cx_Freeze copies the whole PySide6 package, about 340 MB, most of it Qt modules the manager never loads
(WebEngine, QML, Designer, PDF). The copy in dist/ leaves those out, and the installer is made from that copy.
Compiled modules record where their source was on the build machine (a path with the Windows account name,
shown in tracebacks), so the copy's .pyc files are rewritten with generic paths and the build fails if any remain.

Run with the project's Python from the repository root: python packaging/build_release.py
Needs Inno Setup 6 for the installer (ISCC.exe); without it, only the trimmed folder is made.'''

import marshal
import os
import re
import shutil
import subprocess
import sys
import types
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
DIST = os.path.join(ROOT, 'dist')
SCRIPT = os.path.join(ROOT, 'packaging', 'installer.iss')
ISCC_PATHS = (
    os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'), 'Inno Setup 6', 'ISCC.exe'),
    os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'Inno Setup 6', 'ISCC.exe'),
)

# Qt modules the manager imports; the matching Qt6*.dll, Qt*.pyd and Qt*.pyi files are kept, other Qt modules are left out
QT_MODULES = ('Core', 'Gui', 'Widgets', 'Network', 'Svg')
# lib/PySide6/plugins folders the manager needs (window platform, styles, icon and image formats)
QT_PLUGINS = ('platforms', 'styles', 'iconengines', 'imageformats')
# other files left out, relative to the build folder
LEFT_OUT = {
    os.path.join('lib', 'PySide6', 'opengl32sw.dll'),  # software OpenGL, only for Qt Quick
    os.path.join('lib', 'PySide6', 'pyside6qml.abi3.dll'),
    os.path.join('lib', 'PySide6', 'qml'),
    os.path.join('lib', 'PySide6', 'plugins', 'imageformats', 'qpdf.dll'),  # needs Qt6Pdf
    os.path.join('tools', 'make_icon.py'),  # development script
}


def forkVersion() -> str:
    with open(os.path.join(ROOT, 'src', 'globals', 'constants.py'), encoding='utf-8') as file:
        match = re.search(r'^FORK_VERSION = "casual fork ([0-9.]+)"', file.read(), re.MULTILINE)
    if not match:
        raise SystemExit('FORK_VERSION not found in src/globals/constants.py')
    return match.group(1)


def isQtFile(name: str) -> bool:
    return bool(re.match(r'Qt6?[A-Z0-9]', name)) and name.endswith(('.dll', '.pyd', '.pyi'))


def keptQtFile(name: str) -> bool:
    return any(re.fullmatch(rf'Qt6?{module}\.(dll|pyd|pyi)', name) for module in QT_MODULES)


def leftOut(relative: str) -> bool:
    if relative in LEFT_OUT:
        return True
    parts = relative.split(os.sep)
    if parts[:2] == ['lib', 'PySide6']:
        if len(parts) == 3 and isQtFile(parts[2]):
            return not keptQtFile(parts[2])
        # FFmpeg, shipped for Qt Multimedia
        if len(parts) == 3 and parts[2].lower().startswith(('avcodec', 'avformat', 'avutil', 'swscale', 'swresample')):
            return True
        if len(parts) >= 4 and parts[2] == 'plugins':
            return parts[3] not in QT_PLUGINS
    return False


def trimmedCopy(build: str, target: str) -> tuple[int, int]:
    '''Copies the build to target without the files left out. Returns (bytes copied, bytes left out).'''
    copied = skipped = 0
    for folder, folders, files in os.walk(build):
        relativeFolder = os.path.relpath(folder, build)
        for name in list(folders):
            relative = os.path.normpath(os.path.join(relativeFolder, name))
            if leftOut(relative):
                folders.remove(name)
                skipped += sum(
                    os.path.getsize(os.path.join(inner, file))
                    for inner, _, innerFiles in os.walk(os.path.join(folder, name))
                    for file in innerFiles
                )
        for name in files:
            relative = os.path.normpath(os.path.join(relativeFolder, name))
            source = os.path.join(folder, name)
            if leftOut(relative):
                skipped += os.path.getsize(source)
                continue
            os.makedirs(os.path.join(target, relativeFolder), exist_ok=True)
            shutil.copy2(source, os.path.join(target, relative))
            copied += os.path.getsize(source)
    return copied, skipped


def genericFilename(filename: str) -> str:
    '''The recorded source path of a compiled module without the build machine's folders:
    <python>/Lib/..., <site-packages>/..., <project>/... or ~/...'''
    normalized = filename.replace('/', '\\')
    for marker, placeholder in (('\\Lib\\', '<python>\\Lib\\'), ('\\site-packages\\', '<site-packages>\\')):
        index = normalized.rfind(marker)
        if index >= 0:
            return placeholder + normalized[index + len(marker) :]
    root = ROOT.replace('/', '\\')
    if normalized.lower().startswith(root.lower() + '\\'):
        return '<project>\\' + normalized[len(root) + 1 :]
    home = os.path.expanduser('~').replace('/', '\\')
    if normalized.lower().startswith(home.lower() + '\\'):
        return '~\\' + normalized[len(home) + 1 :]
    return normalized


def scrubbedCode(code: types.CodeType) -> types.CodeType:
    consts = tuple(scrubbedCode(const) if isinstance(const, types.CodeType) else const for const in code.co_consts)
    return code.replace(co_filename=genericFilename(code.co_filename), co_consts=consts)


def scrubbedPyc(data: bytes) -> bytes:
    '''A .pyc with the build machine's paths taken out of its code objects (they would show in tracebacks).
    The 16-byte header (magic, flags, source mtime and size) is kept as it is.'''
    header, code = data[:16], marshal.loads(data[16:])
    return header + marshal.dumps(scrubbedCode(code))


def scrubPaths(folder: str) -> int:
    '''Rewrites every .pyc in the folder and in lib/library.zip. Returns the number of files rewritten.'''
    count = 0
    for inner, _, files in os.walk(folder):
        for name in files:
            if name.endswith('.pyc'):
                path = os.path.join(inner, name)
                with open(path, 'rb') as file:
                    data = file.read()
                with open(path, 'wb') as file:
                    file.write(scrubbedPyc(data))
                count += 1
    library = os.path.join(folder, 'lib', 'library.zip')
    if os.path.isfile(library):
        rewritten = library + '.new'
        with zipfile.ZipFile(library) as source, zipfile.ZipFile(rewritten, 'w', zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                data = source.read(info)
                if info.filename.endswith('.pyc'):
                    data = scrubbedPyc(data)
                    count += 1
                target.writestr(info, data)
        os.replace(rewritten, library)
    return count


def remainingPaths(folder: str, needle: str) -> list[str]:
    '''Files in the folder, including inside lib/library.zip, still containing the needle'''
    found = []
    needleBytes = needle.lower().encode()
    for inner, _, files in os.walk(folder):
        for name in files:
            path = os.path.join(inner, name)
            with open(path, 'rb') as file:
                data = file.read()
            if name == 'library.zip':
                with zipfile.ZipFile(path) as library:
                    found += [
                        f'{path}:{member}'
                        for member in library.namelist()
                        if needleBytes in library.read(member).lower()
                    ]
            elif needleBytes in data.lower():
                found.append(path)
    return found


def main() -> None:
    version = forkVersion()
    build = os.path.join(ROOT, 'build', f'W3MM-casualfork-{version}')
    folder = os.path.join(DIST, f'W3MM-casualfork-{version}')
    # cx_Freeze builds over an existing folder, so files from an older build could end up in the release
    for existing in (build, folder):
        if os.path.exists(existing):
            raise SystemExit(f'{existing} already exists. Move or remove it first, or raise FORK_VERSION.')

    subprocess.run([sys.executable, 'setup.py', 'build_exe', f'--build-exe={build}'], cwd=ROOT, check=True)
    copied, skipped = trimmedCopy(build, folder)
    print(f'copied {copied / 2**20:.0f} MB to {folder}, left out {skipped / 2**20:.0f} MB')
    print(f'scrubbed build paths from {scrubPaths(folder)} compiled modules')
    # the user profile folder names the Windows account; it mustn't be anywhere in the release
    leaked = remainingPaths(folder, os.path.expanduser('~'))
    if leaked:
        raise SystemExit('the build machine\'s profile path is still in: ' + ', '.join(leaked))

    iscc = next((path for path in ISCC_PATHS if os.path.isfile(path)), None)
    if not iscc:
        print('Inno Setup 6 not found, so no installer was made')
        return
    subprocess.run(
        [iscc, f'/DAppVersion={version}', f'/DSourceDir={folder}', f'/DOutputDir={DIST}', SCRIPT], cwd=ROOT, check=True
    )


if __name__ == '__main__':
    main()
