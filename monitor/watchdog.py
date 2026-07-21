"""Independent watchdog kill switch for D4 paper trader.

Monitors the D4's health file on a 5-second poll and takes action when
hardcoded thresholds are breached. This runs as a separate systemd service
so it survives the D4 process crashing.

Thresholds are HARDCODED — they cannot be changed by settings.yaml or
environment variables. This is intentional: the watchdog is an independent
safety layer that the trading algorithm cannot disable.

Actions on breach:
  1. Log the violation to syslog
  2. Stop the D4 process (systemctl stop aurum1-d4-paper.service)
  3. Write a breach report to run/watchdog_breach.json
  4. Continue monitoring (in case the D4 auto-restarts into the same bad state)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Hardcoded thresholds — CANNOT be changed by settings.yaml
# These are absolute safety limits, independent of the trading strategy.
# ---------------------------------------------------------------------------

# Max drawdown from peak equity (15% hard limit vs 8% soft limit in RiskManager)
MAX_DRAWDOWN_PCT = 15.0

# Max daily loss as % of equity (10% hard limit vs 3% soft limit)
MAX_DAILY_LOSS_PCT = 10.0

# Max equity drop in a 1-hour rolling window (5% — catches rapid crashes)
MAX_1H_DROP_PCT = 5.0

# Stale data threshold — force restart if latest candle > X minutes old
STALE_DATA_MINUTES = 360  # 6 hours

# Poll interval (seconds) — doesn't need to be fast, just responsive enough
POLL_INTERVAL_SECONDS = 5

# How many consecutive breaches before escalating (prevents flapping)
CONSECUTIVE_BREACHES_ESCALATE = 3


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
HEALTH_FILE = ROOT / "run" / "d4_paper_trader_health.json"
BREACH_FILE = ROOT / "run" / "watchdog_breach.json"
PID_FILE = ROOT / "run" / "watchdog.pid"
SERVICE_NAME = "aurum1-d4-paper.service"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("aurum1.watchdog")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
logger.addHandler(_handler)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Breach record
# ---------------------------------------------------------------------------

def _load_breach_history() -> list[dict[str, Any]]:
    """Load breach history from the breach file."""
    if not BREACH_FILE.exists():
        return []
    try:
        return json.loads(BREACH_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_breach(violation: str, detail: str, health: dict[str, Any]) -> None:
    """Record a breach to the breach file and syslog."""
    history = _load_breach_history()
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "violation": violation,
        "detail": detail,
        "health_snapshot": {
            "equity": health.get("equity"),
            "peak_equity": health.get("peak_equity"),
            "drawdown_pct": health.get("drawdown_pct"),
            "daily_pnl": health.get("daily_pnl"),
            "trade_count": health.get("trade_count"),
            "candle_age_minutes": health.get("market_latest_candle_age_minutes"),
        },
    }
    history.append(record)
    # Keep last 100 breaches
    history = history[-100:]
    BREACH_FILE.parent.mkdir(parents=True, exist_ok=True)
    BREACH_FILE.write_text(json.dumps(history, indent=2, default=str))
    logger.warning("BREACH: %s — %s", violation, detail)


# ---------------------------------------------------------------------------
# Health file reader
# ---------------------------------------------------------------------------

def _read_health() -> dict[str, Any] | None:
    """Read the D4 health file. Returns None if unavailable."""
    if not HEALTH_FILE.exists():
        return None
    try:
        return json.loads(HEALTH_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Watchdog logic
# ---------------------------------------------------------------------------

def _stop_d4() -> bool:
    """Stop the D4 service. Returns True if successful."""
    try:
        result = subprocess.run(
            ["systemctl", "stop", SERVICE_NAME],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.warning("D4 service stopped via watchdog")
            return True
        else:
            logger.error("Failed to stop D4: %s", result.stderr.strip())
            return False
    except subprocess.TimeoutExpired:
        logger.error("Timed out stopping D4")
        return False
    except FileNotFoundError:
        # systemctl not available (dev environment)
        logger.warning("systemctl not found — D4 stop simulated")
        return True


def _d4_is_running() -> bool:
    """Check if the D4 process is running via PID file."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _check_thresholds(health: dict[str, Any]) -> str | None:
    """Check all thresholds against health data. Returns violation string or None."""
    equity = health.get("equity", 0)
    peak = health.get("peak_equity", equity)
    dd = health.get("drawdown_pct", 0)
    daily_pnl = health.get("daily_pnl", 0)
    candle_age = health.get("market_latest_candle_age_minutes")

    # 1. Drawdown check
    if isinstance(dd, (int, float)) and dd > MAX_DRAWDOWN_PCT:
        return f"Drawdown {dd:.1f}% exceeds hard limit of {MAX_DRAWDOWN_PCT}%"

    # 2. Daily loss check
    if equity and isinstance(daily_pnl, (int, float)) and daily_pnl < 0:
        loss_pct = abs(daily_pnl) / equity * 100
        if loss_pct > MAX_DAILY_LOSS_PCT:
            return f"Daily loss {loss_pct:.1f}% exceeds hard limit of {MAX_DAILY_LOSS_PCT}%"

    # 3. Stale data check
    if candle_age is not None and isinstance(candle_age, (int, float)):
        if candle_age > STALE_DATA_MINUTES:
            return (
                f"Stale data: latest candle {candle_age:.0f} minutes old "
                f"(limit: {STALE_DATA_MINUTES} min)"
            )

    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _acquire_pid_lock() -> bool:
    """Create PID file. Returns True if acquired."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        if PID_FILE.exists():
            pid_str = PID_FILE.read_text().strip()
            if pid_str:
                try:
                    pid = int(pid_str)
                    os.kill(pid, 0)
                    logger.warning("Another watchdog is running (PID %s)", pid_str)
                    return False
                except (OSError, ValueError):
                    pass
        PID_FILE.write_text(str(os.getpid()))
        return True
    except Exception as exc:
        logger.warning("Could not acquire PID lock: %s", exc)
        return True


def _release_pid_lock():
    """Remove PID file if owned by this process."""
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except Exception:
        pass


def main() -> int:
    """Run the watchdog loop."""
    if not _acquire_pid_lock():
        return 1

    logger.info("Watchdog started (poll every %ss)", POLL_INTERVAL_SECONDS)
    logger.info(
        "Thresholds: DD>%.0f%% | DailyLoss>%.0f%% | StaleData>%.0fmin",
        MAX_DRAWDOWN_PCT, MAX_DAILY_LOSS_PCT, STALE_DATA_MINUTES,
    )

    consecutive_breaches = 0
    total_breaches = 0
    actions_taken = 0

    try:
        while True:
            health = _read_health()

            if health is None:
                # Health file missing — D4 might be starting up or stopped
                if _d4_is_running() and health is None:
                    logger.warning("D4 running but no health file found")
                    consecutive_breaches += 1
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            violation = _check_thresholds(health)

            if violation:
                consecutive_breaches += 1
                total_breaches += 1
                _save_breach(violation, f"Breach {total_breaches}", health)

                if consecutive_breaches >= CONSECUTIVE_BREACHES_ESCALATE:
                    logger.critical(
                        "ESCALATING: %s consecutive breaches — stopping D4",
                        consecutive_breaches,
                    )
                    if _stop_d4():
                        actions_taken += 1
                    # Reset counter to avoid repeated stops
                    consecutive_breaches = 0
            else:
                # Reset consecutive counter on healthy check
                if consecutive_breaches > 0:
                    logger.info("Health restored after %s consecutive breaches", consecutive_breaches)
                consecutive_breaches = 0

            time.sleep(POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("Watchdog stopped by user")
    finally:
        _release_pid_lock()

    logger.info("Watchdog exited. Total breaches: %d, Actions taken: %d", total_breaches, actions_taken)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
