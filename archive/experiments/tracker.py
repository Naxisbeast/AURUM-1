"""Track experiment results in SQLite database."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from experiments.models import (
    ExperimentConfig,
    ExperimentResult,
    MetricComparison,
    MonteCarloResult,
    StressTestResult,
    WalkForwardMetrics,
)


class ExperimentTracker:
    """Persist and query experiment results in SQLite."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = Path(__file__).resolve().parent / "results" / "experiments.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS experiments (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        category TEXT,
                        config_json TEXT,
                        parent_experiment_id TEXT,
                        tags TEXT,
                        status TEXT DEFAULT 'completed',
                        passed INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL,
                        duration_seconds REAL DEFAULT 0,
                        trade_count INTEGER DEFAULT 0,
                        profit_factor REAL DEFAULT 0,
                        sharpe REAL DEFAULT 0,
                        max_drawdown REAL DEFAULT 0,
                        win_rate REAL DEFAULT 0,
                        total_net_pnl REAL DEFAULT 0,
                        gates_passed INTEGER DEFAULT 0
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS metric_comparisons (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        experiment_id TEXT NOT NULL,
                        metric_name TEXT NOT NULL,
                        baseline_value REAL,
                        experiment_value REAL,
                        absolute_change REAL,
                        relative_change REAL,
                        p_value REAL,
                        is_significant INTEGER,
                        FOREIGN KEY (experiment_id) REFERENCES experiments(id)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS walk_forward_windows (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        experiment_id TEXT NOT NULL,
                        window_index INTEGER,
                        profit_factor REAL,
                        sharpe REAL,
                        win_rate REAL,
                        max_drawdown REAL,
                        net_pnl REAL,
                        FOREIGN KEY (experiment_id) REFERENCES experiments(id)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS stress_tests (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        experiment_id TEXT NOT NULL,
                        test_name TEXT NOT NULL,
                        profit_factor REAL,
                        sharpe REAL,
                        max_drawdown REAL,
                        net_pnl REAL,
                        win_rate REAL,
                        trade_count INTEGER,
                        passed INTEGER,
                        FOREIGN KEY (experiment_id) REFERENCES experiments(id)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS monte_carlo (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        experiment_id TEXT NOT NULL UNIQUE,
                        n_simulations INTEGER,
                        median_final_equity REAL,
                        median_max_drawdown REAL,
                        pct95_max_drawdown REAL,
                        median_sharpe REAL,
                        ruin_probability REAL,
                        passed INTEGER,
                        FOREIGN KEY (experiment_id) REFERENCES experiments(id)
                    )
                """)

    def create_experiment(self, config: ExperimentConfig) -> str:
        """Create a new experiment record and return its ID."""
        experiment_id = str(uuid.uuid4())[:12]
        now = datetime.now(UTC).isoformat()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO experiments
                    (id, name, description, category, config_json, parent_experiment_id, tags, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        experiment_id,
                        config.name,
                        config.description,
                        config.category,
                        json.dumps(config.settings_overrides),
                        config.parent_experiment_id,
                        json.dumps(config.tags),
                        now,
                    ),
                )
        return experiment_id

    def save_result(self, result: ExperimentResult) -> None:
        """Persist a completed experiment result."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE experiments SET
                        status = ?, passed = ?, duration_seconds = ?,
                        trade_count = ?, profit_factor = ?, sharpe = ?,
                        max_drawdown = ?, win_rate = ?, total_net_pnl = ?,
                        gates_passed = ?
                    WHERE id = ?
                    """,
                    (
                        result.status,
                        1 if result.passed else 0,
                        result.duration_seconds,
                        result.trade_count,
                        result.profit_factor,
                        result.sharpe,
                        result.max_drawdown,
                        result.win_rate,
                        result.total_net_pnl,
                        result.gates_passed,
                        result.experiment_id,
                    ),
                )

                # Metric comparisons
                for mc in result.metric_comparisons:
                    conn.execute(
                        """
                        INSERT INTO metric_comparisons
                        (experiment_id, metric_name, baseline_value, experiment_value,
                         absolute_change, relative_change, p_value, is_significant)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            result.experiment_id,
                            mc.metric_name,
                            mc.baseline_value,
                            mc.experiment_value,
                            mc.absolute_change,
                            mc.relative_change,
                            mc.p_value,
                            1 if mc.is_significant else 0,
                        ),
                    )

                # Walk-forward windows
                if result.walk_forward:
                    for w in result.walk_forward.windows:
                        conn.execute(
                            """
                            INSERT INTO walk_forward_windows
                            (experiment_id, window_index, profit_factor, sharpe,
                             win_rate, max_drawdown, net_pnl)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                result.experiment_id,
                                w.get("window_index"),
                                w.get("profit_factor", 0),
                                w.get("sharpe", 0),
                                w.get("win_rate", 0),
                                w.get("max_drawdown", 0),
                                w.get("net_pnl", 0),
                            ),
                        )

                # Stress tests
                for st in result.stress_tests:
                    conn.execute(
                        """
                        INSERT INTO stress_tests
                        (experiment_id, test_name, profit_factor, sharpe,
                         max_drawdown, net_pnl, win_rate, trade_count, passed)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            result.experiment_id,
                            st.test_name,
                            st.profit_factor,
                            st.sharpe,
                            st.max_drawdown,
                            st.net_pnl,
                            st.win_rate,
                            st.trade_count,
                            1 if st.passed else 0,
                        ),
                    )

                # Monte Carlo
                if result.monte_carlo:
                    mc = result.monte_carlo
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO monte_carlo
                        (experiment_id, n_simulations, median_final_equity,
                         median_max_drawdown, pct95_max_drawdown, median_sharpe,
                         ruin_probability, passed)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            result.experiment_id,
                            mc.n_simulations,
                            mc.median_final_equity,
                            mc.median_max_drawdown,
                            mc.pct95_max_drawdown,
                            mc.median_sharpe,
                            mc.ruin_probability,
                            1 if mc.passed else 0,
                        ),
                    )

    def get_all_experiments(self) -> list[dict[str, Any]]:
        """Return summary of all experiments."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, name, category, status, passed, created_at,
                       profit_factor, sharpe, max_drawdown, win_rate,
                       gates_passed, trade_count
                FROM experiments
                ORDER BY created_at DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        """Get full details for one experiment."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM experiments WHERE id = ?", (experiment_id,)
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["metrics"] = [
                dict(m)
                for m in conn.execute(
                    "SELECT * FROM metric_comparisons WHERE experiment_id = ?",
                    (experiment_id,),
                ).fetchall()
            ]
            result["walk_forward"] = [
                dict(w)
                for w in conn.execute(
                    "SELECT * FROM walk_forward_windows WHERE experiment_id = ? ORDER BY window_index",
                    (experiment_id,),
                ).fetchall()
            ]
            result["stress_tests"] = [
                dict(s)
                for s in conn.execute(
                    "SELECT * FROM stress_tests WHERE experiment_id = ?",
                    (experiment_id,),
                ).fetchall()
            ]
            result["monte_carlo"] = dict(
                conn.execute(
                    "SELECT * FROM monte_carlo WHERE experiment_id = ?",
                    (experiment_id,),
                ).fetchone()
                or {}
            )
            return result

    def summary_table(self) -> str:
        """Print a summary table of all experiments."""
        experiments = self.get_all_experiments()
        if not experiments:
            return "No experiments recorded yet."
        lines = [
            f"{'ID':<14} {'Name':<28} {'Cat':<8} {'PF':<8} {'Sharpe':<8} "
            f"{'DD':<8} {'PnL':<10} {'Gates':<6} {'Status':<8}",
            "-" * 100,
        ]
        for exp in experiments:
            status = "✅" if exp["passed"] else "❌" if exp["status"] == "completed" else "⏳"
            lines.append(
                f"{exp['id']:<14} {exp['name']:<28} {exp['category']:<8} "
                f"{exp['profit_factor']:<8.3f} {exp['sharpe']:<8.3f} "
                f"{exp['max_drawdown']:<8.1%} {exp['total_net_pnl']:<10.0f} "
                f"{exp['gates_passed']}/7{'':<2} {status}"
            )
        return "\n".join(lines)


__all__ = ["ExperimentTracker"]
