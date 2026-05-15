# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for UL Transcoding.

Requirements:
    pip install pyinstaller

Build (from this directory):
    pyinstaller ul_transcoding.spec

Output:
    dist/ul-transcoding          (Linux / macOS)
    dist/ul-transcoding.exe      (Windows)

Notes:
  - tkinter and the full Tcl/Tk runtime are bundled automatically from
    your build machine's Python installation.  Make sure tkinter works
    on the build machine before running PyInstaller
    (python -c "import tkinter; tkinter.Tk().destroy()").
  - FFmpeg / ffprobe are NOT bundled — they must still be present in the
    user's PATH at runtime.
  - console=True is intentional: it is required for CLI mode to produce
    output.  On Windows this means a console window will appear behind the
    GUI; that is expected behaviour for a hybrid GUI/CLI tool.
  - If UPX is not installed on your system, set upx=False below (or just
    ignore the warning — PyInstaller falls back gracefully).
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Pull in every tkinter sub-module and the Tcl/Tk data files that
# PyInstaller's built-in hook might miss on some platforms.
tk_hidden = collect_submodules("tkinter")
tk_datas  = collect_data_files("tkinter")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=tk_datas,
    hiddenimports=tk_hidden + [
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "tkinter.scrolledtext",
        # json, pathlib, threading, etc. are pure-Python stdlib — PyInstaller
        # picks them up automatically; listed here only for documentation.
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim modules we definitely don't use to keep the binary smaller.
    excludes=[
        "numpy", "pandas", "PIL", "matplotlib",
        "scipy", "cryptography", "email", "html",
        "http", "urllib", "xml", "xmlrpc",
        "unittest", "pydoc", "doctest",
    ],
    noarchive=False,
    optimize=1,   # strip docstrings (safe for this app)
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ul-transcoding",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,        # set True on Linux/macOS to shave a few MB
    upx=True,           # set False if UPX is not installed
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # must be True for CLI output to work
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,   # None = native arch; set "x86_64"/"arm64" to cross-compile
    codesign_identity=None,
    entitlements_file=None,
    icon=None,          # set to "icon.ico" (Win) or "icon.icns" (macOS) if you have one
)
