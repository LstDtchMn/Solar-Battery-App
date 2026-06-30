#!/usr/bin/env python3
"""Build the standalone KiloVaultMonitor executable with PyInstaller.

    pip install pyinstaller bleak pyserial
    python scripts/build_exe.py

Output: dist/KiloVaultMonitor (or dist/KiloVaultMonitor.exe on Windows).
Run it from the repository root.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run: pip install pyinstaller")
        return 1

    spec = ROOT / "kilovault.spec"
    print(f"Building from {spec} ...")
    rc = subprocess.call(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(spec)],
        cwd=str(ROOT),
    )
    if rc != 0:
        print("Build failed.")
        return rc

    out = ROOT / "dist" / ("KiloVaultMonitor.exe" if sys.platform == "win32"
                           else "KiloVaultMonitor")
    print("\nDone." if out.exists() else "\nBuild finished (check dist/).")
    if out.exists():
        print(f"  Executable: {out}")
        print("  Double-click it (Windows) or run it to start the dashboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
