@echo off
rem ============================================================
rem  打包 dsh-mini.exe（单文件、无控制台、Windows 10 / 11 现代环境）
rem
rem  目标机器不需要安装 Python，也不需要任何外部依赖。
rem ============================================================
setlocal enableextensions
cd /d "%~dp0"

set "PYEXE="
where python >nul 2>nul
if %ERRORLEVEL%==0 set "PYEXE=python"
if not defined PYEXE (
    where py >nul 2>nul
    if %ERRORLEVEL%==0 set "PYEXE=py"
)
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python38\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python38\python.exe"

if not defined PYEXE (
    echo [错误] 没找到 Python，请先安装 Python 3.8+ 并加入 PATH。
    pause
    exit /b 1
)

echo [1/4] 解释器：%PYEXE%
%PYEXE% -c "import sys;print(sys.version)"

echo [2/4] 检查 PyInstaller ...
%PYEXE% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo 安装 PyInstaller ...
    %PYEXE% -m pip install --disable-pip-version-check pyinstaller
    if errorlevel 1 (
        echo [错误] PyInstaller 安装失败。
        pause
        exit /b 1
    )
)

echo [3/4] 打包 dsh-mini.exe（单文件 / 无控制台 / 纯图形界面）...
%PYEXE% -m PyInstaller --noconfirm --clean ^
    --distpath "%~dp0.." ^
    --workpath "%~dp0build" ^
    "%~dp0dsh-mini.spec"
if errorlevel 1 (
    echo [错误] 打包失败。
    pause
    exit /b 1
)

echo [4/4] 收尾检查（离线编译）...
%PYEXE% -m py_compile "%~dp0dsh-mini.py"
if errorlevel 1 (
    echo [错误] 源码编译不过，先修代码。
    pause
    exit /b 1
)

echo.
echo 打包成功：%~dp0..\dsh-mini.exe
echo.
pause
