# PyInstaller spec — builds a single double-click KiloVaultMonitor executable.
#
#   pip install pyinstaller bleak pyserial
#   pyinstaller kilovault.spec
#   -> dist/KiloVaultMonitor(.exe)
#
# The static dashboard assets are bundled, and bleak's platform backend is
# collected so Bluetooth works inside the frozen build.

import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("kilovault/server/static", "kilovault/server/static")]
binaries = []
hiddenimports = []

# Pull in bleak (and its OS-specific backend) and pyserial fully.
for pkg in ("bleak", "serial"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# On Windows, bleak's BLE backend uses WinRT projections that collect_all does
# not always discover. Pull them in explicitly so BLE works in the frozen .exe.
if sys.platform == "win32":
    for pkg in ("winrt", "bleak_winrt"):
        try:
            hiddenimports += collect_submodules(pkg)
        except Exception:
            pass
    hiddenimports += [
        "winrt.windows.devices.bluetooth",
        "winrt.windows.devices.bluetooth.advertisement",
        "winrt.windows.devices.bluetooth.genericattributeprofile",
        "winrt.windows.foundation",
        "winrt.windows.storage.streams",
    ]

block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "numpy", "matplotlib", "PIL"],
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
    name="KiloVaultMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # keep a console so users can see status / read errors
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
