; Inno Setup script for MHM Pipeline — wraps the PyInstaller one-folder
; output in dist\MHMPipeline\ into a single self-contained installer .exe.
;
; Run on a Windows host after PyInstaller succeeds:
;   "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" installer\windows\build_installer.iss
;
; Output: dist\MHMPipeline-Setup-0.1.0.exe (~4-5 GB compressed)

[Setup]
; SourceDir resolves all relative paths in this script (LicenseFile, SetupIconFile,
; OutputDir, [Files] Source:) from the repo root, regardless of which directory
; ISCC was invoked from.
SourceDir=..\..
AppName=MHM Pipeline
AppVersion=0.1.0
AppPublisher=Bar-Ilan University
AppPublisherURL=https://github.com/alexgoldberg/mhm-pipeline
DefaultDirName={autopf}\MHMPipeline
DefaultGroupName=MHM Pipeline
OutputDir=dist
OutputBaseFilename=MHMPipeline-Setup-0.1.0
; lzma2/ultra64 is required: the bundled payload (~9 GB uncompressed) only
; fits under Inno Setup's 4.2 GB single-file Setup.exe ceiling at this
; compression level. lzma2/normal produced a ~5 GB payload that triggered
; "Disk spanning must be enabled" — ultra64 compresses tightly enough to
; stay under the limit at the cost of ~30-45 min compress on slow CPUs.
; To speed compress: dedupe the HF blob/snapshot duplication in
; scripts/package_for_windows_build.sh (cuts payload by ~3 GB) before
; switching back to a faster compression level.
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
LicenseFile=installer\windows\LICENSE.rtf
SetupIconFile=installer\windows\mhm_pipeline.ico
WizardStyle=modern
DiskSpanning=no
PrivilegesRequired=admin
UninstallDisplayIcon={app}\MHMPipeline.exe
UninstallDisplayName=MHM Pipeline

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\MHMPipeline\*"; DestDir: "{app}"; \
  Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\MHM Pipeline"; Filename: "{app}\MHMPipeline.exe"; \
  IconFilename: "{app}\MHMPipeline.exe"
Name: "{commondesktop}\MHM Pipeline"; Filename: "{app}\MHMPipeline.exe"; \
  IconFilename: "{app}\MHMPipeline.exe"; Tasks: desktopicon
Name: "{group}\Uninstall MHM Pipeline"; Filename: "{uninstallexe}"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
  GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\MHMPipeline.exe"; \
  Description: "Launch MHM Pipeline"; \
  Flags: nowait postinstall skipifsilent
