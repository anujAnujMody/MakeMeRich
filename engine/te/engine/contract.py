"""Contract resolution — turns a rule's `long_call`/`long_put` intent on an
INDEX into a concrete, tradeable OPTION contract with a real premium.

This closes the defect found live on 2026-07-31: ORB correctly detected
breakouts on the index, but nothing ever converted that into an option, so
`te.engine.cycle` sized and ordered the raw underlying (`BUY NIFTY on NFO` at
a "premium" of ₹24,350 — the index level). Cost was then computed against a
₹15.8 lakh notional, making the cost-vs-edge gate reject every signal
deterministically, forever.

The resolution deliberately delegates strike selection to OpenAlgo's
`optionsymbol` service rather than computing it here: no strike-step table,
no ATM rounding, no expiry-weekday/holiday arithmetic to keep correct. See
`te.broker.openalgo_rest.OpenAlgoRestClient.option_symbol`.

`te.engine` may import `te.broker` per the layer rule; `te.strategy` may not,
which is why this lives here and not inside the rule.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import structlog

from te.broker.openalgo_rest import OpenAlgoRestClient, OpenAlgoRestError
from te.domain.clock import IST
from te.domain.money import Paise
from te.domain.signal import Direction
from te.domain.symbols import parse_expiry

logger = structlog.get_logger(__name__)

#: Underlying -> the INDEX exchange its spot quotes live on. Distinct from
#: the placement exchange (NFO/BFO), which `optionsymbol` returns.
UNDERLYING_INDEX_EXCHANGES: dict[str, str] = {
    "NIFTY": "NSE_INDEX",
    "BANKNIFTY": "NSE_INDEX",
    "SENSEX": "BSE_INDEX",
    "BANKEX": "BSE_INDEX",
}

_OPTION_TYPE: dict[str, str] = {"long_call": "CE", "long_put": "PE"}


@dataclass(frozen=True)
class ResolvedContract:
    """A real contract plus the real premium to trade it at."""

    symbol: str
    exchange: str
    lot_size: int
    premium: Paise
    bid: Paise
    ask: Paise
    underlying_ltp: float
    #: Largest quantity the exchange permits in a SINGLE order for this
    #: contract, straight from the broker's `optionsymbol` response. `0`
    #: means the broker did not report one — treated as "no cap known"
    #: rather than "no cap exists", because published freeze quantities
    #: disagree between sources and change every few months (R7's problem,
    #: same shape as lot size). See `te.engine.cycle`'s freeze-quantity
    #: guard for why it caps rather than chunks.
    freeze_qty: int = 0

    @property
    def spread_pct(self) -> Decimal:
        """Bid-ask spread as a percentage of LTP. `Decimal(0)` when the
        premium is zero (a dead/untraded contract), which the liquidity guard
        rejects separately on premium rather than reading a misleading 0%."""
        if self.premium <= 0:
            return Decimal(0)
        return Decimal(int(self.ask) - int(self.bid)) / Decimal(int(self.premium)) * Decimal(100)


#: What `te.engine.cycle` accepts. A plain callable rather than the concrete
#: class, so tests (and a future historical-replay resolver reading
#: `option_bhav`) can substitute their own without a broker.
type ContractResolver = Callable[[str, Direction, dt.datetime], ResolvedContract | None]


class OptionContractResolver:
    """Resolves `(underlying, direction)` to a tradeable contract at `as_of`.

    Returns `None` (never raises) when a contract cannot be resolved or fails
    a guard — the caller records that as a real skip reason, matching this
    engine's rule that every skip carries a stated cause. A broker outage must
    degrade to "no trade this cycle", never to a crashed cycle.
    """

    def __init__(
        self,
        client: OpenAlgoRestClient,
        *,
        offset: str = "ATM",
        max_spread_pct: Decimal = Decimal("1.0"),
        min_premium_paise: int = 500,
    ) -> None:
        self._client = client
        self._offset = offset
        self._max_spread_pct = max_spread_pct
        self._min_premium_paise = min_premium_paise
        # `(underlying, IST trade date) -> expiries`. The listed expiry chain
        # for an underlying does not change during a session, but this
        # resolver is long-lived (built once in `build_scheduler`), so
        # without a cache every firing pays an extra blocking REST round trip
        # to re-learn a constant. Keyed by date so a session rollover cannot
        # serve yesterday's chain.
        self._expiries: dict[tuple[str, dt.date], list[str]] = {}

    def _chain(self, underlying: str, index_exchange: str, as_of: dt.datetime) -> list[str]:
        """The broker's listed expiry chain, nearest first, cached per
        `(underlying, IST date)`."""
        cache_key = (underlying, as_of.astimezone(IST).date())
        expiries = self._expiries.get(cache_key)
        if expiries is None:
            expiries = self._client.expiry_dates(underlying, _placement_exchange(index_exchange))
            if expiries:
                self._expiries[cache_key] = expiries
        return expiries

    def expiry_dates(self, underlying: str, as_of: dt.datetime) -> frozenset[dt.date]:
        """Every listed expiry for `underlying`, as real dates.

        Exists so the LIVE path can answer "is today an expiry day?" from the
        broker's own calendar. `ExpiryDayOnly` previously had to guess from
        the weekday live, while its backtest read the real contract archive —
        so the two measured different strategies under one name. NIFTY's
        weekly expiry moved Thursday -> Tuesday inside the recorded data, and
        the weekday guess mismatched 83 of 125 real expiries.

        Returns an EMPTY set rather than raising when the broker is
        unreachable or returns something unparseable: an unknown calendar
        must make a calendar-gated rule stand down, not crash the cycle.
        """
        index_exchange = UNDERLYING_INDEX_EXCHANGES.get(underlying)
        if index_exchange is None:
            return frozenset()
        try:
            raw = self._chain(underlying, index_exchange, as_of)
        except OpenAlgoRestError:
            logger.exception("expiry chain unavailable", underlying=underlying)
            return frozenset()
        dates = set()
        for text in raw:
            try:
                dates.add(parse_expiry(text))
            except ValueError:
                logger.warning("unparseable expiry from broker", underlying=underlying, expiry=text)
        return frozenset(dates)

    def strike_band(self, underlying: str, as_of: dt.datetime, *, band: int) -> list[tuple[str, str]]:
        """Every `(symbol, placement_exchange)` within `band` strikes either
        side of ATM, on the nearest expiry, for BOTH option types.

        Exists so the live recorder can archive real option PREMIUMS, not
        just index levels. Found on 2026-08-03: the WS recorder subscribed
        only to the four index symbols, so the bar store had no live premium
        data at all — and every backtest scores strategies on premiums. The
        only premium source was the exchange's once-a-day bhavcopy, which
        meant a day's trading could never be measured until the next
        morning.

        A BAND rather than just ATM because ATM is not a fixed strike: it
        follows the index all day. Recording only the strike that was ATM at
        09:15 would lose the actual traded contract the moment the index
        moved half a percent. `ITMn`/`OTMn` walk in opposite directions for
        calls and puts, so running both types over the same offsets covers
        one contiguous strike window for each.

        Returns an EMPTY list rather than raising on any broker failure, and
        skips individual strikes that fail to resolve: this feeds a
        subscription list, and losing option coverage must never take the
        index recording down with it.
        """
        index_exchange = UNDERLYING_INDEX_EXCHANGES.get(underlying)
        if index_exchange is None:
            logger.warning("no index exchange mapped for underlying", underlying=underlying)
            return []
        try:
            expiries = self._chain(underlying, index_exchange, as_of)
        except OpenAlgoRestError:
            logger.exception("expiry chain unavailable for strike band", underlying=underlying)
            return []
        if not expiries:
            logger.warning("broker returned no expiries for strike band", underlying=underlying)
            return []

        offsets = [
            "ATM",
            *(f"ITM{step}" for step in range(1, band + 1)),
            *(f"OTM{step}" for step in range(1, band + 1)),
        ]
        # Keyed by symbol so first-wins dedup and insertion order come from
        # one structure: a broker that clamps a deep offset to the end of the
        # listed chain returns the same contract twice, and subscribing to it
        # twice would double every tick it produces.
        found: dict[str, str] = {}
        for option_type in ("CE", "PE"):
            for offset in offsets:
                try:
                    contract = self._client.option_symbol(underlying, index_exchange, expiries[0], offset, option_type)
                except OpenAlgoRestError:
                    logger.warning(
                        "strike unresolvable — skipped",
                        underlying=underlying,
                        offset=offset,
                        option_type=option_type,
                    )
                    continue
                found.setdefault(contract.symbol, contract.exchange)
        logger.info("resolved strike band", underlying=underlying, strikes=len(found), band=band)
        return list(found.items())

    def __call__(self, underlying: str, direction: Direction, as_of: dt.datetime) -> ResolvedContract | None:
        index_exchange = UNDERLYING_INDEX_EXCHANGES.get(underlying)
        if index_exchange is None:
            logger.warning("no index exchange mapped for underlying", underlying=underlying)
            return None

        option_type = _OPTION_TYPE[direction]
        try:
            # `expiry_dates` is nearest-first and broker-confirmed, so [0] is
            # the current weekly WITHOUT any holiday arithmetic on our side.
            expiries = self._chain(underlying, index_exchange, as_of)
            if not expiries:
                logger.warning("broker returned no expiries", underlying=underlying)
                return None

            contract = self._client.option_symbol(underlying, index_exchange, expiries[0], self._offset, option_type)
            quote = self._client.quotes(contract.symbol, contract.exchange)
        except OpenAlgoRestError:
            logger.exception("contract resolution failed", underlying=underlying, direction=direction)
            return None

        resolved = ResolvedContract(
            symbol=contract.symbol,
            exchange=contract.exchange,
            lot_size=contract.lot_size,
            freeze_qty=contract.freeze_qty,
            premium=Paise(int(round(quote.ltp * 100))),
            bid=Paise(int(round(quote.bid * 100))),
            ask=Paise(int(round(quote.ask * 100))),
            underlying_ltp=contract.underlying_ltp,
        )

        # Guards. A near-zero premium means an untraded/dead strike, and a
        # wide spread means the round trip pays more in slippage than the
        # statutory charges — both are real reasons not to trade, and both are
        # invisible to the cost model (which only sees LTP).
        if int(resolved.premium) < self._min_premium_paise:
            logger.info(
                "contract rejected: premium too low",
                symbol=resolved.symbol,
                premium_paise=int(resolved.premium),
            )
            return None
        # A quote with no usable depth must be REJECTED, not waved through.
        # `openalgo_rest.quotes()` defaults a missing `bid`/`ask` to 0.0, so
        # an envelope without depth fields yields `spread_pct = (0-0)/ltp =
        # 0%` — which sails past the ceiling below and turns the liquidity
        # guard into a no-op precisely on the illiquid strikes it exists to
        # exclude. `ask < bid` is likewise a crossed/stale book, not a
        # tradeable one.
        if int(resolved.bid) <= 0 or int(resolved.ask) <= 0 or int(resolved.ask) < int(resolved.bid):
            logger.info(
                "contract rejected: no usable bid/ask depth",
                symbol=resolved.symbol,
                bid_paise=int(resolved.bid),
                ask_paise=int(resolved.ask),
            )
            return None
        if resolved.spread_pct > self._max_spread_pct:
            logger.info(
                "contract rejected: spread too wide",
                symbol=resolved.symbol,
                spread_pct=str(resolved.spread_pct),
            )
            return None
        return resolved


def _placement_exchange(index_exchange: str) -> str:
    """`expiry` is keyed by the DERIVATIVES exchange, while `optionsymbol` is
    keyed by the INDEX exchange — verified against a live OpenAlgo instance on
    2026-07-31."""
    return "BFO" if index_exchange == "BSE_INDEX" else "NFO"
