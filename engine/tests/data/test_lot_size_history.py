"""Historical lot sizes, versioned like charge rates.

NIFTY's lot size changed twice inside the span of the option archive: 25 to
75 on 2024-11-20 (SEBI raised the minimum contract value to Rs 15 lakh), and
75 to 65 from the January 2026 series. Labelling a 2024 firing on today's 65
overstates cost-per-unit by 15% — and nothing downstream reveals it, because
a cost-adjusted barrier that moved a few percent produces a perfectly
plausible-looking win rate.

The values here were cross-checked against SEBI's own minimum-contract-value
bands rather than trusted from a single source, because the secondary
reporting disagreed on the dates. See `config/lot_sizes.yaml`'s header for
that arithmetic.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.data.lot_size_history import UnknownLotSizeError, load_lot_size_history, lot_size_on

_PATH = Path(__file__).resolve().parents[2] / "config" / "lot_sizes.yaml"


@pytest.fixture(scope="module")
def history():  # noqa: ANN201
    return load_lot_size_history(_PATH)


@pytest.mark.parametrize(
    ("on", "expected"),
    [
        (dt.date(2024, 1, 4), 25),  # first day of the option archive
        (dt.date(2024, 11, 19), 25),  # day before the change
        (dt.date(2024, 11, 20), 75),  # SEBI's Rs 15 lakh contract-value floor
        (dt.date(2025, 12, 31), 75),
        (dt.date(2026, 1, 1), 65),  # January 2026 series
        (dt.date(2026, 5, 5), 65),  # last day of the archive
    ],
)
def test_the_lot_size_in_force_on_each_date(history, on, expected) -> None:  # noqa: ANN001
    assert lot_size_on(history, "NIFTY", on) == expected


def test_todays_size_matches_what_the_live_engine_trades(history) -> None:
    """65 is what the broker's `optionsymbol` service returns and what
    `Settings.paper_cycle_lot_size` seeds. If this table disagreed with the
    live path, a backtest and a live trade would price the same contract
    differently."""
    assert lot_size_on(history, "NIFTY", dt.date.today()) == 65


def test_an_unknown_symbol_is_refused_not_defaulted(history) -> None:
    """BANKNIFTY/SENSEX/BANKEX are deliberately absent: the archive barely
    covers them and the secondary sources disagreed on BANKNIFTY's pre-2026
    sizes. Refusing is the point — an invented row would silently misprice
    every label it touched."""
    with pytest.raises(UnknownLotSizeError, match="no historical lot size on file"):
        lot_size_on(history, "BANKNIFTY", dt.date(2024, 6, 1))


def test_a_date_before_the_earliest_row_is_refused(history) -> None:
    """Same discipline as the charge table: no falling back to the oldest
    known size, which would be a guess wearing a number's clothes."""
    with pytest.raises(UnknownLotSizeError, match="no lot size on file for"):
        lot_size_on(history, "NIFTY", dt.date(2023, 6, 1))


def test_rows_are_sorted_and_every_one_carries_provenance(history) -> None:
    for symbol, rows in history.items():
        assert rows == sorted(rows, key=lambda r: r.effective_from), symbol
        for row in rows:
            assert row.verified_at is not None, f"{symbol} {row.effective_from} has no verified_at"
            assert row.lot_size > 0


def test_the_notional_of_every_row_sits_inside_sebis_band(history) -> None:
    """The cross-check that caught the disagreement in the first place. SEBI
    set a Rs 5-10 lakh minimum contract value before Nov 2024 and Rs 15 lakh
    after; a lot size that puts NIFTY's notional far outside its era's band
    is the wrong number regardless of which blog published it.

    Spot levels are the approximate NIFTY level at each row's start date —
    used only to sanity-check an order of magnitude, not as market data."""
    approx_spot = {
        dt.date(2024, 1, 1): 21_700,
        dt.date(2024, 11, 20): 23_500,
        dt.date(2026, 1, 1): 24_300,
    }
    floors = {dt.date(2024, 1, 1): 500_000, dt.date(2024, 11, 20): 1_500_000, dt.date(2026, 1, 1): 1_500_000}

    for row in history["NIFTY"]:
        spot = approx_spot[row.effective_from]
        notional = spot * row.lot_size
        assert notional >= floors[row.effective_from], (
            f"NIFTY lot {row.lot_size} from {row.effective_from} gives a notional of "
            f"Rs {notional:,}, below the Rs {floors[row.effective_from]:,} floor of that era"
        )
