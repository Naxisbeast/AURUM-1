"""Launch the AURUM-1 orchestrator in paper/live mode."""

from __future__ import annotations

import argparse
from pathlib import Path

from aurum1.data.ingestion import load_settings
from aurum1.orchestrator import Orchestrator
from aurum1.signals import MachineMode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AURUM-1 orchestrator")
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in MachineMode],
        default=MachineMode.RULE_REGIME.value,
        help="State-machine operating mode",
    )
    parser.add_argument(
        "--settings",
        default="aurum1/config/settings.yaml",
        help="Path to AURUM-1 settings file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings(Path(args.settings))
    settings.setdefault("orchestrator", {})["mode"] = args.mode
    orchestrator = Orchestrator(settings)
    orchestrator.run()


if __name__ == "__main__":
    main()
