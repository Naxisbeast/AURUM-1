"""Tests for the append-only trial ledger."""

from __future__ import annotations

import pytest

from aurum1.research.trial_ledger import (
    TrialRecord,
    log_trial,
    get_all_trials,
    trial_count,
    delete_trial,
)


def _make_record(variant_id: str = "D4", notes: str = "") -> TrialRecord:
    return TrialRecord(
        variant_id=variant_id,
        parent_family="donchian_breakout",
        n_obs=18,
        sharpe=1.27,
        skew=-0.3,
        kurtosis=3.5,
        return_series_path="reports/research/test.json",
        notes=notes or f"Test {variant_id}",
    )


class TestLogTrial:
    """Logging trials to the ledger."""

    def test_log_one_trial(self):
        record = _make_record("test_log_one")
        log_trial(record)
        trials = get_all_trials("donchian_breakout")
        matching = [t for t in trials if t["variant_id"] == "test_log_one"]
        assert len(matching) == 1
        assert matching[0]["sharpe"] == 1.27
        delete_trial(variant_id="test_log_one")

    def test_same_variant_twice_preserves_both(self):
        log_trial(_make_record("test_duplicate", "Run 1"))
        log_trial(_make_record("test_duplicate", "Run 2"))
        trials = get_all_trials("donchian_breakout")
        matching = [t for t in trials if t["variant_id"] == "test_duplicate"]
        assert len(matching) == 2
        notes = [t["notes"] for t in matching]
        assert "Run 1" in notes
        assert "Run 2" in notes
        delete_trial(variant_id="test_duplicate")

    def test_id_is_autoincrementing(self):
        log_trial(_make_record("test_auto_1"))
        log_trial(_make_record("test_auto_2"))
        trials = get_all_trials("donchian_breakout")
        t1 = [t for t in trials if t["variant_id"] == "test_auto_1"]
        t2 = [t for t in trials if t["variant_id"] == "test_auto_2"]
        assert len(t1) == 1
        assert len(t2) == 1
        assert t1[0]["id"] < t2[0]["id"]
        delete_trial(variant_id="test_auto_1")
        delete_trial(variant_id="test_auto_2")


class TestGetAllTrials:
    """Retrieving trials."""

    def test_returns_empty_for_unknown_family(self):
        trials = get_all_trials("nonexistent_family")
        assert trials == []

    def test_returns_filtered_by_family(self):
        # Clean up any leftovers from previous runs
        for vid in ["test_fam_a", "test_fam_b", "test_fam_c"]:
            try:
                delete_trial(variant_id=vid)
            except Exception:
                pass
        r1 = _make_record("test_fam_a")
        r1.parent_family = "family_a"
        log_trial(r1)
        r2 = _make_record("test_fam_b")
        r2.parent_family = "family_a"
        log_trial(r2)
        log_trial(TrialRecord(
            variant_id="test_fam_c",
            parent_family="other_family",
            n_obs=10, sharpe=0.5, skew=0.0, kurtosis=3.0,
            return_series_path="", notes="other",
        ))
        family_a = get_all_trials("family_a")
        assert len(family_a) == 2
        other = get_all_trials("other_family")
        assert len(other) == 1
        delete_trial(variant_id="test_fam_a")
        delete_trial(variant_id="test_fam_b")
        delete_trial(variant_id="test_fam_c")


class TestTrialCount:
    """Row count."""

    def test_increases_after_log(self):
        before = trial_count()
        log_trial(_make_record("test_count"))
        after = trial_count()
        assert after == before + 1
        delete_trial(variant_id="test_count")


class TestDeleteTrial:
    """Deleting trials."""

    def test_raises_without_args(self):
        with pytest.raises(ValueError, match="Either trial_id or variant_id"):
            delete_trial()

    def test_delete_by_id_removes_one(self):
        log_trial(_make_record("test_del_id"))
        log_trial(_make_record("test_del_id"))
        trials_before = get_all_trials("donchian_breakout")
        matching = [t for t in trials_before if t["variant_id"] == "test_del_id"]
        assert len(matching) == 2
        # Delete the first one
        delete_trial(trial_id=matching[0]["id"])
        trials_after = get_all_trials("donchian_breakout")
        remaining = [t for t in trials_after if t["variant_id"] == "test_del_id"]
        assert len(remaining) == 1
        delete_trial(variant_id="test_del_id")

    def test_delete_by_variant_id_removes_all(self):
        log_trial(_make_record("test_del_all"))
        log_trial(_make_record("test_del_all"))
        trials_before = get_all_trials("donchian_breakout")
        assert len([t for t in trials_before if t["variant_id"] == "test_del_all"]) == 2
        delete_trial(variant_id="test_del_all")
        trials_after = get_all_trials("donchian_breakout")
        assert len([t for t in trials_after if t["variant_id"] == "test_del_all"]) == 0
