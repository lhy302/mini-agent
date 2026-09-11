@echo off
rem ============================================================
rem  dsh-mini cleanup tool  (ASCII only on purpose)
rem
rem  It targets the delivery folder that contains dsh-mini.exe:
rem    * if this script sits next to the exe, it cleans its own folder
rem    * if it sits in a sub-folder (e.g. dev\), it cleans the parent
rem  So it keeps working no matter where the whole folder is copied.
rem ============================================================
setlocal enableextensions

set "TARGET=%~dp0"
if exist "%~dp0dsh-mini.exe" goto :target_ready
if exist "%~dp0dsh-mini.config.json" goto :target_ready
if exist "%~dp0..\dsh-mini.exe" for %%I in ("%~dp0..") do set "TARGET=%%~fI\"
if exist "%~dp0..\dsh-mini.config.json" for %%I in ("%~dp0..") do set "TARGET=%%~fI\"

:target_ready
cd /d "%TARGET%"

:menu
cls
echo ============================================================
echo   dsh-mini  cleanup tool
echo ============================================================
echo.
echo   Target folder: %TARGET%
echo.
echo    1  Delete config      dsh-mini.config.json
echo    2  Delete sessions    sessions\*.json
echo    3  Delete BOTH config and sessions
echo    4  Clean fallback dir %%APPDATA%%\dsh-mini
echo    5  Clean temp leftovers %%TEMP%%\_MEI*
echo    0  Exit
echo.
echo   NOTE: deleting the config means you must enter your API key
echo         and base_url again on the next start.
echo.
choice /c 123450 /n /m "Press a key [0-5]: "
if errorlevel 6 goto :end
if errorlevel 5 goto :temp
if errorlevel 4 goto :fallback
if errorlevel 3 goto :both
if errorlevel 2 goto :ses
if errorlevel 1 goto :cfg
goto :end

:cfg
echo.
echo Delete: %TARGET%dsh-mini.config.json
call :confirm
if errorlevel 1 goto :menu
if exist "%TARGET%dsh-mini.config.json" (
    del /f /q "%TARGET%dsh-mini.config.json"
    echo   [ok] deleted
) else (
    echo   [--] not found
)
call :wait
goto :menu

:ses
echo.
echo Delete: %TARGET%sessions\*.json
if exist "%TARGET%sessions\*.json" (
    del /f /q "%TARGET%sessions\*.json"
    echo   [ok] deleted
) else (
    echo   [--] no session files found
)
call :wait
goto :menu

:both
echo.
echo Delete: %TARGET%dsh-mini.config.json
echo Delete: %TARGET%sessions\*.json
call :confirm
if errorlevel 1 goto :menu
if exist "%TARGET%dsh-mini.config.json" (
    del /f /q "%TARGET%dsh-mini.config.json"
    echo   [ok] config deleted
) else (
    echo   [--] config not found
)
if exist "%TARGET%sessions\*.json" (
    del /f /q "%TARGET%sessions\*.json"
    echo   [ok] sessions deleted
) else (
    echo   [--] no session files found
)
call :wait
goto :menu

:fallback
echo.
echo Delete: %%APPDATA%%\dsh-mini\dsh-mini.config.json
echo Delete: %%APPDATA%%\dsh-mini\sessions\*.json
if not defined APPDATA (
    echo   [!!] %%APPDATA%% is not defined on this system
    call :wait
    goto :menu
)
if exist "%APPDATA%\dsh-mini" (
    if exist "%APPDATA%\dsh-mini\dsh-mini.config.json" (
        del /f /q "%APPDATA%\dsh-mini\dsh-mini.config.json"
        echo   [ok] config deleted
    ) else (
        echo   [--] config not found
    )
    if exist "%APPDATA%\dsh-mini\sessions\*.json" (
        del /f /q "%APPDATA%\dsh-mini\sessions\*.json"
        echo   [ok] sessions deleted
    ) else (
        echo   [--] no session files found
    )
) else (
    echo   [--] "%APPDATA%\dsh-mini" does not exist
)
call :wait
goto :menu

:temp
echo.
echo Delete: %%TEMP%%\_MEI*
if not defined TEMP (
    echo   [!!] %%TEMP%% is not defined on this system
    call :wait
    goto :menu
)
set "found="
for /d %%D in ("%TEMP%\_MEI*") do (
    set "found=1"
    rd /s /q "%%~fD" 2>nul
    echo   [ok] removed %%D
)
if not defined found echo   [--] nothing to remove
call :wait
goto :menu

:confirm
choice /c yn /n /m "Confirm delete? [y/N]: "
if errorlevel 2 exit /b 1
exit /b 0

:wait
echo.
set "waitkey="
set /p "waitkey=Press Enter to continue... "
exit /b 0

:end
echo.
echo Bye.
