@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title AI Narrative RPG
cd /d "%~dp0"

REM ===== 1. Find a real Python =====
REM Do not trust `where python` on its own: Win10/11 ships a Microsoft Store
REM alias (%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe) that exists even when
REM Python is not installed, so `where` reports success and running it opens the Store.
set "PYEXE="
for /f "delims=" %%p in ('where python 2^>nul') do (
    if not defined PYEXE (
        set "CAND=%%p"
        if "!CAND:WindowsApps=!"=="!CAND!" set "PYEXE=%%p"
    )
)

REM Fall back to the Windows py launcher
if not defined PYEXE (
    where py >nul 2>nul
    if not errorlevel 1 set "PYEXE=py"
)

if not defined PYEXE goto :no_python

REM Judge by output, not by exit code: impostors (the Store alias, a hollow `py`)
REM often return 0 for unfamiliar arguments. Only a real Python prints "Python 3.x".
set "PYVER="
for /f "delims=" %%v in ('"%PYEXE%" --version 2^>^&1') do set "PYVER=%%v"
set "PYCHK=!PYVER:Python 3=!"
if "!PYCHK!"=="!PYVER!" goto :no_python

REM Must be >= 3.10
"%PYEXE%" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto :bad_python

echo [Python] !PYVER!  (!PYEXE!)

REM ===== 2. Check whether dependencies are installed =====
"%PYEXE%" -c "import ttkbootstrap, requests" >nul 2>nul
if not errorlevel 1 goto :launch

echo.
echo First run: installing dependencies, please wait...

REM Try the bundled offline packages first (built for 64-bit Python 3.10-3.13, no network needed)
if exist vendor_packages (
    echo [1/2] Trying bundled offline packages ^(vendor_packages^)...
    "%PYEXE%" -m pip install --no-index --find-links vendor_packages -r requirements.txt >nul 2>nul
    if not errorlevel 1 goto :deps_ok
    echo       Could not install system-wide, retrying into the user directory...
    "%PYEXE%" -m pip install --user --no-index --find-links vendor_packages -r requirements.txt >nul 2>nul
    if not errorlevel 1 goto :deps_ok
    echo       Offline install failed ^(the bundle targets 64-bit Python 3.10-3.13^),
    echo       falling back to installing over the network.
    echo.
)

REM Network install (works with any Python version, needs a connection)
echo [2/2] Trying a network install ^(requires an internet connection^)...
"%PYEXE%" -m pip install -r requirements.txt
if not errorlevel 1 goto :deps_ok

REM System-wide install failed (usually permissions) -> retry into the user directory
echo       System-wide install was blocked, retrying into the user directory...
"%PYEXE%" -m pip install --user -r requirements.txt
if not errorlevel 1 goto :deps_ok

goto :install_failed

:deps_ok
echo.
echo Dependencies installed.
goto :launch

:launch
echo.
echo Starting the game...
"%PYEXE%" main.py
if errorlevel 1 (
    echo.
    echo [ERROR] The game exited with error code !errorlevel!
    echo Please send the error output above to the developer.
)
pause
exit /b 0

:bad_python
echo.
echo ============================================================
echo [ERROR] Python is too old: !PYVER!
echo.
echo   This program requires Python 3.10 or newer; yours is older.
echo   Download a current version from the official site (it can coexist
echo   with the old one):
echo     https://www.python.org/downloads/
echo   Be sure to check "Add Python to PATH" during installation.
echo ============================================================
echo.
pause
exit /b 1

:no_python
echo.
echo ============================================================
echo [ERROR] No usable Python 3 found.
echo.
echo   Note: the "python.exe" from the Microsoft Store does not count.
echo   Installing it will not help.
echo.
echo   Download and install Python 3.10 or newer from the official site:
echo     https://www.python.org/downloads/
echo   Be sure to check "Add Python to PATH" during installation.
echo ============================================================
echo.
pause
exit /b 1

:install_failed
echo.
echo ============================================================
echo [ERROR] Dependency installation failed, error code !errorlevel!
echo.
echo Likely causes and fixes:
echo   1. Python version does not match the offline bundle
echo      - The bundle supports 64-bit Python 3.10-3.13. If you are on a
echo        32-bit or older Python, a network install is required ^(already
echo        attempted above^).
echo      - Install manually: python -m pip install -r requirements.txt
echo   2. No network access, or the package index is unreachable
echo      - Check your connection and try again.
echo   3. No write permission for the system directory
echo      - A --user install was already attempted above. If it still fails,
echo        run this file as Administrator.
echo ============================================================
pause
exit /b 1
