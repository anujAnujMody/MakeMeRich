"""`te.ml.labeling` — triple-barrier labels from ORB firings, NET of real
costs. `test_label_uses_net_target_not_gross` is the load-bearing test named
explicitly in the plan."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from te.data.barstore import BAR_COLUMNS, BarStore
from te.data.charges_loader import load_charge_rate_table
from te.domain.clock import IST
from te.domain.costs import CostModel, select_rates
from te.domain.money import Paise
from te.ml.labeling import (
    compute_uniqueness_weights,
    extract_hypothetical_entry_premium,
    label_one_firing,
)

_CHARGES_PATH = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
INSTRUMENT = "NIFTY30JUN2626500CE"
EXCHANGE = "NFO"
ON = dt.date(2026, 6, 1)


@pytest.fixture
def cost_model() -> CostModel:
    table = load_charge_rate_table(_CHARGES_PATH)
    return CostModel(select_rates(table, ON))


def _bar(event_ts: dt.datetime, *, h: float, low: float, c: float) -> dict[str, object]:
    return {
        "symbol": INSTRUMENT,
        "exchange": EXCHANGE,
        "event_ts": event_ts,
        "interval": "1m",
        "o": c,
        "h": h,
        "l": low,
        "c": c,
        "v": 1_000,
        "oi": 0,
        "ingested_at": event_ts,
        "source": "test",
    }


def _entry_ts() -> dt.datetime:
    return dt.datetime(2026, 6, 1, 9, 30, tzinfo=IST)


def test_extract_hypothetical_entry_premium_parses_orbs_actual_string() -> None:
    assert extract_hypothetical_entry_premium("close=36.00, range=[28.00, 32.00]") == Paise(3600)


def test_extract_hypothetical_entry_premium_raises_on_unparseable_input() -> None:
    with pytest.raises(ValueError, match="could not extract"):
        extract_hypothetical_entry_premium("not reached")


def test_label_uses_net_target_not_gross(cost_model: CostModel, tmp_path: Path) -> None:
    """A firing whose price path touches the GROSS target but not the NET
    (cost-adjusted) target must label 0, not 1 — the model must never learn
    to predict a gross win that real costs would erase."""
    store = BarStore(tmp_path / "bars")
    entry_ts = _entry_ts()
    entry_premium = Paise(3000)  # ₹30.00
    stop_distance = Paise(500)  # stop at ₹25.00
    target_distance = Paise(200)  # gross target ₹32.00

    # Confirm costs are non-zero for this notional so net_target > gross_target.
    gross_target = Paise(entry_premium + target_distance)
    cost_per_unit = cost_model.round_trip(
        entry_premium=entry_premium, exit_premium=gross_target, qty=1, exchange=EXCHANGE, on=entry_ts.date()
    ).total
    assert cost_per_unit > 0

    # Price touches exactly the gross target (₹32.00) but never the net
    # target (gross + cost_per_unit) and never the stop.
    bars = pd.DataFrame(
        [_bar(entry_ts + dt.timedelta(minutes=1), h=32.00, low=29.50, c=31.50)],
        columns=list(BAR_COLUMNS),
    )
    store.append(bars)

    label, barrier, _resolved_at = label_one_firing(
        store=store,
        instrument=INSTRUMENT,
        entry_ts=entry_ts,
        entry_premium=entry_premium,
        stop_distance=stop_distance,
        target_distance=target_distance,
        max_hold=dt.timedelta(minutes=5),
        cost_model=cost_model,
        exchange=EXCHANGE,
    )

    assert label == 0
    assert barrier == "time"


def test_label_is_1_when_net_target_is_touched(cost_model: CostModel, tmp_path: Path) -> None:
    store = BarStore(tmp_path / "bars")
    entry_ts = _entry_ts()
    entry_premium = Paise(3000)
    stop_distance = Paise(500)
    target_distance = Paise(200)

    gross_target = Paise(entry_premium + target_distance)
    cost_per_unit = cost_model.round_trip(
        entry_premium=entry_premium, exit_premium=gross_target, qty=1, exchange=EXCHANGE, on=entry_ts.date()
    ).total
    net_target_rupees = (entry_premium + target_distance + cost_per_unit) / 100

    bars = pd.DataFrame(
        [_bar(entry_ts + dt.timedelta(minutes=1), h=net_target_rupees + 0.5, low=29.50, c=net_target_rupees + 0.4)],
        columns=list(BAR_COLUMNS),
    )
    store.append(bars)

    label, barrier, _ = label_one_firing(
        store=store,
        instrument=INSTRUMENT,
        entry_ts=entry_ts,
        entry_premium=entry_premium,
        stop_distance=stop_distance,
        target_distance=target_distance,
        max_hold=dt.timedelta(minutes=5),
        cost_model=cost_model,
        exchange=EXCHANGE,
    )
    assert label == 1
    assert barrier == "target"


def test_label_is_0_when_stop_is_touched_first(cost_model: CostModel, tmp_path: Path) -> None:
    store = BarStore(tmp_path / "bars")
    entry_ts = _entry_ts()
    entry_premium = Paise(3000)
    stop_distance = Paise(500)  # stop at ₹25.00
    target_distance = Paise(200)

    bars = pd.DataFrame(
        [_bar(entry_ts + dt.timedelta(minutes=1), h=30.10, low=24.90, c=25.00)],
        columns=list(BAR_COLUMNS),
    )
    store.append(bars)

    label, barrier, _ = label_one_firing(
        store=store,
        instrument=INSTRUMENT,
        entry_ts=entry_ts,
        entry_premium=entry_premium,
        stop_distance=stop_distance,
        target_distance=target_distance,
        max_hold=dt.timedelta(minutes=5),
        cost_model=cost_model,
        exchange=EXCHANGE,
    )
    assert label == 0
    assert barrier == "stop"


def test_compute_uniqueness_weights_downweights_overlapping_labels() -> None:
    t0 = dt.datetime(2026, 6, 1, 9, 30, tzinfo=IST)
    # Two labels overlapping in full, one disjoint.
    spans = [
        (t0, t0 + dt.timedelta(minutes=10)),
        (t0, t0 + dt.timedelta(minutes=10)),
        (t0 + dt.timedelta(minutes=30), t0 + dt.timedelta(minutes=40)),
    ]
    weights = compute_uniqueness_weights(spans)
    assert weights[0] == pytest.approx(0.5, rel=1e-6)
    assert weights[1] == pytest.approx(0.5, rel=1e-6)
    assert weights[2] == pytest.approx(1.0, rel=1e-6)


def test_compute_uniqueness_weights_empty_input() -> None:
    assert compute_uniqueness_weights([]) == []


# ---------------------------------------------------------------------------
# label_firings_from_evaluations — the DB-driven entry point
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    from te.persistence.db import make_engine, make_session_factory
    from te.persistence.models import Base

    engine = make_engine(f"sqlite:///{tmp_path / 'labeling_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _seed_firings(session_factory, n: int) -> list[dt.datetime]:  # noqa: ANN001
    """`n` traded ORB evaluations, one minute apart, each carrying the
    breakout condition `label_firings_from_evaluations` keys off."""
    from te.domain.evaluation import ConditionResult, Evaluation
    from te.ml.labeling import _BREAKOUT_CONDITION_LABEL
    from te.persistence.repos.paper_trading import record_cycle, record_evaluation

    timestamps = [_entry_ts() + dt.timedelta(minutes=i) for i in range(n)]
    with session_factory() as session:
        cycle_id = record_cycle(session, ts=_entry_ts(), mode="paper")
        for i, ts in enumerate(timestamps):
            record_evaluation(
                session,
                cycle_id=cycle_id,
                evaluation=Evaluation(
                    id=f"eval-{i}",
                    timestamp=ts,
                    strategy="orb",
                    instrument=INSTRUMENT,
                    verdict="traded",
                    reason="breakout confirmed",
                    conditions=(
                        ConditionResult(
                            label="volume confirmation",
                            required=">= 1.5x",
                            actual="2.0x",
                            passed=True,
                            evaluated=True,
                        ),
                        ConditionResult(
                            label=_BREAKOUT_CONDITION_LABEL,
                            required="close > range high",
                            actual="close=30.00, range=[28.00, 29.00]",
                            passed=True,
                            evaluated=True,
                        ),
                    ),
                ),
            )
        session.commit()
    return timestamps


def test_label_firings_from_evaluations_labels_every_traded_firing(
    session_factory,  # noqa: ANN001
    cost_model: CostModel,
    tmp_path: Path,
) -> None:
    from te.ml.labeling import label_firings_from_evaluations

    timestamps = _seed_firings(session_factory, 5)

    store = BarStore(tmp_path / "bars")
    # Price runs up hard enough to clear the NET target (which, at qty=1,
    # carries the whole flat per-order brokerage) for every firing.
    store.append(
        pd.DataFrame(
            [_bar(timestamps[0] + dt.timedelta(minutes=i), h=500.0, low=29.9, c=499.0) for i in range(1, 20)],
            columns=list(BAR_COLUMNS),
        )
    )

    firings = label_firings_from_evaluations(
        session_factory,
        store,
        cost_model,
        strategy="orb",
        exchange=EXCHANGE,
        stop_distance=Paise(500),
        target_distance=Paise(200),
        max_hold=dt.timedelta(minutes=10),
    )

    assert len(firings) == len(timestamps)
    assert {f.evaluation_id for f in firings} == {f"eval-{i}" for i in range(5)}
    # Each firing is matched to ITS OWN condition row, not another's.
    assert all(f.entry_premium == Paise(3000) for f in firings)
    assert [f.label for f in firings] == [1, 1, 1, 1, 1]
    assert all(f.barrier == "target" for f in firings)


def test_label_firings_fetches_all_condition_rows_in_one_query(
    session_factory,  # noqa: ANN001
    cost_model: CostModel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-evaluation SELECT here is quadratic over the whole firing
    history — the conditions must be fetched in a single batched query."""
    import sqlalchemy as sa

    from te.persistence.models import EvaluationConditionRow

    _seed_firings(session_factory, 8)
    store = BarStore(tmp_path / "bars")

    condition_queries = 0
    real_execute = sa.orm.Session.execute

    def _counting_execute(self, statement, *args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal condition_queries
        if EvaluationConditionRow.__tablename__ in str(statement):
            condition_queries += 1
        return real_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(sa.orm.Session, "execute", _counting_execute)

    from te.ml.labeling import label_firings_from_evaluations

    label_firings_from_evaluations(
        session_factory,
        store,
        cost_model,
        strategy="orb",
        exchange=EXCHANGE,
        stop_distance=Paise(500),
        target_distance=Paise(200),
        max_hold=dt.timedelta(minutes=10),
    )

    assert condition_queries == 1, f"expected 1 batched conditions query, got {condition_queries}"
