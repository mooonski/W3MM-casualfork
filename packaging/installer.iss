; Installer for the fork, built by packaging/build_release.py, which passes AppVersion, SourceDir and OutputDir.
; Installs for the current user by default (no admin prompt), with the option to install for all users.
; Settings, installed.xml and backups live outside the program folder and are kept on uninstall.

#ifndef AppVersion
  #error Run packaging/build_release.py instead of compiling this script directly
#endif

#define AppName "FMTweak Form"
; the name before 1.0; its shortcuts are removed when installing over it
#define OldAppName "W3MM casual fork"
#define AppExe "W3MM-casualfork.exe"
; the same as APP_ID in src/globals/constants.py, so the Start menu shortcut and the window share a taskbar button
#define AppUserModelId "W3MM.casualfork"

[Setup]
AppId=W3MM.casualfork
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=mooonski
AppPublisherURL=https://github.com/mooonski/W3MM-casualfork
AppSupportURL=https://github.com/mooonski/W3MM-casualfork/issues
AppComments=Unofficial fork of The Witcher 3 Mod Manager
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
SetupIconFile=..\res\w3mm-fork.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=W3MM-casualfork-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: files; Name: "{autoprograms}\{#OldAppName}.lnk"
Type: files; Name: "{autodesktop}\{#OldAppName}.lnk"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelId}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelId}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; the manager's own updater runs this installer silently with /RELAUNCH=1 and expects to be started again
Filename: "{app}\{#AppExe}"; Flags: nowait skipifnotsilent; Check: RelaunchRequested

[Code]
function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;
