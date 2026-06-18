# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone `tre-engine` sidecar (onedir).

Build: python -m PyInstaller --noconfirm --clean \
         --distpath packaging/dist --workpath packaging/build packaging/tre-engine.spec
Paths are resolved from SPECPATH so the build works from any CWD; datas use
(src, dest) tuples (no OS-specific path separator) for cross-platform CI.
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = os.path.dirname(SPECPATH)  # repo root (packaging/..)

# tree.gui.server loads templates/static via Path(__file__).parent, so they must
# land at _internal/tree/gui/{templates,static}. collect_data_files('tree') misses
# them under an editable install, so declare them explicitly.
datas = [
    (os.path.join(ROOT, "tree_engine", "tree", "gui", "templates"), "tree/gui/templates"),
    (os.path.join(ROOT, "tree_engine", "tree", "gui", "static"), "tree/gui/static"),
]
datas += collect_data_files("tree")
binaries = []
hiddenimports = ["websockets"] + collect_submodules("uvicorn")

for pkg in ("qdrant_client", "huggingface_hub"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [os.path.join(ROOT, "packaging", "tre_entry.py")],
    pathex=[os.path.join(ROOT, "tree_engine")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tre-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="tre-engine")
