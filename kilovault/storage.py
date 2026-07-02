"""SQLite time-series storage.

The original HLX iT app kept **no** history ("Events are not saved into any kind
of history"). This module persists every battery's telemetry, a device registry
(friendly names / capacities), and an alarm-event log — all in a single local
SQLite file, fully offline.

A single connection is shared with ``check_same_thread=False`` and guarded by a
lock, because the async collector writes while the web server's request threads
read.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .protocol import BatterySample

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    address      TEXT PRIMARY KEY,
    name         TEXT,
    capacity_ah  REAL,
    model        TEXT,
    serial       TEXT,
    firmware     TEXT,
    first_seen   REAL,
    last_seen    REAL
);

CREATE TABLE IF NOT EXISTS samples (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    address       TEXT NOT NULL,
    ts            REAL NOT NULL,
    voltage       REAL,
    current       REAL,
    power         REAL,
    soc           REAL,
    temperature   REAL,
    cycles        INTEGER,
    total_capacity REAL,
    remaining_capacity REAL,
    status        INTEGER,
    cell_delta    REAL,
    min_cell      REAL,
    max_cell      REAL,
    cells         TEXT,
    crc_ok        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_samples_addr_ts ON samples(address, ts);

CREATE TABLE IF NOT EXISTS counters (
    address        TEXT PRIMARY KEY,
    wh_charged     REAL DEFAULT 0,
    wh_discharged  REAL DEFAULT 0,
    ah_charged     REAL DEFAULT 0,
    ah_discharged  REAL DEFAULT 0,
    since_ts       REAL
);

CREATE TABLE IF NOT EXISTS thresholds (
    address     TEXT PRIMARY KEY,
    overrides   TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    address     TEXT NOT NULL,
    code        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    message     TEXT,
    raised_ts   REAL NOT NULL,
    cleared_ts  REAL
);
CREATE INDEX IF NOT EXISTS idx_events_addr ON events(address, raised_ts);
"""


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # Wait (instead of raising 'database is locked') if another writer/reader
        # briefly holds the lock — e.g. the diagnostics bundle or a second process.
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- devices --------------------------------------------------------
    def upsert_device(
        self,
        address: str,
        name: str = "",
        model: str = "",
        serial: str = "",
        firmware: str = "",
        capacity_ah: Optional[float] = None,
    ) -> None:
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT address, name, capacity_ah FROM devices WHERE address=?",
                (address,),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO devices(address,name,capacity_ah,model,serial,"
                    "firmware,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?)",
                    (address, name or address, capacity_ah, model, serial,
                     firmware, now, now),
                )
            else:
                # Note: ``name`` is intentionally NOT updated here. Friendly
                # names are user-owned and managed only via set_device_name, so
                # a re-sighting that carries the advertised name never clobbers
                # a name the user has chosen.
                self._conn.execute(
                    "UPDATE devices SET last_seen=?, "
                    "model=COALESCE(NULLIF(?,''), model), "
                    "serial=COALESCE(NULLIF(?,''), serial), "
                    "firmware=COALESCE(NULLIF(?,''), firmware), "
                    "capacity_ah=COALESCE(?, capacity_ah) "
                    "WHERE address=?",
                    (now, model, serial, firmware, capacity_ah, address),
                )
            self._conn.commit()

    def set_device_name(self, address: str, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO devices(address,name,first_seen,last_seen) "
                "VALUES(?,?,?,?) ON CONFLICT(address) DO UPDATE SET name=excluded.name",
                (address, name, time.time(), time.time()),
            )
            self._conn.commit()

    def set_device_capacity(self, address: str, capacity_ah: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO devices(address,capacity_ah,first_seen,last_seen) "
                "VALUES(?,?,?,?) ON CONFLICT(address) DO UPDATE SET "
                "capacity_ah=excluded.capacity_ah",
                (address, capacity_ah, time.time(), time.time()),
            )
            self._conn.commit()

    def get_device(self, address: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM devices WHERE address=?", (address,)
            ).fetchone()
        return dict(row) if row else None

    def get_devices(self) -> Dict[str, dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM devices").fetchall()
        return {r["address"]: dict(r) for r in rows}

    # -- samples --------------------------------------------------------
    def insert_sample(self, s: BatterySample) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO samples(address,ts,voltage,current,power,soc,"
                "temperature,cycles,total_capacity,remaining_capacity,status,"
                "cell_delta,min_cell,max_cell,cells,crc_ok) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    s.address, s.timestamp or time.time(), s.voltage, s.current,
                    s.power, s.soc, s.temperature, s.cycles, s.total_capacity,
                    s.remaining_capacity, s.status, s.cell_delta, s.min_cell,
                    s.max_cell, json.dumps([round(c, 3) for c in s.active_cells]),
                    1 if s.crc_ok else 0,
                ),
            )
            self._conn.commit()

    def history(
        self,
        address: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        limit: int = 20000,
        columns: Iterable[str] = ("ts", "voltage", "current", "power", "soc",
                                  "temperature", "cell_delta"),
        max_points: Optional[int] = None,
    ) -> List[dict]:
        """Return time-series rows in ascending time order.

        When ``max_points`` is set and the window holds more rows than that, the
        rows are evenly down-sampled across the *whole* window (instead of
        truncating to only the most recent ones), so a multi-day chart still
        shows the full span.
        """
        cols = ",".join(c for c in columns if c.isidentifier())
        where = "address=?"
        args: list = [address]
        if since is not None:
            where += " AND ts>=?"
            args.append(since)
        if until is not None:
            where += " AND ts<=?"
            args.append(until)

        with self._lock:
            step = 1
            n = 0
            if max_points and max_points > 0:
                n = self._conn.execute(
                    f"SELECT COUNT(*) FROM samples WHERE {where}", args
                ).fetchone()[0]
                if n > max_points:
                    step = -(-n // max_points)  # ceil(n / max_points) >= 2
            if step > 1:
                # Number rows per-address by time (the global AUTOINCREMENT id is
                # shared across batteries, so `id % step` would drop/bias one
                # battery's rows when samples interleave). ROW_NUMBER is contiguous
                # per address, giving an even sample across the whole window.
                # Anchor on the first row ((rn-1) % step) and always include the
                # newest (rn = n) so both endpoints of the chart are present.
                # Order by rn (not ts) so callers may request columns without ts.
                q = (f"SELECT {cols} FROM ("
                     f"  SELECT {cols}, ROW_NUMBER() OVER (ORDER BY ts) AS rn "
                     f"  FROM samples WHERE {where}"
                     f") WHERE (rn - 1) % ? = 0 OR rn = ? ORDER BY rn ASC LIMIT ?")
                rows = self._conn.execute(q, args + [step, n, limit]).fetchall()
                return [dict(r) for r in rows]
            q = f"SELECT {cols} FROM samples WHERE {where} ORDER BY ts DESC LIMIT ?"
            rows = self._conn.execute(q, args + [limit]).fetchall()
        return [dict(r) for r in reversed(rows)]

    def daily_summary(self, address: str, days: int = 30) -> List[dict]:
        """Per-day rollup for one battery (local time), newest first.

        Energy in/out per day is approximated from the mean charge/discharge
        power over the day multiplied by the span the samples cover.
        """
        q = (
            "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') AS day, "
            "COUNT(*) n, MIN(voltage) min_v, MAX(voltage) max_v, AVG(voltage) avg_v, "
            "MIN(soc) min_soc, MAX(soc) max_soc, "
            "MIN(temperature) min_t, MAX(temperature) max_t, "
            "AVG(CASE WHEN power > 0 THEN power ELSE 0 END) avg_chg, "
            "AVG(CASE WHEN power < 0 THEN -power ELSE 0 END) avg_dis, "
            "(MAX(ts) - MIN(ts)) / 3600.0 span_h "
            "FROM samples WHERE address=? GROUP BY day ORDER BY day DESC LIMIT ?"
        )
        with self._lock:
            rows = self._conn.execute(q, (address, days)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            span = d.pop("span_h") or 0.0
            d["wh_charged"] = round((d.pop("avg_chg") or 0.0) * span, 1)
            d["wh_discharged"] = round((d.pop("avg_dis") or 0.0) * span, 1)
            for k in ("min_v", "max_v", "avg_v", "min_t", "max_t"):
                if d.get(k) is not None:
                    d[k] = round(d[k], 2)
            for k in ("min_soc", "max_soc"):
                if d.get(k) is not None:
                    d[k] = round(d[k])
            out.append(d)
        return out

    def latest(self, address: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM samples WHERE address=? ORDER BY ts DESC LIMIT 1",
                (address,),
            ).fetchone()
        return dict(row) if row else None

    def stats(self, address: str, since: Optional[float] = None) -> dict:
        q = (
            "SELECT MIN(voltage) min_v, MAX(voltage) max_v, "
            "MIN(temperature) min_t, MAX(temperature) max_t, "
            "MIN(soc) min_soc, MAX(soc) max_soc, COUNT(*) n FROM samples "
            "WHERE address=?"
        )
        args: list = [address]
        if since is not None:
            q += " AND ts>=?"
            args.append(since)
        with self._lock:
            row = self._conn.execute(q, args).fetchone()
        return dict(row) if row else {}

    def prune(self, older_than_days: float) -> int:
        cutoff = time.time() - older_than_days * 86400
        with self._lock:
            cur = self._conn.execute("DELETE FROM samples WHERE ts<?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    # -- energy counters (persist across restarts) ----------------------
    def get_counters(self, address: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM counters WHERE address=?", (address,)
            ).fetchone()
        return dict(row) if row else None

    def save_counters(self, address, wh_charged, wh_discharged,
                      ah_charged, ah_discharged, since_ts) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO counters(address,wh_charged,wh_discharged,ah_charged,"
                "ah_discharged,since_ts) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(address) DO UPDATE SET wh_charged=excluded.wh_charged,"
                "wh_discharged=excluded.wh_discharged,ah_charged=excluded.ah_charged,"
                "ah_discharged=excluded.ah_discharged,since_ts=excluded.since_ts",
                (address, wh_charged, wh_discharged, ah_charged, ah_discharged, since_ts),
            )
            self._conn.commit()

    def reset_counters(self, address: str, since_ts: float) -> None:
        self.save_counters(address, 0, 0, 0, 0, since_ts)

    # -- per-battery alarm threshold overrides --------------------------
    def get_thresholds(self, address: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT overrides FROM thresholds WHERE address=?", (address,)
            ).fetchone()
        if not row or not row["overrides"]:
            return {}
        try:
            parsed = json.loads(row["overrides"])
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def get_all_thresholds(self) -> Dict[str, dict]:
        with self._lock:
            rows = self._conn.execute("SELECT address, overrides FROM thresholds").fetchall()
        out = {}
        for r in rows:
            try:
                parsed = json.loads(r["overrides"]) if r["overrides"] else {}
            except ValueError:
                parsed = {}
            out[r["address"]] = parsed if isinstance(parsed, dict) else {}
        return out

    def set_thresholds(self, address: str, overrides: dict) -> None:
        payload = json.dumps(overrides)
        with self._lock:
            self._conn.execute(
                "INSERT INTO thresholds(address,overrides) VALUES(?,?) "
                "ON CONFLICT(address) DO UPDATE SET overrides=excluded.overrides",
                (address, payload),
            )
            self._conn.commit()

    # -- events ---------------------------------------------------------
    def raise_event(self, address: str, code: str, severity: str, message: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events(address,code,severity,message,raised_ts) "
                "VALUES(?,?,?,?,?)",
                (address, code, severity, message, time.time()),
            )
            self._conn.commit()
            return cur.lastrowid

    def clear_event(self, event_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE events SET cleared_ts=? WHERE id=? AND cleared_ts IS NULL",
                (time.time(), event_id),
            )
            self._conn.commit()

    def recent_events(self, address: Optional[str] = None, limit: int = 200) -> List[dict]:
        q = "SELECT * FROM events"
        args: list = []
        if address:
            q += " WHERE address=?"
            args.append(address)
        q += " ORDER BY raised_ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    # -- export ---------------------------------------------------------
    def export_csv(self, path: Path, address: Optional[str] = None,
                   since: Optional[float] = None) -> int:
        import csv

        q = ("SELECT address,ts,voltage,current,power,soc,temperature,cycles,"
             "total_capacity,remaining_capacity,status,cell_delta,min_cell,"
             "max_cell,cells,crc_ok FROM samples")
        args: list = []
        conds = []
        if address:
            conds.append("address=?")
            args.append(address)
        if since is not None:
            conds.append("ts>=?")
            args.append(since)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY ts"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            if rows:
                w.writerow(rows[0].keys())
                for r in rows:
                    w.writerow(list(r))
        return len(rows)
