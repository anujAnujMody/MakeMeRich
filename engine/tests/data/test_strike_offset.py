"""`OptionContractIndex.nearest(strikes_out_of_the_money=...)`.

Which strike gets bought was hardcoded to at-the-money in every measurement
this project ran until 2026-08-01. That is not a neutral default: a further
out-of-the-money strike is much cheaper per lot — which is exactly why it is
tempting on small capital — but the flat Rs 20 per order is a far bigger
FRACTION of a small premium, and the spread widens with distance. The
direction of that trade-off is an empirical question that could not be asked
while the parameter did not exist.

These tests pin the mechanics so the answer to that question is trustworthy:
the direction of "out of the money" is opposite for calls and puts, and
asking for a distance the archive cannot supply must be refused rather than
quietly clamped to the end of the chain.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from te.data.option_history import OptionContractIndex, ParsedOptionSymbol


def _index(strikes: list[int], *, expiry: dt.date) -> OptionContractIndex:
    """An `OptionContractIndex` built directly, bypassing the store scan.

    `__init__` reads a Parquet directory listing; these tests are about the
    strike-selection arithmetic alone, so the internal maps are populated by
    hand rather than by writing thousands of partitions to disk.
    """
    index = OptionContractIndex.__new__(OptionContractIndex)
    index._base = "NIFTY"  # noqa: SLF001
    index._by_expiry = {}  # noqa: SLF001
    for option_type in ("CE", "PE"):
        index._by_expiry[(expiry, option_type)] = [  # noqa: SLF001
            ParsedOptionSymbol(
                symbol=f"NIFTY{expiry:%d%b%y}".upper() + f"{option_type[0]}{strike}",
                base="NIFTY",
                expiry=expiry,
                strike=Decimal(strike),
                option_type=option_type,
            )
            for strike in strikes
        ]
    index._expiries = [expiry]  # noqa: SLF001
    return index


_EXPIRY = dt.date(2026, 3, 12)
_ON = dt.date(2026, 3, 10)
#: A normal NIFTY chain: 50-point steps around a 24,500 spot.
_STRIKES = [24_300, 24_350, 24_400, 24_450, 24_500, 24_550, 24_600, 24_650, 24_700]


@pytest.fixture
def chain() -> OptionContractIndex:
    return _index(_STRIKES, expiry=_EXPIRY)


def test_zero_offset_is_at_the_money_and_unchanged(chain: OptionContractIndex) -> None:
    """The default must reproduce the old behaviour exactly, or every
    measurement taken before this parameter existed becomes incomparable."""
    for option_type in ("CE", "PE"):
        contract = chain.nearest(on=_ON, index_level=Decimal(24_500), option_type=option_type)
        assert contract is not None
        assert contract.strike == Decimal(24_500)


@pytest.mark.parametrize(("offset", "expected"), [(1, 24_550), (2, 24_600), (4, 24_700)])
def test_a_call_moves_UP_the_chain(chain: OptionContractIndex, offset: int, expected: int) -> None:
    contract = chain.nearest(
        on=_ON, index_level=Decimal(24_500), option_type="CE", strikes_out_of_the_money=offset
    )
    assert contract is not None
    assert contract.strike == Decimal(expected)


@pytest.mark.parametrize(("offset", "expected"), [(1, 24_450), (2, 24_400), (4, 24_300)])
def test_a_put_moves_DOWN_the_chain(chain: OptionContractIndex, offset: int, expected: int) -> None:
    """The direction inversion is the whole reason this needs a test: a put
    gets cheaper as the strike FALLS, and getting this backwards would buy
    deep in-the-money puts while reporting them as cheap OTM ones."""
    contract = chain.nearest(
        on=_ON, index_level=Decimal(24_500), option_type="PE", strikes_out_of_the_money=offset
    )
    assert contract is not None
    assert contract.strike == Decimal(expected)


@pytest.mark.parametrize("option_type", ["CE", "PE"])
def test_a_distance_the_chain_cannot_reach_is_refused_not_clamped(
    chain: OptionContractIndex, option_type: str
) -> None:
    """Clamping to the furthest listed strike would report a result for a
    distance the archive cannot support — a quiet substitution that no
    downstream number would reveal."""
    assert (
        chain.nearest(on=_ON, index_level=Decimal(24_500), option_type=option_type, strikes_out_of_the_money=50)
        is None
    )


def test_the_offset_is_measured_from_spot_not_from_the_chain_edge(chain: OptionContractIndex) -> None:
    """With spot near the bottom of the listed range, OTM1 must still be one
    step from SPOT — not one step from the lowest strike on file."""
    contract = chain.nearest(
        on=_ON, index_level=Decimal(24_320), option_type="CE", strikes_out_of_the_money=1
    )
    assert contract is not None
    # 24,320 is nearest 24,300; one strike out of the money for a call is 24,350.
    assert contract.strike == Decimal(24_350)


def test_offset_counts_STRIKES_so_it_adapts_to_a_different_step_size() -> None:
    """BANKNIFTY steps in 100s, NIFTY in 50s. Counting strikes rather than
    points means "two strikes out" means the same structural thing on both,
    and can never land on a strike nobody listed."""
    wide = _index([56_000, 56_100, 56_200, 56_300, 56_400], expiry=_EXPIRY)
    contract = wide.nearest(
        on=_ON, index_level=Decimal(56_200), option_type="CE", strikes_out_of_the_money=2
    )
    assert contract is not None
    assert contract.strike == Decimal(56_400)


def test_an_expiry_exactly_at_max_days_to_expiry_is_still_reachable(chain: OptionContractIndex) -> None:
    """The boundary itself. `_EXPIRY` (2026-03-12) is exactly 2 days after
    `_ON` (2026-03-10) — every other test in this file uses a 2-day gap
    against the default `max_days_to_expiry=7` or a gap far past it, never a
    candidate sitting exactly ON the limit. `>` (not `>=`) must still return
    a contract here, or a compliant request one day inside its own stated
    window would be silently refused."""
    contract = chain.nearest(
        on=_ON,
        index_level=Decimal(24_500),
        option_type="CE",
        strikes_out_of_the_money=1,
        max_days_to_expiry=(_EXPIRY - _ON).days,
    )
    assert contract is not None
    assert contract.strike == Decimal(24_550)


def test_an_expiry_beyond_the_cycle_is_still_refused_with_an_offset(chain: OptionContractIndex) -> None:
    """The coverage guard must not be weakened by the new parameter."""
    assert (
        chain.nearest(
            on=dt.date(2026, 1, 1),
            index_level=Decimal(24_500),
            option_type="CE",
            strikes_out_of_the_money=2,
        )
        is None
    )
