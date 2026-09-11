# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for dsh-mini: one-file, console, zero external dependencies.
# Build with Python 3.8 + PyInstaller 5.13.2 (Windows 7 compatible).
#
# The Universal CRT (UCRT) runtime is bundled explicitly when the build machine
# provides the redistributable copies in C:\Windows\System32\downlevel, so the
# resulting exe also runs on a clean Windows 7 SP1 box without KB2999226.

import glob
import os
import sys

block_cipher = None

# ---- 额外捆绑的运行时 DLL（Win7 免依赖的关键）----
extra_binaries = []
downlevel = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "downlevel")
if os.path.isdir(downlevel):
    for pattern in ("api-ms-win-crt-*.dll", "ucrtbase.dll"):
        for path in glob.glob(os.path.join(downlevel, pattern)):
            extra_binaries.append((path, "."))
python_dir = os.path.dirname(sys.executable)
for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "python3.dll"):
    path = os.path.join(python_dir, name)
    if os.path.isfile(path):
        extra_binaries.append((path, "."))
    else:
        sys32_path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", name)
        if os.path.isfile(sys32_path):
            extra_binaries.append((sys32_path, "."))

# ---- PE 定制：内嵌自包含 PowerShell 7 压缩包 ----
extra_datas = []
pwsh_zip = os.path.join(SPECPATH, "pwsh-embedded.zip")
if os.path.isfile(pwsh_zip):
    extra_datas.append((pwsh_zip, "."))

a = Analysis(
    ['dsh-mini.py'],
    pathex=[],
    binaries=extra_binaries,
    datas=extra_datas,
    hiddenimports=['msvcrt'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'distutils', 'lib2to3', 'pydoc', 'doctest', 'test', 'sqlite3', 'multiprocessing'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='dsh-mini',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,               # 无控制台黑框：图形界面是唯一界面
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=os.path.join(SPECPATH, 'dsh-mini.version.txt'),
)
