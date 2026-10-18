@echo off
REM ============================================================
REM  SystemRestore build script - package a single-file exe
REM  Usage: double-click build.bat, or run from cmd in this dir
REM  Requires: Python 3.10+ (PyInstaller auto-installed if missing)
REM ============================================================
setlocal

chcp 65001 >nul
cd /d "%~dp0"

set PYTHON=python
where %PYTHON% >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

echo [1/4] Checking PyInstaller...
%PYTHON% -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo       PyInstaller missing, installing...
    %PYTHON% -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

echo [2/4] Cleaning old build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist SystemRestore.spec del /q SystemRestore.spec

echo [3/4] Packaging with PyInstaller...
%PYTHON% -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name SystemRestore ^
    --icon assets\icon.ico ^
    --add-data "sysrestore\web;sysrestore\web" ^
    --clean ^
    run.py
if errorlevel 1 (
    echo [ERROR] PyInstaller failed. See output above.
    pause
    exit /b 1
)

echo [4/4] Done.
if exist dist\SystemRestore.exe (
    echo.
    echo ============================================================
    echo  Build succeeded: dist\SystemRestore.exe
    echo  Single file, no Python needed - copy it to any Windows PC.
    echo ============================================================
) else (
    echo [ERROR] dist\SystemRestore.exe not found.
    pause
    exit /b 1
)

pause
endlocal
