"""The cost engine — every rupee figure gets exactly one code path, and it is
net. This is the most safety-critical module in the engine: it is what stops
the system from reporting a profit that costs would actually erase.

Rates are never constants in code — `ChargeRates` is constructed from
`config/charges.yaml` by a loader living outside `te.domain` (`te.domain` has
no I/O; see `te/data/charges_loader.py`), which also selects the correct
versioned row for a given trade date. `CostModel` is constructed with one
already-resolved `ChargeRates`.

**Rounding convention** (see `tests/domain/test_costs.py::test_worked_example`
for the full worked arithmetic): every component is rounded to the nearest
paise, ROUND_HALF_UP. For `round_trip()`, the percentage-based charges that
apply to both legs (`exchange_txn`, `sebi`) are computed off the COMBINED
buy+sell notional and rounded once — not computed per-leg and summed — since
summing two independently-rounded per-leg amounts drifts from the
combined-notional figure the plan's own worked example uses as ground truth.
GST is computed off the already-rounded brokerage/exchange_txn/sebi, then
rounded itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from te.domain.money import Paise

_BPS_DIVISOR = Decimal(10_000)
_ONE_PAISE = Decimal(1)

Side = Literal["BUY", "SELL"]
OptionType = Literal["CE", "PE"]


def _round_paise(value: Decimal) -> Paise:
    return Paise(int(value.quantize(_ONE_PAISE, rounding=ROUND_HALF_UP)))


@dataclass(frozen=True)
class ChargeRates:
    """One versioned row from `config/charges.yaml`."""

    effective_from: date
    verified_at: date
    brokerage_per_executed_order_paise: Paise
    stt_sell_bps: Decimal
    stt_exercise_intrinsic_bps: Decimal
    exchange_txn_bps: dict[str, Decimal]
    sebi_bps: Decimal
    gst_pct: Decimal
    stamp_buy_bps: Decimal


# The full versioned rate table, as loaded from `config/charges.yaml` — a
# loader elsewhere (`te/data/charges_loader.py`) selects the correct row by
# trade date before constructing a `CostModel`.
ChargeRateTable = list[ChargeRates]


def select_rates(table: ChargeRateTable, on: date) -> ChargeRates:
    """Picks the rate row with the latest `effective_from <= on`. Raises if
    every row is in the future relative to `on`."""
    candidates = [r for r in table if r.effective_from <= on]
    if not candidates:
        raise ValueError(f"no charge rates effective on or before {on!r} — earliest row is in the future")
    return max(candidates, key=lambda r: r.effective_from)


@dataclass(frozen=True)
class CostBreakdown:
    """Itemised transaction costs, all in integer paise."""

    brokerage: Paise
    stt: Paise
    exchange_txn: Paise
    sebi: Paise
    gst: Paise
    stamp: Paise

    @property
    def total(self) -> Paise:
        return Paise(self.brokerage + self.stt + self.exchange_txn + self.sebi + self.gst + self.stamp)


_ZERO_BREAKDOWN = CostBreakdown(
    brokerage=Paise(0), stt=Paise(0), exchange_txn=Paise(0), sebi=Paise(0), gst=Paise(0), stamp=Paise(0)
)


class CostModel:
    """Computes itemised transaction costs from one resolved `ChargeRates`
    row. GST applies ONLY to (brokerage + exchange_txn + sebi), never to STT
    or stamp duty. STT is SELL-side only (except the exercise variant,
    charged to the option holder on intrinsic value at expiry). Stamp duty is
    BUY-side only."""

    def __init__(self, rates: ChargeRates) -> None:
        self._rates = rates

    def leg(self, *, side: Side, premium: Paise, qty: int, exchange: str, on: date) -> CostBreakdown:
        """Cost of a single order execution (one side, one fill)."""
        rates = self._rates_for(on)
        notional = Decimal(premium) * Decimal(qty)
        exch_bps = rates.exchange_txn_bps[exchange]

        brokerage = Paise(rates.brokerage_per_executed_order_paise)
        stt = _round_paise(notional * rates.stt_sell_bps / _BPS_DIVISOR) if side == "SELL" else Paise(0)
        exchange_txn = _round_paise(notional * exch_bps / _BPS_DIVISOR)
        sebi = _round_paise(notional * rates.sebi_bps / _BPS_DIVISOR)
        stamp = _round_paise(notional * rates.stamp_buy_bps / _BPS_DIVISOR) if side == "BUY" else Paise(0)
        gst = self._gst_on(brokerage, exchange_txn, sebi, rates)

        return CostBreakdown(brokerage=brokerage, stt=stt, exchange_txn=exchange_txn, sebi=sebi, gst=gst, stamp=stamp)

    def round_trip(
        self, *, entry_premium: Paise, exit_premium: Paise, qty: int, exchange: str, on: date
    ) -> CostBreakdown:
        """Cost of a full buy-then-sell round trip. See the module docstring
        for the exact rounding convention this uses to reproduce the plan's
        worked example (₹65.11) exactly."""
        rates = self._rates_for(on)
        exch_bps = rates.exchange_txn_bps[exchange]

        buy_notional = Decimal(entry_premium) * Decimal(qty)
        sell_notional = Decimal(exit_premium) * Decimal(qty)
        combined_notional = buy_notional + sell_notional

        brokerage = Paise(rates.brokerage_per_executed_order_paise * 2)
        stt = _round_paise(sell_notional * rates.stt_sell_bps / _BPS_DIVISOR)
        exchange_txn = _round_paise(combined_notional * exch_bps / _BPS_DIVISOR)
        sebi = _round_paise(combined_notional * rates.sebi_bps / _BPS_DIVISOR)
        stamp = _round_paise(buy_notional * rates.stamp_buy_bps / _BPS_DIVISOR)
        gst = self._gst_on(brokerage, exchange_txn, sebi, rates)

        return CostBreakdown(brokerage=brokerage, stt=stt, exchange_txn=exchange_txn, sebi=sebi, gst=gst, stamp=stamp)

    def expiry_settlement(
        self, *, strike: Paise, spot_at_expiry: Paise, qty: int, option_type: OptionType, on: date
    ) -> CostBreakdown:
        """OTM at expiry: no exercise happens, so no component charges
        anything. ITM at expiry: STT is charged on INTRINSIC value only
        (post-Sep-2019 rule), never on the full settlement notional; no other
        component applies (exercise settlement is not a regular order
        execution)."""
        rates = self._rates_for(on)
        if option_type == "CE":
            intrinsic = Decimal(spot_at_expiry) - Decimal(strike)
        else:
            intrinsic = Decimal(strike) - Decimal(spot_at_expiry)

        if intrinsic <= 0:
            return _ZERO_BREAKDOWN

        intrinsic_notional = intrinsic * Decimal(qty)
        stt = _round_paise(intrinsic_notional * rates.stt_exercise_intrinsic_bps / _BPS_DIVISOR)
        return CostBreakdown(
            brokerage=Paise(0), stt=stt, exchange_txn=Paise(0), sebi=Paise(0), gst=Paise(0), stamp=Paise(0)
        )

    def _gst_on(self, brokerage: Paise, exchange_txn: Paise, sebi: Paise, rates: ChargeRates) -> Paise:
        taxable = Decimal(brokerage) + Decimal(exchange_txn) + Decimal(sebi)
        return _round_paise(taxable * rates.gst_pct / Decimal(100))

    def _rates_for(self, on: date) -> ChargeRates:
        if on < self._rates.effective_from:
            raise ValueError(
                f"trade date {on!r} is before this CostModel's rates became effective "
                f"({self._rates.effective_from!r}) — construct the model with the rate row that "
                "was actually in force on that date"
            )
        return self._rates
