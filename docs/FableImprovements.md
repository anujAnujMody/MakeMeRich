# FableImprovements — Engine Improvement Plan

Date: 2026-07-31. Produced from a code-level review of `engine/te` plus a six-track
web-research fanout (broker execution realities, strategy evidence, options data
sources, ML practice, risk frameworks, retail field reports). Claims below marked
"measured" come from sourced research; anything the research could not confirm is
flagged unverified in the underlying reports.

## Summary

The engine's architecture (shared live/backtest code path, point-in-time gates,
trial ledger, DSR/PBO, meta-labeling with regime features) aligns with best
practice. What is not validated is what the engine measures with:

- The backtest prices options from **index moves** (one delta snapshot, no theta,
  no IV dynamics) — real expired-option data exists cheaply and fixes this.
- The fill model is optimistic: practitioner-measured execution on NIFTY options
  loses ~50–75% of the quoted spread per leg even on limit orders.
- Live risk settings (dashboard-set) are 3–5x looser than professional norms.
- The strategy category (intraday long index options) is one where SEBI's own
  data shows 91–93% of retail participants lose money.
- The best directly-comparable backtest (Zerodha, NIFTY options ORB 2022–2026)
  found a 2-hour opening range beat 15-min, and **selling** the breakout beat
  buying it on every risk metric (6% vs 45% max drawdown).
- Going live has a hard new gate: SEBI's April 2026 framework effectively
  requires a static-IP deployment (whitelisted IP per client, ≤10 orders/sec).

---

## Phase 0 — Safety corrections (days, do first)

1. **Clamp dashboard-settable guardrails server-side** (`te/api` settings →
   `engine_state`). Research norms vs current live values:
   - Per-trade risk ≤ 1.5% (currently 5%)
   - Daily loss limit ≤ 5% of capital (currently 15% — Rs 45,000 on Rs 3L)
   - Drawdown breaker forced on, ≤ 20% (currently 100% = disabled)
   - Position-notional cap ≤ 25% (currently 100% = disabled; one live trade
     held Rs 74,165 of premium ≈ 25% of capital)
   - Risk-of-ruin math: at 5%/trade a 10-loss streak (expected within ~250
     trades at ~50% win rate) is a ~40% drawdown; at 1% it is under 10%.
2. **Set `min_minutes_before_hard_exit`** from the measured time-to-target
   distribution (`scripts/measure_time_to_target.py` exists for this). Closes
   the -Rs 6,672 clock-exit hole (15:05 entry force-closed at 15:20).
3. **Consecutive-loss circuit**: pause new entries for the day after 3 straight
   stop-outs (standard prop-desk behavioral guard; sits next to
   `max_entries_per_underlying_per_day`).
4. **Fix dishonest dashboard numbers**:
   - `currentDrawdownPct` hardcoded 0.0 (`te/api/routers/engine.py:108`) — wire real value
   - `mlStage` hardcoded "shadow" (`te/api/routers/dashboard.py:169`) — wire real ladder state
   - `nextCheckInSeconds` hardcoded 0 — wire real countdown
   - Unify win-rate/avg-win definitions (Performance page = per **day**,
     PnL card = per **trade**; same labels, different numbers)
5. **Secrets**: broker credentials (Angel client ID, PIN, TOTP secret, OpenAlgo
   password/API key) are plaintext in compose env — visible via `docker inspect`.
   Move to a permission-restricted env file outside git; rotate current values.

## Phase 1 — Real option data (single biggest upgrade, ~1–2 weeks)

6. **Ingest Dhan expired-options history**: DhanHQ v2 "Expired Option Contracts"
   API serves up to 5 years of 1-min OHLC + IV + OI for expired NSE index
   options (near-expiry strikes ATM±10), max 30 days/call. Cost: free with ≥25
   trades/30 days on a Dhan account, else ₹499+GST/month. This is the only
   self-serve source found — Zerodha Kite, Angel SmartAPI and Fyers do NOT
   serve expired option contracts. Add an `option_bars` ingest path into the
   Parquet barstore behind the same `bars_asof` gate.
7. **Rebuild the backtest on real premiums**: replace the index-as-premium proxy
   in `te/backtest/engine.py` (`_last_close_premium`) and the single-snapshot
   delta conversion in `te/ml/barriers.py`. Re-derive every existing conclusion
   (1:1 target choice, expectancy tables) — theta and IV dynamics are currently
   invisible and bias all results optimistic.
8. **Upgrade the fill model**: replace flat 2bps slippage
   (`te/backtest/fills.py:31`) with spread-crossing fills. Measured practitioner
   data (~60k live NIFTY/BANKNIFTY option orders): ATM spread ~Rs 12–16/lot;
   limit orders capture only ~48% of the spread; avg fill time 3.3s; systematic
   adverse selection between signal and fill. Heuristic starting point: charge
   ~75% of spread for a single leg (single-source; calibrate from own data).
   The `slippage_observations` table already exists — record live paper
   spread-vs-fill and calibrate from it. Never fill at mid or bar-close raw.
9. **External cross-check**: run the same strategy on AlgoTest (25 free
   backtests/week) and diff trade lists — divergence isolates pricing bias
   from logic bugs.

## Phase 2 — Live-readiness hardening (~1–2 weeks, before any real order)

10. **Quote batching**: Angel SmartAPI quote endpoint is now 1 request/sec with
    50-symbol bulk fetch. The scheduler's per-symbol quote loop must batch;
    add client-side throttling (extend `te/broker/ratelimit.py`) — forum
    reports say Angel's own rate-limit enforcement is inconsistent.
11. **Order lifecycle realism** in `ExecutionManager`:
    - Freeze-quantity chunking: NIFTY 1,800 qty/order (Feb 2026); BANKNIFTY
      600 vs 900 conflicts across sources — verify against the live NSE
      circular before hardcoding; SENSEX/BANKEX figures unverified.
    - Partial-fill states (paper assumes all-or-nothing instant fills).
    - Idempotency keys on order submissions (retry-safe).
    - Reconciliation loop: engine positions vs broker positions every 1–5 min;
      halt new entries on mismatch.
12. **Watchdog + halt semantics**:
    - Independent heartbeat process: engine dead → alert (Telegram) + cancel
      working orders.
    - Stale-quote detection threshold well under the 1-min stop cadence.
    - Distinct "exchange halted" state (market-wide circuit breaker at
      10/15/20% freezes options too): suspend entries, do NOT force-flatten
      into a frozen book.
13. **Hard exit before broker RMS**: Angel force-squares intraday F&O around
    15:20 (their fill, plus fees). Current `hard_exit_by` = 15:20 collides with
    it — move to 15:10–15:15.
14. **Compliance (SEBI Feb 2025 circular, in force Apr 1 2026)**:
    - Static-IP deployment effectively mandatory (whitelisted IP, one backup
      IP, changeable weekly). Home broadband is brittle (CG-NAT); a Mumbai
      VPS with static IP (~₹300–1,500/mo) is the robust path.
    - Stay under 10 orders/sec per exchange-segment → no algo registration
      needed for personal use (inference from secondary sources; read the
      primary circular before going live).
    - LIMIT-only orders (already the engine's behavior; plain market orders
      are restricted for algo flow anyway).
15. **Event calendar**: wire the exchange holiday list (known gap: "NSE holiday
    list not wired up yet") and an event stand-down/size-down list (RBI MPC,
    Union Budget, Fed/CPI nights — measured VIX spikes around each). Then
    measure ORB expectancy on event vs normal days from own labels; no
    rigorous external study exists.

## Phase 3 — Strategy direction (after Phase 1 data lands)

16. **Re-test ORB on real option premiums**, through the trial ledger with
    DSR/PBO gates:
    - 2-hour opening range variant (the one config with directly relevant
      supporting evidence).
    - Trend-alignment filter (folklore tier — test, don't assume).
    - Futures volume as breakout confirmation, replacing the vacuous index
      volume filter (indices report zero volume; the condition currently
      filters nothing).
    - Morning-only entry windows; expiry-day handling (evidence mixed).
    - Context: the best-evidenced ORB result (Zarattini/Aziz, Sharpe 2.81, US
      equities) derives its entire edge from a stocks-in-play relative-volume
      filter that has no single-index equivalent — naive index ORB sits
      outside the evidenced edge.
17. **Confront buy-vs-sell**: same breakout signal sold as a defined-risk
    credit spread showed 6% max drawdown vs 45% buying, green every tested
    year (single practitioner backtest, medium confidence). Requires engine
    support for multi-leg orders, spread margin, leg-risk management. Rs 3L
    supports 1–2 defined-risk lots (iron condor margin ≈ Rs 44k in the
    Varsity example). Owner decision; evidence points here.
18. **Sizing upgrades**: quarter-to-half Kelly from calibrated model
    probabilities once the meta-labeler matures; volatility-scaled sizing
    (smaller on high-VIX days).

## Phase 4 — ML refinements (ongoing, low urgency)

19. **Verify purge/embargo uses barrier resolution time**, not trigger time —
    the most common triple-barrier leakage bug. Add an explicit test.
20. **Add CPCV** as a second honesty gate beside purged walk-forward
    (peer-reviewed evidence of lower PBO / higher DSR). Keep PBO ≤ 0.5 as the
    hard deploy line (traceable to Bailey & Borwein).
21. **Calibrate probabilities** (Platt over isotonic at these sample sizes)
    before using them for bet sizing.
22. **Candidate features to test through the gates** (hypothesis tier, no
    published effect sizes): GIFT Nifty overnight gap, near-month futures
    basis / OI change at open, opening-range percentile vs trailing
    distribution.
23. **Numeric shadow gate**: ~100–200 shadow trades before advisory promotion
    (heuristic tier; better than undefined).

## Sequence

Phase 0 this week → Phase 1 next (it invalidates-or-confirms everything else)
→ Phase 2 in parallel once the backfill is running → Phases 3–4 after the
re-derived backtest exists.

## Standing caveat

Nothing found in the literature demonstrates intraday long-option buying on
Indian indices is net-profitable after 2026-era costs (SEBI: 91–93% of retail
F&O participants lose). Phase 1 is what lets this engine answer that question
credibly for itself — which is exactly what it was built to ask.
