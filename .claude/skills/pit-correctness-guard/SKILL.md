---
name: pit-correctness-guard
description: Point-in-time correctness rules for the trading engine's bar data and feature/label construction — the bar-close gate, ingested_at vs event_ts, and the single dataset path. Use when reading market data, building features, writing backtests, or touching anything under engine/te/data or engine/te/ml.
---

# Point-in-time correctness guard

This project's engine (`engine/te/`) has exactly one rule that, if violated, silently invalidates
every backtest, DSR/PBO number, and ML label built afterward: **nothing may see data it couldn't have
seen at the time**. This skill exists because that class of bug is invisible in code review — it
produces a program that runs, compiles, and passes naive tests, and only breaks the honesty of the
numbers it reports.

## The bar-close gate

Every bar row in `engine/te/data/barstore.py`'s `BarStore` carries two timestamps:
- `event_ts` — what the bar is *about* (its open time)
- `ingested_at` — when *our system* actually recorded it

`engine/te/data/asof.py`'s `bars_asof(store, symbol, as_of, lookback, interval)` is the **only**
sanctioned way to read bars anywhere in the codebase. It filters on:

```
close_ts <= as_of AND ingested_at <= as_of
```

where `close_ts = event_ts + interval`. Both halves matter:
- The `<=` on **close**, not open, is what stops a strategy from seeing a bar before it actually
  finished forming.
- The `ingested_at` half is what stops a backfilled/late-arriving/revised bar from leaking into a
  decision that happened before that data existed. This is the harder bug to catch by inspection —
  it only shows up if you diff `event_ts` against `ingested_at` deliberately.

**Never** add a second bar-reading entry point. If a new module needs bars, it imports `bars_asof`. A
`grep -rn "BarStore" engine/te` that turns up a `.read(` call outside `te/data/asof.py` is a bug.

## The single dataset path (ML features)

`engine/te/ml/dataset.py`'s `build_training_set(as_of, spec, store, db, for_inference=False)` is the
**only** route from raw bars to a feature vector, for both training AND live inference — inference
calls the exact same function with `as_of=now()`. This is what makes online/offline skew structurally
impossible: there is no second feature-computation code path that could drift from the first. If you
ever find yourself writing a second "compute the features for live use" function, stop — extend
`build_training_set` instead.

## Disjointness between primary and secondary features

`engine/te/ml/featurespec.py` defines `PRIMARY_VOCABULARY` (the ORB rule's own feature names) and
raises if a secondary `FeatureSpec` uses any of them. This isn't pedantry — meta-labeling only works
when the secondary model sees information the primary rule didn't already use (see `honest-metrics`
for why). Never widen `SECONDARY_V1`'s columns to include anything the primary strategy already
computes.

## Fitting rule

Scalers, imputers, and calibrators are fit **inside the training fold only**, via an sklearn
`Pipeline` — never on the full dataset before a train/test split. A manual
`StandardScaler().fit(all_X)` before splitting is a silent, common leak. `engine/te/ml/calibrate.py`'s
Platt/sigmoid calibration is fit on a *purged* split for the same reason.

## Quick self-check before merging any change under `engine/te/data` or `engine/te/ml`

1. Does every bar read go through `bars_asof`? (grep for direct `BarStore` reads)
2. Does every feature computation go through `build_training_set`? (grep for a second feature-builder)
3. If you added a secondary ML feature, does `FeatureSpec.__post_init__` still pass — i.e. does it not
   collide with `PRIMARY_VOCABULARY`?
4. Is there a leakage test in `engine/tests/leakage/` covering the new code path? If not, add one —
   `test_no_train_test_overlap.py` and `test_shuffled_label_control.py` are the two patterns to copy.
