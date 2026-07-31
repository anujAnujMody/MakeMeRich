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

    def __call__(self, underlying: str, direction: Direction, as_of: dt.datetime) -> ResolvedContract | None:
        index_exchange = UNDERLYING_INDEX_EXCHANGES.get(underlying)
        if index_exchange is None:
            logger.warning("no index exchange mapped for underlying", underlying=underlying)
            return None

        option_type = _OPTION_TYPE[direction]
        try:
            # `expiry_dates` is nearest-first and broker-confirmed, so [0] is
            # the current weekly WITHOUT any holiday arithmetic on our side.
            cache_key = (underlying, as_of.astimezone(IST).date())
            expiries = self._expiries.get(cache_key)
            if expiries is None:
                expiries = self._client.expiry_dates(underlying, _placement_exchange(index_exchange))
                if expiries:
                    self._expiries[cache_key] = expiries
            if not expiries:
                logger.warning("broker returned no expiries", underlying=underlying)
                return None

            contract = self._client.option_symbol(
                underlying, index_exchange, expiries[0], self._offset, option_type
            )
            quote = self._client.quotes(contract.symbol, contract.exchange)
        except OpenAlgoRestError:
            logger.exception("contract resolution failed", underlying=underlying, direction=direction)
            return None

        resolved = ResolvedContract(
            symbol=contract.symbol,
            exchange=contract.exchange,
            lot_size=contract.lot_size,
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
