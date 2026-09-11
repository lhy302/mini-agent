@echo off
rem ============================================================
rem  DSH Minimal Agent - Windows launcher (ASCII only on purpose)
rem  Double-click this file, or run it from a terminal.
rem ============================================================
setlocal enableextensions
cd /d "%~dp0"
set "RC=1"

if exist "%~dp0..\dsh-mini.exe" (
    "%~dp0..\dsh-mini.exe" %*
    set "RC=%ERRORLEVEL%"
    goto :finish
)

if exist "%~dp0dsh-mini.exe" (
    "%~dp0dsh-mini.exe" %*
    set "RC=%ERRORLEVEL%"
    goto :finish
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 "%~dp0dsh-mini.py" %*
    set "RC=%ERRORLEVEL%"
    goto :finish
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python "%~dp0dsh-mini.py" %*
    set "RC=%ERRORLEVEL%"
    goto :finish
)

echo.
echo [ERROR] Neither dsh-mini.exe nor a Python 3 interpreter was found.
echo.
echo   Option 1: build the standalone exe once, with Python 3.8 installed:
echo             build-win7-exe.bat
echo   Option 2: install Python 3.8 (the last release that supports Windows 7)
echo             and run:  python dsh-mini.py
echo.
pause
exit /b 1

:finish
if not "%RC%"=="0" (
    echo.
    echo [exit code: %RC%]
    pause
)
exit /b %RC%
