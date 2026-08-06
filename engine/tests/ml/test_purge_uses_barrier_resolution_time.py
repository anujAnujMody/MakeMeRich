"""The most common triple-barrier leakage bug, pinned.

Purging must use each label's RESOLUTION time — the moment its barrier was
actually touched — not its trigger time. The distinction is invisible in the
code (`purge_and_embargo` takes `prediction_times` and `evaluation_times`,
and both are just Series) and catastrophic in effect: a label triggered at
10:00 that resolves at 12:30 overlaps every training row up to 12:30, so
purging on 10:00 leaves two and a half hours of the future inside the
training set. The model then scores well out-of-sample for a reason that
will never exist live.

`scripts/train_meta_model.py` passes `_exit_ts` as `evaluation_times`, which
is correct. Nothing tested that it stayed correct — and swapping the two
arguments, or "simplifying" `evaluation_times` to equal `prediction_times`,
would leave every existing test green.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from te.ml.cv import purge_and_embargo

BASE = dt.datetime(2026, 4, 1, 9, 15, tzinfo=dt.UTC)


def _times(trigger_minutes: list[int], hold_minutes: list[int]) -> tuple[pd.Series, pd.Series]:
    prediction = pd.Series([BASE + dt.timedelta(minutes=m) for m in trigger_minutes])
    evaluation = pd.Series(
        [BASE + dt.timedelta(minutes=t + h) for t, h in zip(trigger_minutes, hold_minutes, strict=True)]
    )
    return prediction, evaluation


def test_a_label_still_running_into_the_test_block_is_purged() -> None:
    """Row 0 triggers at 09:15 and does not resolve until 12:15. The test
    block starts at 10:15. Its outcome is therefore partly determined by the
    same price path the test block is made of, and it must not be trained
    on — even though its TRIGGER is comfortably before the block."""
    prediction, evaluation = _times(
        trigger_minutes=[0, 10, 20, 60, 70, 80],
        #                ^ 3-hour hold: resolves at 12:15, inside the test block
        hold_minutes=[180, 5, 5, 5, 5, 5],
    )
    train_idx = np.array([0, 1, 2])
    test_idx = np.array([3, 4, 5])

    kept = purge_and_embargo(
        train_idx, test_idx, prediction_times=prediction, evaluation_times=evaluation, gap=dt.timedelta(0)
    )

    assert 0 not in kept, "a label resolving inside the test block leaked into training"
    assert set(kept) == {1, 2}, "short-lived labels that resolve before the block must be kept"


def test_purging_on_trigger_time_alone_would_keep_the_leaking_row() -> None:
    """The counterfactual, stated as a test so the bug's shape is on record:
    if `evaluation_times` were (wrongly) the trigger times, row 0 looks like
    a clean pre-block sample and survives — which is exactly the silent
    failure this file exists to catch."""
    prediction, _ = _times(trigger_minutes=[0, 10, 20, 60, 70, 80], hold_minutes=[180, 5, 5, 5, 5, 5])

    kept_wrongly = purge_and_embargo(
        np.array([0, 1, 2]),
        np.array([3, 4, 5]),
        prediction_times=prediction,
        evaluation_times=prediction,  # the bug
        gap=dt.timedelta(0),
    )

    assert 0 in kept_wrongly, "fixture no longer demonstrates the bug — revisit this test"


def test_longer_holds_purge_strictly_more() -> None:
    """Monotonicity: the longer a label takes to resolve, the more training
    rows it can contaminate. A purge that ignored resolution time would be
    flat in this parameter."""
    kept_counts = []
    for hold in (5, 60, 180):
        prediction, evaluation = _times(
            trigger_minutes=[0, 10, 20, 30, 60, 70, 80],
            hold_minutes=[hold] * 4 + [5, 5, 5],
        )
        kept = purge_and_embargo(
            np.array([0, 1, 2, 3]),
            np.array([4, 5, 6]),
            prediction_times=prediction,
            evaluation_times=evaluation,
            gap=dt.timedelta(0),
        )
        kept_counts.append(len(kept))

    assert kept_counts == sorted(kept_counts, reverse=True), (
        f"purging did not tighten as label horizons grew: {kept_counts}"
    )


def test_the_gap_holds_out_rows_on_both_sides_of_the_test_block() -> None:
    """The other half, and the part most likely to be misread from the call
    site alone: `gap` is NOT a forward-only embargo. It symmetrically pads
    every test row's label window to `[prediction - gap, evaluation + gap)`
    before overlap is computed, so it drops training rows on BOTH sides.

    That is deliberate (serial correlation runs in both directions and a
    label's own horizon is unknown at split time), but it means a caller who
    passes a large `gap` expecting only a forward embargo silently loses
    much more training data than intended — worth having stated in a test
    rather than discovered when a fold purges down to empty."""
    prediction, evaluation = _times(
        # Row 5 triggers at 11:05, 10 minutes after the test block's last
        # label resolves (10:55) — i.e. inside a 30-minute embargo window.
        trigger_minutes=[0, 10, 20, 90, 95, 110],
        hold_minutes=[5] * 6,
    )
    train_idx = np.array([0, 1, 2, 5])
    test_idx = np.array([3, 4])
    kwargs = {"prediction_times": prediction, "evaluation_times": evaluation}

    tight = purge_and_embargo(train_idx, test_idx, gap=dt.timedelta(minutes=30), **kwargs)
    assert 5 not in tight, "a post-block row inside the gap survived"
    assert {0, 1, 2}.issubset(set(tight)), "a 30-minute gap must not reach back to 09:15-09:35"

    wide = purge_and_embargo(train_idx, test_idx, gap=dt.timedelta(minutes=180), **kwargs)
    assert len(wide) == 0, "a 3-hour gap reaches both directions and should clear this whole train set"
