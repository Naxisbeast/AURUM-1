from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from obsidian.pipeline.sessions import add_new_york_session_columns, derive_new_york_session


def test_new_york_timezone_conversion_and_session_flags() -> None:
    frame = pd.DataFrame({"timestamp_utc": ["2026-01-15T15:30:00Z"]})

    enriched = add_new_york_session_columns(frame)

    assert enriched.loc[0, "ny_time"] == "10:30:00"
    assert int(enriched.loc[0, "ny_hour"]) == 10
    assert bool(enriched.loc[0, "is_ny_am_killzone"]) is True
    assert bool(enriched.loc[0, "is_silver_bullet"]) is True
    assert enriched.loc[0, "ny_session_label"] == "silver_bullet"
    assert enriched.loc[0, "timestamp_utc"] == "2026-01-15T15:30:00Z"


def test_dst_boundary_is_handled_by_america_new_york() -> None:
    before = derive_new_york_session(pd.Timestamp("2026-03-08T06:30:00Z"))
    after = derive_new_york_session(pd.Timestamp("2026-03-08T07:30:00Z"))

    assert before.ny_time == "01:30:00"
    assert after.ny_time == "03:30:00"
    assert before.ny_date == after.ny_date == "2026-03-08"


def test_asia_and_london_killzone_labels() -> None:
    frame = pd.DataFrame(
        {
            "timestamp_utc": [
                "2026-01-16T00:30:00Z",
                "2026-01-15T07:30:00Z",
            ]
        }
    )

    enriched = add_new_york_session_columns(frame)

    assert bool(enriched.loc[0, "is_asia_session"]) is True
    assert enriched.loc[0, "ny_session_label"] == "asia"
    assert bool(enriched.loc[1, "is_london_killzone"]) is True
    assert enriched.loc[1, "ny_session_label"] == "london_killzone"
