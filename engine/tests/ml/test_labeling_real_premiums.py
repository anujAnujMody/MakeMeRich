"""Labelling a firing on the REAL option premium path instead of the
delta-converted index approximation.

The approximation these tests displace has no theta and one day's IV
snapshot, so it is optimistically biased in a direction nothing downstream
can see. The rules asserted here are what keep the replacement honest:

* a real-premium label and an approximated one are never silently mixed —
  every `LabeledFiring` says which it is;
* the fallback fires on missing/stale contract data rather than inventing a
  premium;
* BOTH directions are LONG the premium on the option's own path (a bought PE
  gains when the index falls), which is the exact inversion the index path
  needs and this one must not apply.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.data.barstore import BAR_COLUMNS, BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel, select_rates
from te.domain.money import Paise
from te.ml.labeling import label_one_firing_on_premium

_CHARGES_PATH = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
EXCHANGE = "NFO"
LOT = 65
ENTRY_TS = dt.datetime(2026, 5, 4, 9, 30, tzinfo=IST)
CE = "NIFTY05MAY2624500CE"
PE = "NIFTY05MAY2624500PE"


@pytest.fixture
def cost_model() -> CostModel:
    return CostModel(select_rates(load_charge_rate_table(_CHARGES_PATH), dt.date(2026, 5, 4)))


@pytest.fixture
def store(tmp_path: Path) -> BarStore:
    return BarStore(tmp_path / "bars")


def _write(store: BarStore, symbol: str, bars: list[tuple[dt.datetime, float, float, float]]) -> None:
    rows = [
        {
            "symbol": symbol,
            "exchange": EXCHANGE,
            "event_ts": ts,
            "interval": "1m",
            "o": close,
            "h": high,
            "l": low,
            "c": close,
            "v": 1_000,
            "oi": 0,
            "ingested_at": ts + dt.timedelta(minutes=1),
            "source": "shoonya-archive",
        }
        for ts, high, low, close in bars
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))


def _flat(symbol: str, store: BarStore, *, premium: float, minutes: int = 30) -> None:
    start = ENTRY_TS - dt.timedelta(minutes=1)
    _write(store, symbol, [(start + dt.timedelta(minutes=i), premium, premium, premium) for i in range(minutes)])


def test_target_touched_on_the_option_path_labels_a_win(store: BarStore, cost_model: CostModel) -> None:
    _flat(CE, store, premium=100.0)
    _write(store, CE, [(ENTRY_TS + dt.timedelta(minutes=5), 130.0, 99.0, 129.0)])

    result = label_one_firing_on_premium(
        store=store,
        option_symbol=CE,
        entry_ts=ENTRY_TS,
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        max_hold=dt.timedelta(hours=3),
        cost_model=cost_model,
        exchange=EXCHANGE,
        lot_size=LOT,
    )
    assert result is not None
    assert result.entry_premium == Paise(10_000)
    assert (result.label, result.barrier) == (1, "target")
    assert result.resolved_at == ENTRY_TS + dt.timedelta(minutes=5)


def test_target_is_net_of_real_costs_not_gross(store: BarStore, cost_model: CostModel) -> None:
    """A high that clears the GROSS +20% but not the cost-adjusted level must
    not count as a win — that is the whole reason costs enter the label."""
    _flat(CE, store, premium=100.0)
    _write(store, CE, [(ENTRY_TS + dt.timedelta(minutes=5), 120.0, 99.0, 120.0)])

    result = label_one_firing_on_premium(
        store=store,
        option_symbol=CE,
        entry_ts=ENTRY_TS,
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        max_hold=dt.timedelta(hours=3),
        cost_model=cost_model,
        exchange=EXCHANGE,
        lot_size=LOT,
    )
    assert result is not None
    assert result.barrier == "time"


def test_a_bought_put_wins_when_its_own_premium_rises(store: BarStore, cost_model: CostModel) -> None:
    """The index path inverts long_put (the underlying must FALL). On the
    option's own path there is nothing to invert — the PE was BOUGHT, so its
    premium rising is the win. Applying the index inversion here would label
    every put firing backwards."""
    _flat(PE, store, premium=100.0)
    _write(store, PE, [(ENTRY_TS + dt.timedelta(minutes=5), 140.0, 99.0, 139.0)])

    result = label_one_firing_on_premium(
        store=store,
        option_symbol=PE,
        entry_ts=ENTRY_TS,
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        max_hold=dt.timedelta(hours=3),
        cost_model=cost_model,
        exchange=EXCHANGE,
        lot_size=LOT,
    )
    assert result is not None
    assert (result.label, result.barrier) == (1, "target")


def test_stop_touched_labels_a_loss(store: BarStore, cost_model: CostModel) -> None:
    _flat(CE, store, premium=100.0)
    _write(store, CE, [(ENTRY_TS + dt.timedelta(minutes=3), 101.0, 79.0, 80.0)])

    result = label_one_firing_on_premium(
        store=store,
        option_symbol=CE,
        entry_ts=ENTRY_TS,
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        max_hold=dt.timedelta(hours=3),
        cost_model=cost_model,
        exchange=EXCHANGE,
        lot_size=LOT,
    )
    assert result is not None
    assert (result.label, result.barrier) == (0, "stop")


def test_theta_decay_alone_can_hit_the_stop(store: BarStore, cost_model: CostModel) -> None:
    """The capability the index approximation structurally cannot express: a
    premium that bleeds to the stop with the index unchanged."""
    bars = [(ENTRY_TS - dt.timedelta(minutes=1), 100.0, 100.0, 100.0)]
    bars += [(ENTRY_TS + dt.timedelta(minutes=i), 100.0 - i, 100.0 - i, 100.0 - i) for i in range(1, 40)]
    _write(store, CE, bars)

    result = label_one_firing_on_premium(
        store=store,
        option_symbol=CE,
        entry_ts=ENTRY_TS,
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        max_hold=dt.timedelta(hours=3),
        cost_model=cost_model,
        exchange=EXCHANGE,
        lot_size=LOT,
    )
    assert result is not None
    assert (result.label, result.barrier) == (0, "stop")
    assert result.resolved_at == ENTRY_TS + dt.timedelta(minutes=20)


def test_no_bars_for_the_contract_returns_none_rather_than_guessing(store: BarStore, cost_model: CostModel) -> None:
    result = label_one_firing_on_premium(
        store=store,
        option_symbol=CE,
        entry_ts=ENTRY_TS,
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        max_hold=dt.timedelta(hours=3),
        cost_model=cost_model,
        exchange=EXCHANGE,
        lot_size=LOT,
    )
    assert result is None


def test_a_stale_last_bar_is_not_used_as_an_entry_premium(store: BarStore, cost_model: CostModel) -> None:
    """An illiquid strike's last print can be an hour old. Using it as the
    entry premium would silently label a firing against a price nobody could
    have traded."""
    _write(store, CE, [(ENTRY_TS - dt.timedelta(minutes=45), 100.0, 100.0, 100.0)])

    result = label_one_firing_on_premium(
        store=store,
        option_symbol=CE,
        entry_ts=ENTRY_TS,
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        max_hold=dt.timedelta(hours=3),
        cost_model=cost_model,
        exchange=EXCHANGE,
        lot_size=LOT,
        max_staleness=dt.timedelta(minutes=5),
    )
    assert result is None


def test_a_worthless_contract_is_not_labelled(store: BarStore, cost_model: CostModel) -> None:
    """A 0.05 expiry-day tail quote gives percentage barriers below one
    paise; the label would be noise dressed as data."""
    _flat(CE, store, premium=0.0)
    result = label_one_firing_on_premium(
        store=store,
        option_symbol=CE,
        entry_ts=ENTRY_TS,
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        max_hold=dt.timedelta(hours=3),
        cost_model=cost_model,
        exchange=EXCHANGE,
        lot_size=LOT,
    )
    assert result is None


def test_the_labeller_cannot_see_a_bar_after_the_hold_horizon(store: BarStore, cost_model: CostModel) -> None:
    _flat(CE, store, premium=100.0)
    _write(store, CE, [(ENTRY_TS + dt.timedelta(hours=4), 200.0, 200.0, 200.0)])

    result = label_one_firing_on_premium(
        store=store,
        option_symbol=CE,
        entry_ts=ENTRY_TS,
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        max_hold=dt.timedelta(hours=3),
        cost_model=cost_model,
        exchange=EXCHANGE,
        lot_size=LOT,
    )
    assert result is not None
    assert (result.label, result.barrier) == (0, "time")
    assert result.resolved_at == ENTRY_TS + dt.timedelta(hours=3)


class TestContractResolution:
    """`OptionContractIndex` resolves a firing to a contract that ACTUALLY
    has bars, never to one the current expiry calendar merely implies."""

    @pytest.fixture
    def populated(self, store: BarStore) -> OptionContractIndex:
        for symbol in ("NIFTY05MAY2624400CE", "NIFTY05MAY2624500CE", "NIFTY05MAY2624500PE", "NIFTY12MAY2624500CE"):
            _flat(symbol, store, premium=100.0)
        return OptionContractIndex(store, "NIFTY")

    def test_picks_nearest_strike_of_the_nearest_expiry(self, populated: OptionContractIndex) -> None:
        picked = populated.nearest(on=dt.date(2026, 5, 4), index_level=Decimal("24478.5"), option_type="CE")
        assert picked is not None
        assert picked.symbol == "NIFTY05MAY2624500CE"

    def test_rolls_to_the_next_available_expiry_once_the_nearest_has_passed(
        self, populated: OptionContractIndex
    ) -> None:
        picked = populated.nearest(on=dt.date(2026, 5, 6), index_level=Decimal(24500), option_type="CE")
        assert picked is not None
        assert picked.expiry == dt.date(2026, 5, 12)

    def test_option_type_is_respected(self, populated: OptionContractIndex) -> None:
        picked = populated.nearest(on=dt.date(2026, 5, 4), index_level=Decimal(24500), option_type="PE")
        assert picked is not None
        assert picked.symbol == "NIFTY05MAY2624500PE"

    def test_returns_none_when_nothing_is_loaded_for_that_date(self, populated: OptionContractIndex) -> None:
        assert populated.nearest(on=dt.date(2026, 6, 1), index_level=Decimal(24500), option_type="CE") is None

    def test_a_coverage_gap_is_not_papered_over_by_a_further_expiry(self, store: BarStore) -> None:
        """Only the following week is loaded. A firing on 2026-05-04 would
        have traded the 05-05 weekly; answering with the 05-12 contract
        substitutes a different theta profile and calls it the same trade."""
        _flat("NIFTY12MAY2624500CE", store, premium=100.0)
        index = OptionContractIndex(store, "NIFTY")
        assert index.nearest(on=dt.date(2026, 5, 4), index_level=Decimal(24500), option_type="CE") is None

    def test_a_monthly_only_underlying_can_widen_the_roll_window(self, store: BarStore) -> None:
        """BANKNIFTY has no weekly expiry, so its nearest contract is
        legitimately weeks out — the 7-day default would reject every one."""
        _flat("BANKNIFTY26MAY2655000CE", store, premium=100.0)
        index = OptionContractIndex(store, "BANKNIFTY")
        picked = index.nearest(
            on=dt.date(2026, 5, 4), index_level=Decimal(55000), option_type="CE", max_days_to_expiry=31
        )
        assert picked is not None and picked.symbol == "BANKNIFTY26MAY2655000CE"


# ---------------------------------------------------------------------------
# label_firings_from_evaluations — real and approximated labels, never mixed
# silently
# ---------------------------------------------------------------------------

INDEX = "NIFTY"


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    from te.persistence.db import make_engine, make_session_factory
    from te.persistence.models import Base

    engine = make_engine(f"sqlite:///{tmp_path / 'real_premium_labels.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _seed(session_factory, timestamps: list[dt.datetime]) -> None:  # noqa: ANN001
    """One traded ORB evaluation per timestamp, each an UPSIDE breakout with
    the index at 24,478.50 — so the resolved contract is the 24500 CE."""
    from te.domain.evaluation import ConditionResult, Evaluation
    from te.ml.labeling import _BREAKOUT_CONDITION_LABEL
    from te.persistence.repos.paper_trading import record_cycle, record_evaluation

    with session_factory() as session:
        cycle_id = record_cycle(session, ts=timestamps[0], mode="paper")
        for i, ts in enumerate(timestamps):
            record_evaluation(
                session,
                cycle_id=cycle_id,
                evaluation=Evaluation(
                    id=f"eval-{i}",
                    timestamp=ts,
                    strategy="orb",
                    instrument=INDEX,
                    verdict="traded",
                    reason="breakout confirmed",
                    conditions=(
                        ConditionResult(
                            label=_BREAKOUT_CONDITION_LABEL,
                            required="close > range high",
                            actual="close=24478.50, range=[24400.00, 24450.00]",
                            passed=True,
                            evaluated=True,
                        ),
                    ),
                ),
            )
        session.commit()


def _index_bars(store: BarStore, timestamps: list[dt.datetime]) -> None:
    _write(
        store,
        INDEX,
        [(ts + dt.timedelta(minutes=i), 24_478.5, 24_478.5, 24_478.5) for ts in timestamps for i in range(1, 15)],
    )


def _real_config(store: BarStore):  # noqa: ANN201
    from te.ml.labeling import RealPremiumConfig

    return RealPremiumConfig(
        contracts=OptionContractIndex(store, INDEX),
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        # A constant resolver: these tests do not span a lot-size change.
        # Production passes `te.data.lot_size_history.lot_size_on`, which
        # returns the size actually in force on each firing's own date —
        # NIFTY's changed twice inside the option archive's span.
        lot_size_for=lambda _on: LOT,
    )


def _label(session_factory, store: BarStore, cost_model: CostModel, real):  # noqa: ANN001, ANN201
    from te.ml.labeling import label_firings_from_evaluations

    return label_firings_from_evaluations(
        session_factory,
        store,
        cost_model,
        strategy="orb",
        exchange=EXCHANGE,
        stop_distance=Paise(7_390),
        target_distance=Paise(7_390),
        max_hold=dt.timedelta(hours=3),
        instrument=INDEX,
        real_premiums=real,
    )


def test_firing_labelled_on_the_real_contract_is_marked_as_such(
    session_factory,  # noqa: ANN001
    store: BarStore,
    cost_model: CostModel,
) -> None:
    timestamps = [ENTRY_TS]
    _seed(session_factory, timestamps)
    _index_bars(store, timestamps)
    _flat(CE, store, premium=100.0)
    _write(store, CE, [(ENTRY_TS + dt.timedelta(minutes=6), 130.0, 99.0, 129.0)])

    firings = _label(session_factory, store, cost_model, _real_config(store))

    assert len(firings) == 1
    firing = firings[0]
    assert firing.source == "real_option"
    assert firing.option_symbol == CE
    # The OPTION's premium, not the index level x100 the approximation uses.
    assert firing.entry_premium == Paise(10_000)
    assert (firing.label, firing.barrier) == (1, "target")


def test_falls_back_to_the_index_approximation_when_the_contract_is_missing(
    session_factory,  # noqa: ANN001
    store: BarStore,
    cost_model: CostModel,
) -> None:
    """No option bars loaded for that day: the firing is still labelled, but
    it says so. Dropping it would bias the sample toward the days the
    archive happens to cover."""
    timestamps = [ENTRY_TS]
    _seed(session_factory, timestamps)
    _index_bars(store, timestamps)
    _flat("NIFTY12MAY2624500CE", store, premium=100.0)  # wrong week — not usable

    firings = _label(session_factory, store, cost_model, _real_config(store))

    assert len(firings) == 1
    assert firings[0].source == "index_approx"
    assert firings[0].option_symbol is None
    assert firings[0].entry_premium == Paise(2_447_850)


def test_without_a_real_premium_config_every_label_is_approximated(
    session_factory,  # noqa: ANN001
    store: BarStore,
    cost_model: CostModel,
) -> None:
    """The pre-existing call sites (BANKNIFTY/SENSEX, which the archive
    barely covers) must keep working unchanged — and must not be able to
    claim a real-premium provenance they never had."""
    timestamps = [ENTRY_TS]
    _seed(session_factory, timestamps)
    _index_bars(store, timestamps)
    _flat(CE, store, premium=100.0)

    firings = _label(session_factory, store, cost_model, None)

    assert [f.source for f in firings] == ["index_approx"]


def test_a_mixed_run_reports_both_sources_separately(
    session_factory,  # noqa: ANN001
    store: BarStore,
    cost_model: CostModel,
) -> None:
    """The dataset-poisoning case: one run covering a day the archive has and
    a day it does not. Both firings survive; each carries its own
    provenance, so a caller can refuse to pool them."""
    covered, uncovered = ENTRY_TS, ENTRY_TS + dt.timedelta(days=30)
    _seed(session_factory, [covered, uncovered])
    _index_bars(store, [covered, uncovered])
    _flat(CE, store, premium=100.0)

    firings = _label(session_factory, store, cost_model, _real_config(store))

    assert sorted(f.source for f in firings) == ["index_approx", "real_option"]
