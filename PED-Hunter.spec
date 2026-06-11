# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

python_root = Path(sys.base_prefix)
tcl_root = python_root / 'tcl'
tcl_datas = []
for name, target in (('tcl8.6', '_tcl_data'), ('tk8.6', '_tk_data')):
    source = tcl_root / name
    if source.exists():
        tcl_datas.append((str(source), target))


a = Analysis(
    ['tools\\pyinstaller_entry.py'],
    pathex=['src'],
    binaries=[],
    datas=[('data/catalog', 'data/catalog'), *tcl_datas],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['tools\\pyinstaller_tcl_runtime.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PED-Hunter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
