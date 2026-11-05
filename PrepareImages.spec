# -*- mode: python ; coding: utf-8 -*-
"""Однофайловая сборка PrepareImages (Windows, macOS, Linux).

Использование:
    python -m PyInstaller --clean --noconfirm PrepareImages.spec

Результат: dist/prepare_images.exe (Windows) или dist/prepare_images (macOS/Linux).
Внутрь упакованы интерпретатор Python, Pillow и профиль sRGB — на целевой
машине ничего устанавливать не нужно.
"""

import os

SRC = os.path.join(SPECPATH, "src")

a = Analysis(
    [os.path.join(SRC, "prepare_images.py")],
    pathex=[SRC],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Интерфейс консольный: GUI, тесты и научные библиотеки не нужны.
    excludes=["tkinter", "unittest", "pydoc_data", "test", "numpy"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="prepare_images",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
