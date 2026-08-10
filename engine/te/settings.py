"""Application settings — pydantic-settings, env_prefix="TE_".

`env` and `database_url` have NO defaults: a missing environment variable
must raise a `ValidationError` at instantiation, never silently fall back to
a default (a silent default is how tests end up writing into prod — see the
plan's "Database" section).
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    #: Deliberately NO `env_file` here — see the module docstring's "NO
    #: defaults" rule. A silent fallback file would defeat the exact
    #: `test_settings_fails_without_*` tests that rule exists for. The
    #: bare-host dev engine (`yarn dev:engine`/`dev:all`) instead loads
    #: `engine/.env` at the SHELL level (`dotenv run --`) before this class
    #: ever runs, so those values arrive as real process env vars — the same
    #: mechanism Docker Compose uses, not a second, quieter source Settings
    #: itself falls back to.
    model_config = SettingsConfigDict(env_prefix="TE_", extra="ignore")

    env: Literal["dev", "prod"]
    database_url: str

    bar_store_path: Path
    openalgo_host: str

    #: OpenAlgo's WebSocket endpoint, e.g. `ws://openalgo:8765`. Configured
    #: EXPLICITLY and separately from `openalgo_host` — deliberately never
    #: derived from it. `openalgo_host` already carries its own port
    #: (`http://openalgo:5000`), so scheme-swapping string surgery plus a
    #: `:8765` suffix yields the invalid two-port `ws://openalgo:5000:8765`;
    #: and in a real deployment the REST and WS endpoints may sit on
    #: entirely different hostnames, ports and paths behind a proxy.
    #:
    #: Required, with NO default, for the same reason `env`/`database_url`
    #: are: a silently-wrong endpoint here means the recorder simply never
    #: connects, no bars are ever written, and every strategy quietly
    #: no-ops — a failure that is invisible until someone notices the
    #: missing data. A missing env var must fail loudly at startup instead.
    openalgo_ws_host: str

    openalgo_api_key: SecretStr
    cors_origins: list[str]

    charges_path: Path = Path("config/charges.yaml")
    max_orders_per_second: int = 5
    #: Quote requests/sec. A SEPARATE budget from orders: Angel SmartAPI
    #: rate-limits its quote endpoint far more tightly than order placement
    #: (published as 1 request/sec, with a 50-symbol bulk form), and forum
    #: reports say enforcement is inconsistent — so exceeding it produces
    #: intermittent failures rather than a clean, obvious error.
    #:
    #: This engine polls one quote per OPEN POSITION per cycle to mark it
    #: (see `_current_premium_from_quotes`), which at a handful of positions
    #: a minute sits far below the limit. The limiter exists so that stays
    #: true when position count or cycle frequency changes, rather than
    #: being rediscovered as flaky marks during a live session.
    max_quotes_per_second: int = 1

    # Paper-trading cycle job (te.engine.scheduler) — an intraday ORB
    # strategy, so the default interval is short. `paper_cycle_instruments`
    # empty means the job is registered but is a documented per-instrument
    # no-op (never a hardcoded literal instrument list) until configured.
    paper_cycle_enabled: bool = True
    paper_cycle_interval_minutes: int = 1
    paper_cycle_instruments: tuple[str, ...] = ()
    paper_cycle_exchange: str = "NFO"
    paper_cycle_strategy: str = "orb"
    paper_cycle_lot_size: int = 65
    #: A SEED, nothing more: `te.engine.state.guardrails_defaults_from_settings`
    #: turns this into the default `AccountGuardrails.capital` that
    #: `get_guardrails` falls back to ONLY when `engine_state` holds no
    #: `capital_paise` row yet (a brand-new database). Once anything has been
    #: saved from the dashboard's Account Guardrails card, that stored value
    #: wins on every read — including every real paper-cycle run
    #: (`PaperCycleRunner.run_once` rebuilds its config from `get_guardrails`
    #: fresh each cycle) — and this field, and any `TE_PAPER_CYCLE_CAPITAL_
    #: PAISE` env var setting it, are both ignored for trading from then on.
    #: `build_scheduler` logs a WARNING at startup naming both values when
    #: they diverge, precisely so that divergence is never silent again — it
    #: previously was: an operator set `TE_PAPER_CYCLE_CAPITAL_PAISE=2000000`
    #: (Rs 20,000) in `docker-compose.yml` believing it controlled position
    #: sizing, while a stored Rs 50,000 governed every real cycle.
    #:
    #: The live value is NOT this line. Read `GET /api/engine/guardrails`,
    #: or the dashboard's Settings page, for what is actually in force. This
    #: default (Rs 50,000) was itself hand-derived from the real account's
    #: history: originally Rs 30,000 (set 2026-08-04, matching paper trading
    #: to the live account so `size_position`'s affordability rejections —
    #: measured that day at ~85% of ORB's signals — reflect the constraint
    #: that actually governs), raised by the owner on 2026-08-05 to Rs
    #: 50,000. That history is preserved only as the rationale for THIS
    #: number as a seed; it says nothing about what a fresh database, or a
    #: dashboard edit, holds today.
    paper_cycle_capital_paise: int = 5_000_000
    #: Capped by `te.engine.state.MAX_RISK_PER_TRADE_PCT` — see the ceilings
    #: comment there for the risk-of-ruin arithmetic. Shipping a default
    #: above the ceiling would mean the very first read clamped it, which
    #: reads as the engine ignoring its own configuration.
    #:
    #: 5%, raised from 1.5% on 2026-08-05 by the owner. At 1.5% of Rs 30,000
    #: the risk budget is Rs 450, and one NIFTY lot at a ~Rs 42 premium risks
    #: ~Rs 549 at a 20% stop — so the old value rejected every signal outright
    #: (all four of 2026-08-04's real trades re-size to `lots=0` at 1.5%).
    #: This is a knowingly larger bet per trade, bounded in practice by
    #: `paper_cycle_max_daily_loss_paise` below rather than by this number.
    paper_cycle_risk_budget_pct: Decimal = Decimal(5)
    paper_cycle_min_edge_multiple: Decimal = Decimal("1.2")
    paper_cycle_stop_distance_paise: int = 700
    paper_cycle_target_distance_paise: int = 1_500
    paper_cycle_trailing_distance_paise: int | None = 300
    paper_cycle_max_hold_minutes: int = 180
    #: 15:15, not 15:20. Angel One's published Risk Management Policy squares
    #: off intraday F&O at 15:20 — the same minute this engine used to plan
    #: its own exit. A tie there means the BROKER closes the position: their
    #: fill, at their price, plus a call-and-trade/auto-square-off charge, and
    #: an exit this engine would record as its own. Five minutes of clearance
    #: keeps the exit ours. Verified 2026-08-01:
    #: https://www.angelone.in/support/your-orders/square-off
    paper_cycle_hard_exit_by: dt.time = dt.time(15, 15)
    #: Minutes of runway a new entry must have before `hard_exit_by`.
    #:
    #: 40 = the MEDIAN minutes a winning firing took to reach its target,
    #: measured 2026-08-01 by `scripts/measure_time_to_target.py` over 438
    #: labelled winners on the 60-minute opening-range replay set
    #: (2026-04-01..2026-07-30, 1:1 barriers):
    #:
    #:                 n     p10   p25   p50   p75   p90
    #:   NIFTY       241       7    15    35    71    99
    #:   SENSEX      197      12    23    47    87   118
    #:   COMBINED    438       9    18    40    78   110
    #:
    #: The median is the floor, not a preference: below it, more than half of
    #: all historical winners could not have finished before the clock closed
    #: them. p75 (78m) would protect three quarters but refuses many more late
    #: entries; that trade-off has not been optimised (doing so honestly needs
    #: purged CV), so the descriptive midpoint is what ships.
    #:
    #: With `hard_exit_by` at 15:15 this stops new entries after 14:35. The
    #: failure it prevents is concrete: a NIFTY position opened 15:05 on
    #: 2026-07-31 and force-closed 15:20 for -Rs 6,672 — 82% of that day's
    #: entire loss. Its stop never fired; the clock closed it. It carried full
    #: downside while its upside was arithmetically unreachable.
    paper_cycle_min_minutes_before_hard_exit: int = 40
    #: Rs 2,500 — DERIVED, not chosen: it is exactly one full stop-out at the
    #: capital and risk percentage in force, which is the rule this limit has
    #: always encoded ("one full stop-out, then stand down").
    #:
    #:     risk_budget_pct x capital = 5% x Rs 50,000 = Rs 2,500
    #:
    #: So it must be re-derived whenever `paper_cycle_risk_budget_pct` OR
    #: `paper_cycle_capital_paise` changes. It did not track either of them
    #: through two such changes, and the resulting mismatch is why this
    #: comment spells the derivation out rather than stating a number:
    #:
    #: * Rs 30,000 @ 5% -> Rs 1,500 risk, and Rs 2,000 left room for one
    #:   loser. The rule held.
    #: * Rs 50,000 @ 5% -> Rs 2,500 risk against an UNCHANGED Rs 2,000 cap.
    #:   One losing trade now exceeded the whole day's budget, so the first
    #:   loser breached the halt before it had even finished losing. The
    #:   engine was effectively capped at one trade a day, which is not what
    #:   `paper_cycle_max_trades_per_day = 20` says.
    #:
    #: Raised to Rs 2,500 by the owner on 2026-08-06, restoring the rule.
    #: Against Rs 50,000 that is 5%, inside the 7% ceiling in
    #: `te.engine.state.MAX_DAILY_LOSS_PCT_OF_CAPITAL` (Rs 3,500), so it is a
    #: real limit rather than one the clamp imposes.
    #:
    #: The live DB is authoritative and may differ from this default —
    #: `get_guardrails` prefers the stored value. Read `GET
    #: /api/engine/guardrails`, not this line, for what is actually in force.
    paper_cycle_max_daily_loss_paise: int = 250_000
    paper_cycle_max_concurrent_positions: int = 5
    #: Stand down from NEW entries for the rest of the day after this many
    #: consecutive losing trades (`0` disables). Three is the conventional
    #: prop-desk figure — it is a behavioural circuit breaker against
    #: revenge-trading a bad session, not a measured edge, and is documented
    #: as such rather than dressed up as one. Open positions keep their
    #: exits; only new entries stop. See `te.risk.limits.
    #: check_consecutive_losses`.
    paper_cycle_max_consecutive_losses: int = 3
    #: 3, matching `te.backtest.strategy_lab.MAX_ENTRIES_PER_DAY` exactly.
    #:
    #: Was 20, with no comment and no derivation — the only bare number in
    #: this block. Three things were wrong with it, found 2026-08-06:
    #:
    #: 1. Every backtest result this project has ever produced was measured
    #:    with a cap of THREE entries per day. Live ran at 20, so live was
    #:    not the thing that was measured.
    #: 2. Measured cost per round trip across all 22 real trades averages
    #:    Rs 100.95 (min 65.12, max 210.63). Twenty trades is Rs 2,019/day of
    #:    charges against a Rs 500-1,000/day income target — the ceiling cost
    #:    twice the best case.
    #: 3. It could never bind anyway. With `paper_cycle_max_daily_loss_paise`
    #:    at one full stop-out, a single loser ends the day, so no path
    #:    through the code reaches trade 20. It was a number on a dashboard
    #:    that no execution could produce.
    #:
    #: The backtest cap is per strategy per day on ONE instrument, and live
    #: runs three; 9 would be the like-for-like reading. 3 is chosen instead
    #: because 9 has never been measured and 3 has.
    paper_cycle_max_trades_per_day: int = 3
    #: Hard ceiling on lots per position, applied after every computed sizing
    #: cap (`te.risk.sizing.size_position`). `None` = no ceiling.
    #:
    #: 1, and it is not a preference — it is what makes
    #: `paper_cycle_max_loss_per_trade_paise` below mean what it says. Sizing
    #: computes `lots = risk_budget // risk_per_lot`, so capping the per-lot
    #: risk at Rs 700 against a Rs 2,500 budget buys THREE lots and loses
    #: Rs 2,100. The rupee cap and the lot cap are one feature.
    #:
    #: It also matches how this account actually trades: every real trade so
    #: far has been 1 lot, because affordability rejected anything larger.
    paper_cycle_max_lots: int | None = 1
    #: The rupee loss ONE trade may take, in paise. `None` falls back to the
    #: percentage geometry above.
    #:
    #: Rs 700, set by the owner on 2026-08-06 after a NIFTY trade lost
    #: Rs 2,272 — 4.5% of a Rs 50,000 account in one trade — under a 20%
    #: premium stop. The ask was "buy what the signal picks, cap my loss",
    #: which neither percentage-of-premium nor absolute-premium-distance can
    #: express across instruments with different lot sizes. See
    #: `te.domain.geometry.RupeeRiskGeometry`.
    #:
    #: It also composes with `paper_cycle_max_trades_per_day = 3` and
    #: `paper_cycle_max_daily_loss_paise = Rs 2,500`: three full stop-outs is
    #: Rs 2,100, inside the daily halt, so the day's limits are reachable in
    #: the intended order rather than one rule pre-empting the others.
    #:
    #: What this DOES NOT do is make the strategy profitable. ORB scores the
    #: same as random entry; this changes the size of a loss, not its
    #: likelihood.
    paper_cycle_max_loss_per_trade_paise: int | None = 70_000
    #: Target distance as a MULTIPLE of the rupee risk, used only when
    #: `paper_cycle_max_loss_per_trade_paise` is set.
    #:
    #: 10 = risk Rs 700 to make Rs 7,000. Deliberately far, because the
    #: previous 1:1 geometry (20% stop, 20% target) capped every winner at
    #: exactly the size of every loser — profit was limited by configuration,
    #: not by the market.
    #:
    #: A distant target rather than NO target: a genuinely uncapped position
    #: needs a TRAILING stop to protect it, and the trail is the one exit
    #: rule never backtested here (the last live trail closed 14 of 14 trades
    #: at a 3.1-minute average hold). 10x is reachable — on a Rs 166.80
    #: premium it is +65%, which real intraday options do print — so this is
    #: a real ceiling, not a sentinel dressed up as one.
    paper_cycle_target_risk_multiple: Decimal = Decimal(10)

    # --- Option contract resolution (see te/engine/contract.py) ---
    #: Strike offset handed to OpenAlgo's `optionsymbol` service: `ATM`,
    #: `OTM1`..`OTM20`, `ITM1`..`ITM20`. ATM (delta ~0.5) is the standard
    #: intraday choice — tightest spreads, most linear response to the
    #: underlying. Deep OTM is the classic small-capital trap: cheap premium
    #: buys a low delta, so a correct directional call still loses to costs.
    paper_cycle_option_offset: str = "ATM"
    #: How many strikes either side of ATM the WS recorder archives premiums
    #: for, per underlying, on the nearest expiry. `0` disables option
    #: recording and restores the index-only behaviour that shipped before
    #: 2026-08-03.
    #:
    #: Not just ATM because ATM follows the index all day — a band keeps the
    #: actually-traded contract in the archive after the index moves. 5 is
    #: about +/-1% on NIFTY, which covers a normal session's range; the cost
    #: is one broker `optionsymbol` call per strike per type per underlying
    #: at recorder start (~88 calls, ~32s measured). Those calls are NOT
    #: rate-limited — `max_quotes_per_second` gates `quotes()` only — so the
    #: cost is round-trip latency, paid off the startup path on its own
    #: thread. Widening the band grows it linearly.
    recorder_strike_band: int = 5
    #: Stop/target as a % of the OPTION premium. These take priority over the
    #: absolute `*_distance_paise` values above, which are index-point-scaled
    #: leftovers and mean different things at different premium levels.
    #: REVERTED to 20% the same day it was set to 8% (2026-08-05), because
    #: 8% failed in live paper trading within eleven minutes.
    #:
    #: The morning's reasoning was affordability: a 20% stop on a
    #: next-weekly NIFTY ATM risks more than 3% of Rs 30,000, so most
    #: signals were rejected. 8% fixed that. What it also did was put the
    #: stop INSIDE the premium's ordinary noise band — an index option
    #: moves 8% without the underlying doing anything meaningful. Both of
    #: the day's first two trades were stopped out almost immediately:
    #:
    #:   NIFTY11AUG2624550PE  entry 124.65 -> stop 114.25  -Rs   741  2 min
    #:   NIFTY11AUG2624600PE  entry 138.10 -> stop 122.05  -Rs 1,110  4 min
    #:
    #: Backtested the same morning on the recorded NIFTY history, one knob
    #: at a time from the old baseline, all at Rs 30,000 with compounding:
    #:
    #:   stop20 risk1.5 cap25   38 trades   -Rs   378/day
    #:   stop20 risk1.5 cap50   38 trades   -Rs   378/day   (cap alone: no effect)
    #:   stop20 risk3   cap25   40 trades   -Rs   310/day
    #:   stop 8 risk1.5 cap25   43 trades   -Rs   407/day
    #:   stop 8 risk3   cap50   11 trades   -Rs 1,502/day   <- what shipped
    #:
    #: Neither change is harmful alone; together they are. Every row still
    #: ends at the 20% drawdown breaker — the geometry does not decide
    #: WHETHER this loses, only how fast. 8% with 3% risk got there in 11
    #: trades instead of 38.
    #:
    #: The affordability problem is real and is NOT solved by this revert —
    #: it is solved by sizing off live equity (see `te.engine.cycle`) and,
    #: properly, by strike selection that picks a contract the account can
    #: actually carry. Tightening the stop to buy affordability was treating
    #: a sizing problem with a risk-geometry knob.
    paper_cycle_stop_pct: Decimal | None = Decimal(20)
    #: 1:1 with the stop. Measured, not chosen: a barrier sweep over 1,192
    #: labelled firings on NIFTY and SENSEX (2026-04-01 onward) found
    #: expectancy strictly monotonic in the reward:risk ratio, on BOTH
    #: instruments independently —
    #:
    #:   R:R   1.0     1.5     2.0     3.0
    #:   NIFTY +0.056R -0.040R -0.148R -0.250R
    #:   SNSX  +0.052R -0.111R -0.214R -0.273R
    #:
    #: The previous 40% target (1:2) was the second-worst configuration
    #: tested and had nothing behind it — the Zerodha study it came from
    #: specified only the STOP. A +40% option move inside a 3-hour hold is
    #: simply too far: 125 of 555 NIFTY firings died on the clock at 1:2
    #: versus 26 at 1:1.
    #:
    #: This change is justified by the ORDERING, which is consistent across
    #: 4 ratios x 2 instruments with ~0.2R gaps. It is NOT a claim that 1:1
    #: is profitable: pooled z=1.97 against an expected best-of-6 z of 1.89
    #: under pure noise, i.e. indistinguishable from having tried six things.
    #: 1:1 is also the tightest ratio tested, so this is the edge of the
    #: grid rather than a located optimum.
    #: Moves WITH the stop, to keep the 1:1 ratio the table above measured.
    #: The ratio is the finding, not the absolute size — so this went 20 ->
    #: 8 and back to 20 alongside `paper_cycle_stop_pct`, never on its own.
    paper_cycle_target_pct: Decimal | None = Decimal(20)
    #: Trailing distance as a % of entry premium. 15% sits between the 20%
    #: stop and the 40% target: it only starts binding once the trade is
    #: meaningfully in profit, rather than clipping winners in the first
    #: minutes. Deliberately far wider than the 1-3% used for equities —
    #: option premium is several times more volatile in percentage terms,
    #: and ORB's edge is asymmetry (winners must be allowed to run), so an
    #: over-tight trail destroys the strategy rather than protecting it.
    #: DISABLED, to match the configuration that was actually measured.
    #:
    #: The barrier study simulated three exits only — stop, target and time.
    #: It never modelled a trailing stop. Running one live while quoting that
    #: study's expectancy would be reporting a number earned by a different
    #: strategy. With a 1:1 target the trail is also nearly redundant: it
    #: would activate at +20% (`entry + trailing_distance`), which is the
    #: target itself.
    #:
    #: Re-enable only after a sweep that includes the trail as a fourth
    #: barrier, so live and measured behaviour stay the same thing.
    paper_cycle_trailing_pct: Decimal | None = None
    #: The ONE-TIME profit lock — distinct from the (disabled) continuous
    #: trail above. Once a position's profit reaches this % of entry
    #: premium, its stop jumps EXACTLY ONCE to `paper_cycle_profit_lock_
    #: buffer_pct` below the price at that moment (a real profit floor, not
    #: breakeven), then freezes — see `te.domain.geometry.ExitLevels.
    #: profit_lock_activation` and `te.engine.exits.evaluate_position`.
    #:
    #: Backtested 2026-08-04 against 1,305 real historical NIFTY ORB trades
    #: at these exact numbers before being enabled: mean R roughly flat
    #: (-0.104R vs -0.102R with no lock) but win rate up materially (50.7%
    #: vs 46.2%), because it converts some reversals-from-profit into small
    #: wins at the cost of clipping some trades that would have reached the
    #: full target. A deliberate risk-shaping choice — it does not raise
    #: expectancy, and does not fix the underlying strategy's slightly
    #: negative edge. `None` disables the rule (must be set together with
    #: `paper_cycle_profit_lock_buffer_pct`).
    #: RE-ENABLED 2026-08-06 at the values it was measured with, and the
    #: history is kept here because the disable was a mistake in process even
    #: where it was right on the merits.
    #:
    #: It was disabled on 2026-08-05 inside `af4d53e` ("let a Rs 30,000
    #: account actually buy a lot on a normal day"), which moved stop/target
    #: to 8%/8% for affordability. At an 8% target a +15% activation is
    #: UNREACHABLE — the target closes the trade first — so the lock would
    #: have read as enabled, displayed as enabled, and been structurally
    #: incapable of firing even once. Turning it off was the honest response
    #: to that; bundling a live risk-behaviour change into an unrelated
    #: affordability commit, without surfacing it as its own decision, was
    #: not.
    #:
    #: It was NOT rescaled to +5%/2% at the time, and that call stands: the
    #: 15%/5% pair is not an arbitrary shape, it is what was backtested
    #: against 1,305 real NIFTY ORB trades. Scaled-down numbers have never
    #: been measured, and shipping them would quietly claim that study's
    #: result for a rule it never tested.
    #:
    #: The precondition is now gone — stop/target are back at 20%/20%, which
    #: is exactly the geometry the 1,305-trade study used, so +15% is
    #: reachable again and these values describe a rule that was actually
    #: measured. Measured cost of the ten days it spent disabled: ZERO. All
    #: three trades in that window (ids 20-22) peaked at -4.0%, -1.3% and
    #: +2.5% against a +15% trigger, so none would have activated it.
    #:
    #: If stop/target ever move away from 20%/20% again, this pair must move
    #: with them or be disabled again — the activation percentage is only
    #: meaningful relative to the target that can pre-empt it.
    paper_cycle_profit_lock_activation_pct: Decimal | None = Decimal(15)
    paper_cycle_profit_lock_buffer_pct: Decimal | None = Decimal(5)
    #: Reject a resolved contract whose bid-ask spread exceeds this % of LTP.
    #: Live NIFTY chain (2026-07-31) runs 0.1-0.4% through OTM5 and widens to
    #: ~1.1% by OTM8, so 1.0% admits the liquid band and excludes the rest.
    paper_cycle_max_spread_pct: Decimal = Decimal(1)
    #: Reject a contract quoting below this premium — a near-zero premium is a
    #: dead/untraded strike where the fixed ₹40 brokerage alone dominates.
    paper_cycle_min_premium_paise: int = 500
    #: Max ORB entries per underlying per session. The strategy literature
    #: converges on one or two per session (and on stopping for the day after
    #: two stop-outs) as a choppy-day over-trading guard. Distinct from the
    #: edge-triggered breakout detection in `te.strategy.orb`, which is what
    #: actually prevents re-signalling the same crossing.
    paper_cycle_max_entries_per_underlying_per_day: int = 2

    # Automated daily OpenAlgo-app + Angel-broker relogin (te.broker.openalgo_login)
    # — Angel expires its broker session nightly regardless of restarts; ALL
    # FIVE optional so every existing `Settings()` call site (tests included)
    # keeps working unchanged. `_run_openalgo_relogin` simply skips with a
    # logged reason when any is unset, same convention as
    # `paper_cycle_instruments`'s empty-tuple no-op above.
    openalgo_app_username: str | None = None
    openalgo_app_password: SecretStr | None = None
    angel_client_id: str | None = None
    angel_pin: SecretStr | None = None
    angel_totp_secret: SecretStr | None = None

    @field_validator(
        "openalgo_app_username",
        "openalgo_app_password",
        "angel_client_id",
        "angel_pin",
        "angel_totp_secret",
        mode="before",
    )
    @classmethod
    def _blank_relogin_field_is_unset(cls, value: object) -> object:
        """Docker Compose's `${VAR:-}` substitution passes an EMPTY STRING,
        not an unset variable, when `VAR` is missing from `.env` — which
        would otherwise satisfy the `str | None` type as `""` rather than
        falling through to the `None` default, silently defeating
        `run_openalgo_relogin`'s all-or-nothing "not configured" check (it
        would see 5 present-but-blank values and attempt a real login with
        empty credentials instead of skipping)."""
        return None if value == "" else value

    @model_validator(mode="after")
    def _cross_env_guard(self) -> Self:
        """Rejects `env="dev"` with "prod" in `database_url` and vice versa —
        catches transposed dev/prod DB files before they can be used."""
        url_lower = self.database_url.lower()
        if self.env == "dev" and "prod" in url_lower:
            raise ValueError(f"TE_ENV=dev but TE_DATABASE_URL looks like a prod database: {self.database_url!r}")
        if self.env == "prod" and "dev" in url_lower:
            raise ValueError(f"TE_ENV=prod but TE_DATABASE_URL looks like a dev database: {self.database_url!r}")
        return self
