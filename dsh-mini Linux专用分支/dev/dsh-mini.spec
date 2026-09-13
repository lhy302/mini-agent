# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for dsh-mini: one-file, GUI, zero external dependencies.
# Target: Windows 10 / Windows 11 (64-bit).

import os
import sys

block_cipher = None

extra_binaries = []
python_dir = os.path.dirname(sys.executable)
for name in ("vcruntime140.dll", "vcruntime140_1.dll", "python3.dll"):
    path = os.path.join(python_dir, name)
    if os.path.isfile(path):
        extra_binaries.append((path, "."))

a = Analysis(
    ['dsh-mini.py'],
    pathex=[],
    binaries=extra_binaries,
    datas=[],
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
