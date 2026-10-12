@echo off
rem ASCII-only on purpose: Cyrillic inside a .bat breaks on console codepages.
rem All user-facing Russian text is printed by run_export.py instead.
chcp 65001 >nul 2>&1
title SWGOH roster export
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY="

py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    goto :found
)

python --version >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    goto :found
)

goto :nopython

:found
%PY% "%~dp0run_export.py" %*
set "RC=%ERRORLEVEL%"
echo.
echo Press any key to close this window.
pause >nul
exit /b %RC%

:nopython
echo.
echo   Python not found.
echo.
echo   Install it from https://www.python.org/downloads/
echo   During setup, tick "Add python.exe to PATH".
echo   Then run this file again.
echo.
echo Press any key to close this window.
pause >nul
exit /b 1
