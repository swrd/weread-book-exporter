@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

set "PY_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PY_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PY_EXE if defined WEREAD_PYTHON if exist "%WEREAD_PYTHON%" set "PY_EXE=%WEREAD_PYTHON%"
if not defined PY_EXE set "PY_EXE=python"

"%PY_EXE%" -V >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

rem No args: launch GUI (prefer pythonw.exe to avoid an extra console window); with args: CLI mode
if not "%~1"=="" goto cli

set "GUI_EXE="
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    set "GUI_EXE=%~dp0.venv\Scripts\pythonw.exe"
) else (
    for %%X in (pythonw.exe) do if not defined GUI_EXE if not "%%~$PATH:X"=="" set "GUI_EXE=%%~$PATH:X"
)
if not defined GUI_EXE set "GUI_EXE=%PY_EXE%"
start "" "%GUI_EXE%" "%~dp0gui.py"
exit /b 0

:cli
"%PY_EXE%" "%~dp0main.py" %*

echo.
pause
