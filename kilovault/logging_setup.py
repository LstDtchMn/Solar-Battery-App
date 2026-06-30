"""Logging setup for the monitor.

A good log is essential for supporting a non-technical user remotely: when
something does not work, they can send you ``kilovault.log`` (or a one-click
diagnostics bundle) and you can see exactly what happened.

- Everything (DEBUG and up) goes to a rotating file ``<data_dir>/kilovault.log``
  so the history survives restarts but never grows without bound.
- A cleaner, friendlier stream goes to the console at the chosen level.
- Uncaught exceptions and warnings are captured into the log too.
"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import sys
from pathlib import Path
from typing import Optional

LOG_FILENAME = "kilovault.log"
_MAX_BYTES = 2_000_000
_BACKUPS = 5

_log = logging.getLogger("kilovault")


def get_log_path(data_dir) -> Path:
    return Path(data_dir) / LOG_FILENAME


def setup_logging(data_dir, level: str = "INFO", console: bool = True) -> Path:
    """Configure the ``kilovault`` logger. Returns the log file path."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / LOG_FILENAME

    _log.setLevel(logging.DEBUG)
    for handler in list(_log.handlers):  # idempotent if called twice
        _log.removeHandler(handler)

    file_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    try:
        fh = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(file_fmt)
        _log.addHandler(fh)
    except Exception as exc:  # e.g. read-only dir; keep running with console only
        print(f"[kilovault] could not open log file {log_path}: {exc}", file=sys.stderr)

    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(getattr(logging, str(level).upper(), logging.INFO))
        ch.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        _log.addHandler(ch)

    _log.propagate = False
    logging.captureWarnings(True)

    def _excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        _log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _excepthook
    return log_path


def log_environment(cfg) -> None:
    """Log a startup banner with everything useful for diagnosis."""
    import kilovault

    log = logging.getLogger("kilovault.startup")
    log.info("=" * 60)
    log.info("KiloVault HLX+ Monitor v%s starting", kilovault.__version__)
    log.info("Platform : %s", platform.platform())
    log.info("Python   : %s (%s)", platform.python_version(), sys.executable)
    log.info("Frozen   : %s", bool(getattr(sys, "frozen", False)))
    log.info("Transport: %s", cfg.transport.type)
    log.info("Database : %s", cfg.db_path)
    log.info("Web      : http://%s:%s", cfg.web.host, cfg.web.port)
    for mod in ("bleak", "serial"):
        try:
            m = __import__(mod)
            log.info("Dep %-8s: %s", mod, getattr(m, "__version__", "(unknown version)"))
        except Exception:
            log.info("Dep %-8s: not installed", mod)
    log.info("=" * 60)
