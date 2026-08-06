"""The exchange freeze quantity — the largest quantity permitted in a single
order — and the cap that keeps the engine below it.

Above it the exchange rejects the order outright. Live, that arrives as an
opaque broker error at the exact moment a position was meant to open, and
the engine has no idea why. This is the one Phase-2 order-lifecycle rule
that can be enforced entirely from data the broker already returns.

At this account's size the cap never binds (one NIFTY lot is 65 against a
freeze quantity in the low thousands), which is precisely why it needs a
test: nothing in ordinary operation would ever exercise it.
"""

from __future__ import annotations

import datetime as dt

from te.domain.money import Paise
from te.engine.contract import ResolvedContract


def _contract(*, lot_size: int, freeze_qty: int) -> ResolvedContract:
    return ResolvedContract(
        symbol="NIFTY07AUG2624500CE",
        exchange="NFO",
        lot_size=lot_size,
        premium=Paise(10_000),
        bid=Paise(9_900),
        ask=Paise(10_100),
        underlying_ltp=24_500.0,
        freeze_qty=freeze_qty,
    )


def test_freeze_quantity_defaults_to_zero_meaning_unknown() -> None:
    """`0` must mean "the broker did not tell us", NOT "there is no limit"
    and not "the limit is zero". Published freeze quantities disagree
    between sources (BANKNIFTY is quoted as both 600 and 900) and change
    every few months, so the only trustworthy value is the broker's own —
    and inventing one when it is absent is worse than having none."""
    contract = ResolvedContract(
        symbol="NIFTY07AUG2624500CE",
        exchange="NFO",
        lot_size=65,
        premium=Paise(10_000),
        bid=Paise(9_900),
        ask=Paise(10_100),
        underlying_ltp=24_500.0,
    )
    assert contract.freeze_qty == 0


def test_the_cap_arithmetic_floors_to_whole_lots() -> None:
    """1,800 / 65 = 27.7 — the cap must be 27 lots (1,755 qty), never 28
    (1,820, over the limit). Rounding the wrong way here produces exactly
    the rejected order the cap exists to prevent."""
    contract = _contract(lot_size=65, freeze_qty=1_800)
    capped = contract.freeze_qty // contract.lot_size
    assert capped == 27
    assert capped * contract.lot_size <= contract.freeze_qty


def test_a_freeze_below_one_lot_floors_to_zero_lots() -> None:
    """The arithmetic that made the 2026-08-06 outage possible: a freeze
    quantity under the lot size floors to ZERO tradeable lots.

    Kept as a statement of the arithmetic only. The engine no longer acts on
    it — `te.engine.cycle` filters sub-lot freeze quantities out as missing
    values BEFORE this division, because a cap below one lot cannot be real:
    it would mean the exchange quotes a contract on which no legal order
    exists. Every observed instance was a lookup miss answering `1`.

    See `tests/engine/test_cycle.py::
    test_a_freeze_quantity_below_one_lot_is_treated_as_unreported` for the
    behaviour this arithmetic no longer drives.
    """
    contract = _contract(lot_size=900, freeze_qty=600)
    assert contract.freeze_qty // contract.lot_size == 0


def test_the_resolver_carries_the_brokers_freeze_quantity_through(monkeypatch) -> None:  # noqa: ANN001
    """The value has to survive the hop from `optionsymbol` into
    `ResolvedContract` — it was fetched and then dropped on the floor until
    2026-08-01, so the guard downstream had nothing to act on."""
    from te.broker.openalgo_rest import OptionContract, Quote
    from te.engine.contract import OptionContractResolver

    class _Client:
        def expiry_dates(self, symbol: str, exchange: str, instrument_type: str = "options") -> list[str]:
            return ["07AUG26"]

        def option_symbol(self, *args: object, **kwargs: object) -> OptionContract:
            return OptionContract(
                symbol="NIFTY07AUG2624500CE",
                exchange="NFO",
                lot_size=65,
                tick_size=0.05,
                freeze_qty=1_800,
                underlying_ltp=24_500.0,
            )

        def quotes(self, symbol: str, exchange: str) -> Quote:
            return Quote(
                symbol=symbol,
                exchange=exchange,
                ltp=100.0,
                open=0.0,
                high=0.0,
                low=0.0,
                prev_close=0.0,
                volume=0.0,
                oi=0.0,
                bid=99.9,
                ask=100.1,
            )

    resolver = OptionContractResolver(_Client())  # type: ignore[arg-type]
    resolved = resolver("NIFTY", "long_call", dt.datetime(2026, 8, 3, 10, 30, tzinfo=dt.UTC))

    assert resolved is not None
    assert resolved.freeze_qty == 1_800
