@echo off
REM ===========================================================================
REM MHM Pipeline - Windows Installer Build (one-click).
REM
REM Prerequisites on this Windows host:
REM   1. Python 3.12 from python.org (so `py -3.12` works).
REM   2. Inno Setup 6 from jrsoftware.org (default install path).
REM
REM Double-click this file. ~30 minutes later, dist\MHMPipeline-Setup-0.1.0.exe
REM appears in the repo root.
REM ===========================================================================

setlocal
cd /d "%~dp0\..\.."

echo === MHM Pipeline Windows Installer Build ===
echo.

echo [1/4] Creating Python 3.12 build venv...
if not exist .venv-build (
    py -3.12 -m venv .venv-build
    if errorlevel 1 goto :err
)
call .venv-build\Scripts\activate.bat
if errorlevel 1 goto :err

echo.
echo [2/4] Installing PyInstaller and project dependencies...
python -m pip install --upgrade pip --quiet
if errorlevel 1 goto :err
python -m pip install --quiet pyinstaller
if errorlevel 1 goto :err
python -m pip install --quiet -e .
if errorlevel 1 goto :err

echo.
echo [3/4] Running PyInstaller (this takes ~10 minutes; output in dist\MHMPipeline\)...
pyinstaller installer\windows\MHMPipeline.spec --noconfirm
if errorlevel 1 goto :err

echo.
echo [4/4] Running Inno Setup compiler...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo ERROR: Inno Setup 6 not found at "%ISCC%".
    echo Install it from https://jrsoftware.org/isdl.php and re-run.
    goto :err
)
"%ISCC%" installer\windows\build_installer.iss
if errorlevel 1 goto :err

echo.
echo === DONE ===
echo Installer: dist\MHMPipeline-Setup-0.1.0.exe
echo Send that single file to the supervisor.
pause
exit /b 0

:err
echo.
echo *** BUILD FAILED ***
echo Scroll up to see which step failed.
pause
exit /b 1
