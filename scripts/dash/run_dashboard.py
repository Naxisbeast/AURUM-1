"""Launch the AURUM-1 Streamlit monitoring dashboard."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum1.data.ingestion import initialize_database, load_settings
from aurum1.execution import PaperBroker


def main() -> int:
    settings = load_settings(ROOT / "aurum1" / "config" / "settings.yaml")
    db_path = ROOT / str(settings.get("data", {}).get("db_path", "aurum1/data/aurum1.sqlite3"))
    initialize_database(db_path)
    if bool(settings.get("broker", {}).get("paper_trade", True)):
        PaperBroker(settings)

    monitor_settings = settings.get("monitor", {})
    port = str(monitor_settings.get("dashboard_port", 8501))
    host = str(monitor_settings.get("dashboard_host", "127.0.0.1"))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ROOT / "monitor" / "dashboard.py"),
        "--server.port",
        port,
        "--server.address",
        host,
        "--server.headless",
        "true",
    ]
    return subprocess.call(command, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
