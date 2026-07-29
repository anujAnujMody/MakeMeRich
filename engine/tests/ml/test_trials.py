"""Tests for `te.ml.trials.TrialLedger` — monotonic, no delete path."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from te.ml.trials import TrialLedger, trial_ledger


def _ledger() -> TrialLedger:
    return TrialLedger(create_engine("sqlite:///:memory:"))


def test_record_returns_incrementing_ids() -> None:
    ledger = _ledger()
    first = ledger.record(kind="orb-meta-cv", config_hash="abc123", sharpe=0.42, run_id="run-1")
    second = ledger.record(kind="orb-meta-cv", config_hash="def456", sharpe=0.10, run_id="run-2")
    assert second > first


def test_n_trials_counts_only_matching_scope() -> None:
    ledger = _ledger()
    ledger.record(kind="scope-a", config_hash="h1", sharpe=0.1, run_id="r1")
    ledger.record(kind="scope-a", config_hash="h2", sharpe=0.2, run_id="r2")
    ledger.record(kind="scope-b", config_hash="h3", sharpe=0.3, run_id="r3")

    assert ledger.n_trials("scope-a") == 2
    assert ledger.n_trials("scope-b") == 1
    assert ledger.n_trials("scope-nonexistent") == 0


def test_n_trials_is_monotonic_across_repeated_records() -> None:
    ledger = _ledger()
    counts = []
    for i in range(5):
        ledger.record(kind="scope-a", config_hash=f"h{i}", sharpe=0.1 * i, run_id=f"r{i}")
        counts.append(ledger.n_trials("scope-a"))
    assert counts == sorted(counts)
    assert counts == [1, 2, 3, 4, 5]


def test_trial_sharpes_returns_in_insertion_order() -> None:
    ledger = _ledger()
    ledger.record(kind="scope-a", config_hash="h1", sharpe=0.11, run_id="r1")
    ledger.record(kind="scope-a", config_hash="h2", sharpe=0.22, run_id="r2")
    assert ledger.trial_sharpes("scope-a") == [0.11, 0.22]


def test_trial_ledger_has_no_delete_method() -> None:
    """The structural guarantee: there is no way to call a `delete` on
    `TrialLedger` because no such method exists."""
    assert not hasattr(TrialLedger, "delete")
    assert not any(name.startswith("delete") for name in dir(TrialLedger) if not name.startswith("_"))


def test_sqlite_trigger_blocks_raw_delete() -> None:
    """Belt-and-suspenders: even a hand-run `DELETE FROM trial_ledger` via
    raw SQL against the underlying engine is rejected by the SQLite
    trigger, not just by the missing Python method."""
    engine = create_engine("sqlite:///:memory:")
    ledger = TrialLedger(engine)
    ledger.record(kind="scope-a", config_hash="h1", sharpe=0.1, run_id="r1")

    with pytest.raises(IntegrityError, match="monotonic"):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM trial_ledger"))

    # The row must still be there — the trigger aborted the whole statement.
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM trial_ledger")).scalar_one()
    assert count == 1


def test_physical_table_columns_match_expected_shape() -> None:
    assert {c.name for c in trial_ledger.columns} == {"id", "ts", "kind", "config_hash", "sharpe", "run_id"}
