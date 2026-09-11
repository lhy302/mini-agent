@echo off
rem ============================================================
rem  打包 dsh-mini.exe（单文件、无控制台、Win7 SP1 及以上可用）
rem
rem  前置要求：
rem    * Python 3.8.x            （3.8 是最后一个支持 Windows 7 的 Python）
rem    * PyInstaller 5.13.2      （6.x 已放弃 Win7 与 Python 3.8）
rem
rem  产物：上层目录的 dsh-mini.exe（图形界面，唯一版本，无控制台黑框）
rem  目标机器不需要安装 Python，也不需要任何依赖。
rem ============================================================
setlocal enableextensions
cd /d "%~dp0"

set "PYEXE="
if exist "%LOCALAPPDATA%\Programs\Python\Python38\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python38\python.exe"
if not defined PYEXE if exist "%USERPROFILE%\py38\python.exe" set "PYEXE=%USERPROFILE%\py38\python.exe"
if not defined PYEXE if exist "%ProgramFiles%\Python38\python.exe" set "PYEXE=%ProgramFiles%\Python38\python.exe"
if not defined PYEXE if exist "%SystemDrive%\Python38\python.exe" set "PYEXE=%SystemDrive%\Python38\python.exe"
if not defined PYEXE if exist "%SystemDrive%\Python38-32\python.exe" set "PYEXE=%SystemDrive%\Python38-32\python.exe"
if not defined PYEXE (
    where py >nul 2>nul
    if %ERRORLEVEL%==0 set "PYEXE=py -3.8"
)
if not defined PYEXE (
    echo [错误] 没找到 Python 3.8，请先安装：
    echo         https://www.python.org/downloads/release/python-3810/
    echo         建议装到无中文、无空格的路径，例如 C:\py38
    pause
    exit /b 1
)

echo [1/4] 解释器：%PYEXE%
%PYEXE% -c "import sys;print(sys.version)"

echo [2/4] 安装 PyInstaller 5.13.2 ...
%PYEXE% -m pip install --disable-pip-version-check "pyinstaller==5.13.2"
if errorlevel 1 (
    echo [错误] PyInstaller 安装失败，换国内镜像再试：
    echo         %PYEXE% -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller==5.13.2
    pause
    exit /b 1
)

echo [3/4] 打包 dsh-mini.exe（单文件 / 无控制台）...
%PYEXE% -m PyInstaller --noconfirm --clean ^
    --distpath "%~dp0.." ^
    --workpath "%~dp0build" ^
    "%~dp0dsh-mini.spec"
if errorlevel 1 (
    echo [错误] 打包失败。
    pause
    exit /b 1
)

echo [4/4] 收尾检查（离线，不联网）...
%PYEXE% -m py_compile "%~dp0dsh-mini.py"
if errorlevel 1 (
    echo [错误] 源码编译不过，先修代码。
    pause
    exit /b 1
)

echo.
echo 完成：%~dp0..\dsh-mini.exe
echo.
echo 建议接着验证（见 dev\开发文档.md 第 10、11 节）：
echo   1) ..\dsh-mini.exe --selftest                 离线自检，应当 73/73
echo   2) ..\dsh-mini.exe --gui-autoclose 20         冒烟测试，退出码应为 0
echo   3) python gui-e2e.py --exe ..\dsh-mini.exe    GUI 端到端，应当 26/26
echo   4) 清掉 dev\build 与 __pycache__，交付目录里不要留测试日志
echo.
pause
