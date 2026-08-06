"""The charge-rate table must cover the whole span of data we hold, and it
must pick the rate that was ACTUALLY in force on each trade date.

This exists because the Shoonya option archive starts 2024-01-04, and until
2026-08-01 the table's earliest row was 2026-04-01 — so `select_rates` had
nothing to return for 2 years 3 months of real data and refused to price it
at all. That refusal was correct behaviour (guessing at costs is how a
backtest fabricates profit), but it meant 95% of the archive was unusable.

Rates below are the real published ones, verified 2026-08-01. Each is pinned
here rather than only in YAML so a careless edit to `config/charges.yaml`
fails a test instead of silently re-pricing every historical backtest.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostModel, select_rates
from te.domain.money import Paise

_CHARGES_PATH = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"

#: First and last dates the Shoonya NIFTY option archive covers.
ARCHIVE_START = dt.date(2024, 1, 4)
ARCHIVE_END = dt.date(2026, 5, 5)


@pytest.fixture(scope="module")
def table():  # noqa: ANN201
    return load_charge_rate_table(_CHARGES_PATH)


@pytest.mark.parametrize(
    ("on", "stt_sell", "stt_exercise", "nfo", "bfo"),
    [
        # Pre-uniform-fee regime. Slab-based exchange charge; 0.0495% is the
        # top of the band, i.e. the retail rate.
        (dt.date(2024, 1, 4), 6.25, 12.5, 4.95, 4.95),
        (dt.date(2024, 9, 30), 6.25, 12.5, 4.95, 4.95),
        # Finance (No. 2) Act 2024 STT rise + SEBI's uniform fee structure,
        # both on the same day.
        (dt.date(2024, 10, 1), 10.0, 12.5, 3.503, 3.25),
        (dt.date(2026, 2, 28), 10.0, 12.5, 3.503, 3.25),
        # NSE's 27 Feb 2026 circular: IPFT cut, transaction charge raised to
        # absorb it. BSE unaffected.
        (dt.date(2026, 3, 1), 10.0, 12.5, 3.553, 3.25),
        # Budget 2026.
        (dt.date(2026, 4, 1), 15.0, 15.0, 3.553, 3.25),
        (dt.date(2026, 5, 5), 15.0, 15.0, 3.553, 3.25),
    ],
)
def test_the_rate_in_force_on_each_date(table, on, stt_sell, stt_exercise, nfo, bfo) -> None:  # noqa: ANN001
    rates = select_rates(table, on)
    assert float(rates.stt_sell_bps) == stt_sell
    assert float(rates.stt_exercise_intrinsic_bps) == stt_exercise
    assert float(rates.exchange_txn_bps["NFO"]) == nfo
    assert float(rates.exchange_txn_bps["BFO"]) == bfo


def test_every_day_of_the_option_archive_can_be_priced(table) -> None:
    """The whole point. Walks every calendar day the archive covers and
    asserts a rate exists — a single uncovered day would silently drop that
    day's firings from any dataset built over the span."""
    day = ARCHIVE_START
    while day <= ARCHIVE_END:
        assert select_rates(table, day) is not None, f"no charge rate covers {day}"
        day += dt.timedelta(days=1)


def test_rates_that_did_not_change_are_identical_across_every_row(table) -> None:
    """Brokerage, SEBI fee, GST and stamp duty were unchanged over this whole
    window. Pinning that means a typo in one row shows up here rather than as
    an unexplained cost step in a backtest."""
    for on in (ARCHIVE_START, dt.date(2024, 10, 1), dt.date(2026, 3, 1), dt.date(2026, 4, 1)):
        rates = select_rates(table, on)
        assert int(rates.brokerage_per_executed_order_paise) == 2000, on
        assert float(rates.sebi_bps) == 0.01, on  # Rs 10 per crore
        assert float(rates.gst_pct) == 18.0, on
        assert float(rates.stamp_buy_bps) == 0.3, on  # 0.003% buy side


def test_costs_rise_monotonically_across_the_four_regimes(table) -> None:
    """Every rate change in this window made an options round trip more
    expensive, not less — even 1 Oct 2024, where a fee CUT was more than
    offset by the STT rise. If a future edit breaks this ordering it is
    either a genuine new regime (fine, update the test with the source) or a
    transcription error (not fine)."""
    totals = []
    for on in (dt.date(2024, 1, 4), dt.date(2024, 10, 1), dt.date(2026, 3, 1), dt.date(2026, 4, 1)):
        model = CostModel(select_rates(table, on))
        breakdown = model.round_trip(
            entry_premium=Paise(10_000), exit_premium=Paise(12_000), qty=65, exchange="NFO", on=on
        )
        totals.append(int(breakdown.total))

    assert totals == sorted(totals), f"round-trip cost did not rise monotonically: {totals}"
    assert totals[-1] == 6511, "the current regime must still match the plan's golden worked example"


def test_a_date_before_the_earliest_row_is_refused_not_guessed(table) -> None:
    """Pricing a trade at a rate nobody verified is how a backtest invents
    profit. Before the first row there is no answer, and the honest response
    is to fail loudly rather than reuse the oldest known rate."""
    with pytest.raises(Exception):  # noqa: B017 — the loader's own error type, whatever it raises
        select_rates(table, dt.date(2023, 12, 31))


def test_adding_a_future_rate_row_needs_no_code_change(table) -> None:
    """The scalability requirement, stated as a test: a new regime is one
    YAML row. This builds a table in memory with a hypothetical future row
    and proves `select_rates` picks it up with nothing else touched."""
    from dataclasses import replace

    future = replace(select_rates(table, dt.date(2026, 4, 1)), effective_from=dt.date(2027, 1, 1))
    extended = [*table, future]

    assert select_rates(extended, dt.date(2026, 12, 31)).effective_from == dt.date(2026, 4, 1)
    assert select_rates(extended, dt.date(2027, 1, 1)).effective_from == dt.date(2027, 1, 1)


def test_one_cost_model_can_price_every_regime(table) -> None:  # noqa: ANN001
    """A backtest walks years and must price each trade at the rates that
    were really in force on its own date.

    Before 2026-08-01 `CostModel` held a single row and raised on anything
    earlier, so a two-year label run died on its first 2024 firing. Handing
    it the whole table makes every method regime-aware at once — they all
    already take `on: date`."""
    model = CostModel(table)
    costs = {
        on: int(
            model.round_trip(
                entry_premium=Paise(10_000), exit_premium=Paise(12_000), qty=65, exchange="NFO", on=on
            ).total
        )
        for on in (dt.date(2024, 1, 4), dt.date(2024, 10, 1), dt.date(2026, 3, 1), dt.date(2026, 4, 1))
    }

    assert len(set(costs.values())) == 4, f"a table-backed model priced regimes identically: {costs}"
    assert costs[dt.date(2026, 4, 1)] == 6511, "current regime must still match the golden example"


def test_a_single_row_model_still_refuses_dates_it_cannot_price(table) -> None:  # noqa: ANN001
    """The single-row form is kept for the LIVE path on purpose. A live
    cycle knows today's date, and letting it silently look up an older row
    would hide a stale-rate bug behind an automatic fallback."""
    model = CostModel(select_rates(table, dt.date(2026, 4, 1)))
    with pytest.raises(ValueError, match="before this CostModel's rates became effective"):
        model.round_trip(
            entry_premium=Paise(10_000),
            exit_premium=Paise(12_000),
            qty=65,
            exchange="NFO",
            on=dt.date(2024, 1, 4),
        )
