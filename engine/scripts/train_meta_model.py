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

import pandas as pd

from scripts.label_replay_firings import (
    ATM_PARAMS,
    MAX_HOLD,
    RATES_VERIFIED_FROM,
    index_barriers,
    round_trip_cost_in_index_points,
)
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostModel, select_rates
from te.ml.dataset import build_training_set
from te.ml.featurespec import SECONDARY_V1
from te.ml.labeling import label_firings_from_evaluations
from te.ml.train import train_meta_model
from te.ml.trials import TrialLedger
from te.persistence.db import make_engine, make_session_factory
from te.settings import Settings

#: DSR must EXCEED this for the model to be considered to have an
#: out-of-sample edge; PBO must fall BELOW its threshold. Both from the plan.
DSR_THRESHOLD = 0.95
PBO_THRESHOLD = 0.05


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instruments", default="NIFTY,SENSEX")
    parser.add_argument("--run-id", default=None, help="defaults to a timestamp-free label; pass one for traceability")
    args = parser.parse_args()

    chosen = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in ATM_PARAMS]
    if unknown:
        print(f"no measured ATM params for: {', '.join(unknown)}", file=sys.stderr)
        return 2

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    cost_model = CostModel(select_rates(load_charge_rate_table(settings.charges_path), dt.date.today()))

    print(f"spec {SECONDARY_V1.name} v{SECONDARY_V1.version}: {', '.join(SECONDARY_V1.columns)}\n")

    rows: list[dict[str, object]] = []
    for symbol in chosen:
        stop, target, _, _ = index_barriers(symbol)
        firings = label_firings_from_evaluations(
            session_factory,
            store,
            cost_model,
            strategy="orb",
            exchange=ATM_PARAMS[symbol][4],
            stop_distance=stop,
            target_distance=target,
            max_hold=MAX_HOLD,
            instrument=symbol,
            since=RATES_VERIFIED_FROM,
            cost_per_unit=round_trip_cost_in_index_points(symbol, cost_model, RATES_VERIFIED_FROM),
        )
        print(f"{symbol:10s} {len(firings):>5,} labelled firings")
        for firing in firings:
            features = build_training_set(
                firing.entry_ts, SECONDARY_V1, store, None, instrument=firing.instrument
            )
            rows.append(
                {
                    **{c: features[c] for c in SECONDARY_V1.columns},
                    "_label": firing.label,
                    "_entry_ts": firing.entry_ts,
                    "_exit_ts": firing.exit_ts,
                    "_weight": firing.weight,
                }
            )

    if not rows:
        print("no labelled firings — nothing to train on", file=sys.stderr)
        return 1

    frame = pd.DataFrame(rows)
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
        )
    except ValueError as exc:
        # `validate_training_set` raises rather than warns — a dataset it
        # rejects cannot produce a trustworthy model, so refusing to train is
        # the correct outcome, not an error to work around.
        print(f"REFUSED TO TRAIN: {exc}", file=sys.stderr)
        return 1

    print("=" * 64)
    print(f"samples              {result.n_labeled_samples:,}")
    print(f"trials before / now  {trials_before:,} / {result.n_trials_at_training:,}")
    print(f"DSR                  {result.dsr:.4f}   (needs > {DSR_THRESHOLD})")
    print(f"PBO                  {result.pbo.pbo:.4f}   (not a real PBO here - see below)")
    print("=" * 64)

    dsr_ok = result.dsr > DSR_THRESHOLD
    print(f"  DSR {'PASS' if dsr_ok else 'FAIL'}")

    # PBO is deliberately NOT reported as pass/fail. `train_meta_model` feeds
    # `probability_of_backtest_overfitting` the per-outer-fold return series
    # as its "trials" matrix, and says so: "a structural exercise of the PBO
    # machinery over genuine nested-CV outputs, not (yet) a full
    # multi-strategy backtest grid". CSCV is defined over COMPETING STRATEGY
    # CONFIGURATIONS ranked in-sample against out-of-sample; ranking outer
    # folds against each other answers a different question. A low number
    # here is therefore not evidence of no overfitting, and printing "PBO
    # PASS" beside it would be exactly the kind of unearned reassurance this
    # project exists to refuse.
    print("\n  PBO: NOT EVALUATED as a gate. The CSCV matrix here is built from")
    print("  per-outer-fold return series, not from competing strategy configs,")
    print("  so the number does not mean what PBO means. A real PBO needs a")
    print("  multi-config backtest grid over a shared time index.")

    if dsr_ok:
        print("\nDSR passed. This does NOT promote the model — promotion is a manual")
        print("reviewed action (scripts/promote_model.py), additionally requires a")
        print("genuine PBO, and requires paper sessions recorded AFTER")
        print("params_frozen_at.")
    else:
        print("\nDSR not met, which is the expected outcome at this stage and is the")
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
