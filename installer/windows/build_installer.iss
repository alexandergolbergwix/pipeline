[Setup]
AppName=MHM Pipeline
AppVersion=0.1.0
AppPublisher=Bar-Ilan University
AppPublisherURL=https://github.com/alexgoldberg/mhm-pipeline
DefaultDirName={autopf}\MHMPipeline
DefaultGroupName=MHM Pipeline
OutputDir=dist
OutputBaseFilename=MHMPipeline-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
LicenseFile=LICENSE
SetupIconFile=installer\windows\mhm_pipeline.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs; \
  Excludes: ".git,__pycache__,*.pyc,.venv,dist,build"

[Icons]
Name: "{group}\MHM Pipeline"; Filename: "{app}\MHMPipeline.bat"
Name: "{commondesktop}\MHM Pipeline"; Filename: "{app}\MHMPipeline.bat"; \
  Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
  GroupDescription: "Additional icons:"

[Run]
Filename: "powershell.exe"; \
  Parameters: "-ExecutionPolicy Bypass -File ""{app}\installer\windows\install.ps1"""; \
  StatusMsg: "Installing Python environment..."; Flags: runhidden waituntilterminated

Filename: "{app}\MHMPipeline.bat"; \
  Description: "Launch MHM Pipeline"; Flags: nowait postinstall skipifsilent
