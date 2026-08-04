#!/usr/bin/env python
"""Trains the meta-labelling model on replayed ORB firings.

Usage:
    python -m scripts.train_meta_model
    python -m scripts.train_meta_model --instruments NIFTY,SENSEX

### What this model is, and is not

It NEVER picks a direction. `te.strategy.orb` alone decides whether and
which way to trade. This model answers one narrower question — *given that
ORB fired here, will it reach its target before its stop?* — and its only
route to a decision is `te.ml.gates.MaturityGate`, which at the `shadow`
stage returns `size_multiplier=1.0, veto=False` no matter what the model
says. Training it cannot change a single trade.

### Expect this to fail its gates, and treat that as success

The barrier sweep put the raw rule at roughly break-even, and the best
configuration was statistically indistinguishable from having tried six
things. A model trained on that is unlikely to clear DSR > 0.95 and
PBO < 0.05. Those thresholds exist precisely to say "not yet" — the prior
version of this project reported a 3.85 out-of-sample Sharpe and a 75%
accuracy that were both fabrications, and this machinery is the answer to
that. A failed gate here is the system working.

### Trials are counted forever

Every inner-CV trial is appended to `TrialLedger`, which has no delete path
(enforced by a SQLite trigger). DSR is deflated by the TOTAL number of
trials ever run, not this run's — so re-running this script repeatedly with
tweaks makes the bar harder, exactly as it should. Do not "reset trials to
get a better number": that is the fabrication path this ledger was built to
block.

### Comparing models raises the bar for all of them

`--model xgboost|catboost|random_forest` picks the classifier. Every backend
runs through identical data and identical CV splits, and every backend's
inner-CV trials land in the SAME ledger scope (`<run-id>:inner`). So running
all three against one `--run-id` roughly triples N, and the third run's DSR
is deflated for all three searches — which is the only honest way to read a
"best model". Running them under three different `--run-id`s and quoting the
winner's DSR would deflate it for a third of the search that really happened;
that is a fabricated edge, not a comparison.

Feature importances are NOT comparable across backends: XGBoost reports
gain, CatBoost reports prediction-value-change normalised to sum to 100, and
sklearn reports Gini decrease. Compare them across FOLDS within one model.

### One model across instruments

NIFTY and SENSEX are trained together rather than separately. They share the
same rule and the same barrier geometry, `dte` and `rv_iv_spread` already
distinguish them, and splitting ~1,200 samples into two ~600-sample models
would halve the statistical power for no clear gain.

The trained model is NOT registered or promoted here. Promotion is a manual,
reviewed action with its evidence written to `model_promotions` — see
`scripts/promote_model.py`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostModel, select_rates
from te.ml.barriers import ATM_SNAPSHOTS
from te.ml.featurespec import SECONDARY_V1
from te.ml.nightly import build_labeled_dataset
from te.ml.train import ModelBackend, train_meta_model
from te.ml.trials import TrialLedger
from te.persistence.db import make_engine, make_session_factory
from te.settings import Settings

#: DSR must EXCEED this for the model to be considered to have an
#: out-of-sample edge; PBO must fall BELOW its threshold. Both from the plan.
DSR_THRESHOLD = 0.95
PBO_THRESHOLD = 0.05


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instruments", default="NIFTY,SENSEX")
    parser.add_argument("--run-id", default=None, help="defaults to a timestamp-free label; pass one for traceability")
    parser.add_argument(
        "--model",
        default=ModelBackend.XGBOOST.value,
        choices=[b.value for b in ModelBackend],
        help=(
            "which classifier to fit. All three run through identical CV splits and record into the "
            "SAME trial-ledger scope, so running a second one against the same --run-id raises the "
            "trial count DSR deflates against for every model, not just the new one."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    backend = ModelBackend(args.model)

    chosen = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in ATM_SNAPSHOTS]
    if unknown:
        print(f"no measured ATM params for: {', '.join(unknown)}", file=sys.stderr)
        return 2

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    cost_model = CostModel(select_rates(load_charge_rate_table(settings.charges_path), dt.date.today()))

    print(f"spec {SECONDARY_V1.name} v{SECONDARY_V1.version}: {', '.join(SECONDARY_V1.columns)}\n")

    # ONE dataset path, shared with the scheduled nightly job
    # (`te.ml.nightly.run_nightly_training`). This loop used to live here in
    # full; a hand-run script and a cron job building their training sets
    # from two copies of the same code is how the two quietly start
    # describing different strategies.
    frame = build_labeled_dataset(session_factory, store, cost_model, instruments=tuple(chosen))

    if frame.empty:
        print("no labelled firings — nothing to train on", file=sys.stderr)
        return 1

    features = frame[list(SECONDARY_V1.columns)]
    labels = frame["_label"].astype(int)
    positives = float(labels.mean())
    print(f"\ntotal {len(frame):,} samples, {positives:.1%} positive "
          f"({int(labels.sum()):,} reached target before stop)\n")

    ledger = TrialLedger(engine)
    trials_before = ledger.n_trials("meta")
    run_id = args.run_id or f"orb-meta-{len(frame)}"

    try:
        result = train_meta_model(
            features=features,
            labels=labels,
            prediction_times=frame["_entry_ts"],
            evaluation_times=frame["_exit_ts"],
            spec=SECONDARY_V1,
            trial_ledger=ledger,
            run_id=run_id,
            sample_weights=frame["_weight"],
            backend=backend,
        )
    except ValueError as exc:
        # `validate_training_set` raises rather than warns — a dataset it
        # rejects cannot produce a trustworthy model, so refusing to train is
        # the correct outcome, not an error to work around.
        print(f"REFUSED TO TRAIN: {exc}", file=sys.stderr)
        return 1

    print("=" * 64)
    print(f"model                {backend.value}")
    print(f"samples              {result.n_labeled_samples:,}")
    print(f"trials before / now  {trials_before:,} / {result.n_trials_at_training:,}")
    print(f"DSR                  {result.dsr:.4f}   (needs > {DSR_THRESHOLD})")
    pbo_text = "n/a" if result.pbo.pbo != result.pbo.pbo else f"{result.pbo.pbo:.4f}"
    print(f"PBO                  {pbo_text}   (needs < {PBO_THRESHOLD}, over {result.pbo.n_combinations} splits)")
    print("=" * 64)

    dsr_ok = result.dsr > DSR_THRESHOLD
    print(f"  DSR {'PASS' if dsr_ok else 'FAIL'}")

    # PBO is a real gate as of 2026-08-01. Before that, `te.ml.train` fed
    # `probability_of_backtest_overfitting` the per-outer-FOLD return series
    # of a single config, which ranks time periods against each other rather
    # than competing strategies — so the number could not mean what PBO
    # means, and printing "PBO PASS" beside it would have been exactly the
    # unearned reassurance this project exists to refuse. Every config in
    # the grid is now scored on every outer test block, which is the shared
    # period grid over competing configurations CSCV is defined on.
    #
    # NaN still means NOT EVALUATED (fewer than 2 configs produced a usable
    # series) and is reported as such — never silently treated as a pass.
    pbo_evaluated = result.pbo.pbo == result.pbo.pbo  # False only for NaN
    pbo_ok = pbo_evaluated and result.pbo.pbo < PBO_THRESHOLD
    if not pbo_evaluated:
        print("  PBO NOT EVALUATED — fewer than 2 configurations produced a usable")
        print("      out-of-sample series. This is not a pass.")
    else:
        print(f"  PBO {'PASS' if pbo_ok else 'FAIL'}")

    if dsr_ok and pbo_ok:
        print("\nDSR and PBO both passed. This does NOT promote the model — promotion")
        print("is a manual reviewed action (scripts/promote_model.py) and additionally")
        print("requires paper sessions recorded AFTER params_frozen_at.")
    else:
        print("\nGates not met, which is the expected outcome at this stage and is the")
        print("machinery working. The model stays at `shadow`, where it cannot")
        print("change any order, size or skip reason. Do NOT lower the threshold.")

    if result.feature_importances_per_outer_fold:
        print("\nfeature importance per outer fold (drift here means instability):")
        for column in SECONDARY_V1.columns:
            per_fold = [f"{fold.get(column, 0.0):.3f}" for fold in result.feature_importances_per_outer_fold]
            print(f"  {column:22s} {'  '.join(per_fold)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
