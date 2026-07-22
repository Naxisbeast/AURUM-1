"""Append-only log of every backtest trial run, for DSR / PBO analysis later.

Every variant gets logged here regardless of whether it's promoted, rejected,
or abandoned. This is the raw material for computing a Deflated Sharpe Ratio
once enough trials and live trades accumulate.

Usage:
    from aurum1.research.trial_ledger import log_trial, TrialRecord

    log_trial(TrialRecord(
        variant_id="D4_L20",
        parent_family="donchian_breakout",
        n_obs=18,
        sharpe=1.27,
        skew=-0.3,
        kurtosis=3.5,
        return_series_path="reports/research/d4_walk_forward_L20_local_results.json",
        notes="Standard D4, 18 walk-forward windows, unannualized Sharpe"
    ))
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[2] / "aurum1" / "data" / "trial_ledger.sqlite3"
SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS trials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        variant_id TEXT,
        parent_family TEXT,
        n_obs INTEGER,
        sharpe REAL,
        skew REAL,
        kurtosis REAL,
        return_series_path TEXT,
        notes TEXT,
        logged_at TEXT
    )
"""


@dataclass
class TrialRecord:
    """One backtest trial result, stored for later DSR analysis.

    Fields
    ------
    variant_id : str
        Unique identifier, e.g. "D4", "D4_L15", "D4_atr_filter_v2".
    parent_family : str
        Strategy family for clustering correlated trials, e.g. "donchian_breakout".
    n_obs : int
        Number of observations (trades or windows) the Sharpe/skew/kurtosis are based on.
    sharpe : float
        Unannualized Sharpe ratio — do NOT annualize before storing.
    skew : float
        Skewness of the return series.
    kurtosis : float
        Raw kurtosis of the return series (NOT excess kurtosis). Use
        scipy.stats.kurtosis(..., fisher=False) to compute.
    return_series_path : str
        Path to the raw per-trade or per-window return series, relative to repo root.
    notes : str
        Free text describing the variant, parameters, and any context.
    """
    variant_id: str
    parent_family: str
    n_obs: int
    sharpe: float
    skew: float
    kurtosis: float
    return_series_path: str
    notes: str
    logged_at: str = ""


def _init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(SCHEMA_SQL)
    conn.commit()
    return conn


def log_trial(record: TrialRecord) -> None:
    """Append a trial record to the ledger. Each call creates a new row,
    preserving history even if the same variant_id is logged again (e.g.
    after a data refresh or parameter change)."""
    record.logged_at = datetime.now(UTC).isoformat()
    conn = _init_db()
    try:
        conn.execute(
            """INSERT INTO trials
               (variant_id, parent_family, n_obs, sharpe, skew, kurtosis,
                return_series_path, notes, logged_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(asdict(record).values()),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_trials(family: str | None = None) -> list[dict[str, Any]]:
    """Retrieve all trials, optionally filtered by parent_family."""
    conn = _init_db()
    cur = conn.cursor()
    try:
        if family:
            cur.execute(
                "SELECT * FROM trials WHERE parent_family = ? ORDER BY logged_at",
                (family,),
            )
        else:
            cur.execute("SELECT * FROM trials ORDER BY logged_at")
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description] if rows else []
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def trial_count() -> int:
    """Number of trials logged."""
    conn = _init_db()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
    finally:
        conn.close()


def delete_trial(trial_id: int | None = None, *, variant_id: str | None = None) -> None:
    """Remove a trial by its autoincrement id (preferred) or variant_id.

    Parameters
    ----------
    trial_id : int, optional
        Delete a single trial by its autoincrement id. Use this variant to
        remove one specific entry without affecting other runs of the same
        variant_id.
    variant_id : str, optional
        Delete ALL rows with this variant_id. Use this to wipe an entire
        variant's history when correcting a systematic error.

    At least one of trial_id or variant_id must be provided.
    """
    if trial_id is None and variant_id is None:
        raise ValueError("Either trial_id or variant_id must be provided")
    conn = _init_db()
    try:
        if trial_id is not None:
            conn.execute("DELETE FROM trials WHERE id = ?", (trial_id,))
        elif variant_id is not None:
            conn.execute("DELETE FROM trials WHERE variant_id = ?", (variant_id,))
        conn.commit()
    finally:
        conn.close()


__all__ = ["TrialRecord", "log_trial", "get_all_trials", "trial_count", "delete_trial"]
