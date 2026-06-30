#!/usr/bin/env python3
"""Convenience launcher (handy for PyInstaller).

    python run.py serve --simulate
"""
from kilovault.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
