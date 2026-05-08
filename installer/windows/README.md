# MHM Pipeline — Windows Installer

## Overview

This directory contains the Windows installer build pipeline. The result is a single offline `MHMPipeline-Setup-0.1.0.exe` you can send to a non-technical user — they double-click and a wizard walks them through installation. No internet connection is required at install time or runtime.

The build itself must run on a Windows host (PyInstaller cannot cross-compile from macOS). You produce a source bundle on macOS, upload it to a Windows host, and run one `.bat` file there.

## Build host options

| Option | Cost | Notes | Link |
|---|---|---|---|
| UTM + Windows 11 ARM | Free | Recommended for Apple Silicon. Runs Windows 11 ARM in a VM directly on your Mac. | https://mac.getutm.app/ |
| AppOnFly Windows VPS | ~$25 / month | Browser-based remote Windows desktop. No local install. | https://www.apponfly.com/ |
| Paperspace Core | ~$0.50 / hour | Hourly Windows VM. Optional GPU (not needed here). | https://www.paperspace.com/ |
| Colleague's Windows 10 / 11 PC | Free | Any reasonably modern Windows PC works. | — |

Minimum host requirements (any option):

- 4 GB RAM (8 GB recommended for faster PyInstaller)
- 30 GB free disk space (build artifacts are ~9.5 GB extracted, ~4–5 GB compressed)
- No GPU required — the bundled app runs on CPU

## Prerequisites on the Windows host (one-time)

Install these two tools on the Windows host before the first build. Both are GUI installers ("next, next, finish") and together take about 5 minutes.

1. **Python 3.12** — https://www.python.org/downloads/
   During installation, tick **"Add python.exe to PATH"**.
2. **Inno Setup 6** — https://jrsoftware.org/isinfo.php
   Default installation path (`C:\Program Files (x86)\Inno Setup 6`) is what the build script expects.

You can verify both are installed by opening a Command Prompt and running `py -3.12 --version` and checking that `C:\Program Files (x86)\Inno Setup 6\ISCC.exe` exists.

## Build steps

### Step 1 — On macOS: produce the source bundle

```
bash scripts/package_for_windows_build.sh
```

Produces `dist/mhm-pipeline-source.zip` (~6 GB). The script bundles the source tree, the Mazal/KIMA databases, the four NER model files, the Hugging Face snapshots for `hebrew-manuscript-joint-ner-v2` and `dictabert`, and the Windows build scripts. Training-only artefacts (TSVs, raw NLI XML, k-fold checkpoints) are excluded.

If the script aborts complaining that an HF snapshot is missing, run the app once on the Mac to populate the cache, then retry.

### Step 2 — Transfer the zip to the Windows host

Use whichever channel is convenient: OneDrive, Google Drive, an SCP tunnel, a shared folder mounted in UTM, or a USB stick.

### Step 3 — On Windows: unzip and build

1. Unzip `mhm-pipeline-source.zip` into a working folder. The contents land under `_winstage\`.
2. Open `_winstage\` in File Explorer.
3. Double-click `installer\windows\Build Installer.bat`.

A console window opens and shows progress through four numbered steps:

1. Create a Python venv (`py -3.12 -m venv .venv-build`)
2. Install build dependencies (`pip install pyinstaller`, `pip install -e .`)
3. Run PyInstaller against `installer\windows\MHMPipeline.spec` (~10 minutes)
4. Run Inno Setup against `installer\windows\build_installer.iss` (~15 minutes for LZMA2 ultra compression)

Total wall-clock time is about 30 minutes on a 4-CPU 16-GB host. The console window stays open at the end so you can read the result; press any key to close it.

### Step 4 — Locate the installer

Find it at:

```
dist\MHMPipeline-Setup-0.1.0.exe
```

Expected size: ~4–5 GB. This single file is what you send to the supervisor.

## Sending the installer to a supervisor

1. Share `MHMPipeline-Setup-0.1.0.exe` via OneDrive, Google Drive, WeTransfer, or a USB stick. The file is too large for email.
2. Supervisor saves the `.exe` somewhere on their Windows 10 / 11 machine and double-clicks it.
3. The Inno Setup wizard walks them through these steps:
   - Welcome
   - License agreement (GPL-3.0)
   - Installation path (default: `C:\Program Files\MHMPipeline`)
   - Start Menu folder
   - Optional desktop shortcut
   - Install (~5–10 minutes — this is mostly the LZMA2 archive being unpacked)
   - Finish
4. On Finish, the app launches automatically. A desktop shortcut and a Start Menu entry are created.

### Microsoft SmartScreen warning

The installer is unsigned (no EV code-signing certificate). On first launch, Windows SmartScreen will display:

> Windows protected your PC
> Microsoft Defender SmartScreen prevented an unrecognized app from starting.

The supervisor must click **More info** and then **Run anyway**. This is a one-time prompt — Windows remembers the decision. Make sure the supervisor knows to expect it.

If your institution requires a signed installer, an EV code-signing certificate costs roughly $200/year and is issued through any of the major certificate authorities. That step is outside the scope of this build pipeline.

## Troubleshooting

**`py` command not recognized on Windows.**
Python 3.12 is not on `PATH`. Re-run the Python installer and tick "Add python.exe to PATH". Or supply the absolute path in the `.bat` file.

**Inno Setup `ISCC.exe` not found.**
Inno Setup 6 is not installed at the default path. Download it from https://jrsoftware.org/isinfo.php and re-run the build. If you need a non-default install path, edit the path in `Build Installer.bat`.

**PyInstaller fails with `ImportError: ...`.**
A hidden import is missing. Add the offending module name to `hiddenimports=[...]` in `installer\windows\MHMPipeline.spec` and re-run the build.

**App crashes on first launch on the supervisor's machine.**
Ask the supervisor to send the contents of:

```
%LOCALAPPDATA%\Bar-Ilan University\MHMPipeline\logs\
```

`mhm_pipeline.log` (today's file) and `crash.log` (if present) contain the stack trace.

**Antivirus quarantines the installer.**
Some heuristic AV products flag unsigned PyInstaller-bundled apps. Either sign the installer (see above) or instruct the supervisor to whitelist the file.
