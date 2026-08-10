"""Session audit — checks what the day ACTUALLY did against what the engine
claims to enforce.

Why this module exists, stated plainly because the reason is the design:

On 2026-08-07 the paper engine took **6** entries against a `max_trades_per_day`
of 3, ran **6** straight losses against a `max_consecutive_losses` stand-down of
3, lost **Rs 5,612** against a Rs 2,500 daily cap, and let a single trade lose
**Rs 1,435** against a Rs 700 per-trade cap. The suite was green — 1,535 passed.

Every one of those rules had a test. Every one of those tests built its fixture
by inserting a CLOSED `TradeRow`, which is exactly the case the buggy code
handled correctly. The code counted closed trades and called them "trades
today"; the tests counted closed trades and called them "trades today". Both
held the same wrong idea, so they agreed with each other and the suite stayed
green while the engine broke four of its own limits in one session.

That is the gap this module fills. A test asks "does the code do what I think it
does" — and inherits whatever I think. This asks a different question: **"did the
day obey the rules"**, answered only from the rows the day left behind. It needs
nobody to have imagined the failure in advance, which is precisely where the unit
tests kept failing.

------------------------------------------------------------------------------
THE LOAD-BEARING RULE — an audit must not call the code it audits
------------------------------------------------------------------------------

This module must NEVER import the engine's own counting helpers:
`te.risk.limits`, or the count/aggregate functions in
`te.persistence.repos.paper_trading` (`trades_count_today`,
`underlying_entries_today`, `open_positions_count`, ...).

It re-derives every number from raw ORM rows. An audit that reuses the helper
it is auditing will faithfully reproduce that helper's bug and report "all
clear" — which is exactly how 2026-08-07 stayed invisible. Independence is not
a stylistic preference here, it is the entire mechanism.

`tests/audit/test_audit_is_independent.py` enforces this by parsing this file's
import statements. If that test ever gets in the way, the correct response is
to stop, not to relax it.

------------------------------------------------------------------------------
WHERE THE NUMBERS COME FROM
------------------------------------------------------------------------------

Two tables, joined on `client_order_id`, and the choice matters:

* `open_positions` is the ENTRY ledger. A row is written the moment a position
  opens and is never deleted — closing only stamps `closed_at`. So it holds
  every entry the day made, open or closed. This is what "how many trades did
  we take today" actually means.
* `trades` is the EXIT ledger. A row appears only when a position CLOSES, and
  it carries the realised gross/costs/net.

Counting entries from `trades` is the original bug: a position opened at 11:20
and still open at 12:17 is simply absent, so three guards read "2 trades" while
five were live. Counting from `open_positions` cannot have that failure mode,
because the row exists from the instant of entry.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from te.domain.clock import IST
from te.persistence.models import OpenPositionRow, RiskEventRow, TradeRow

#: A rule the engine claims to enforce was broken. This is the severity that
#: means "the engine did something it promised it would not do".
VIOLATION = "violation"
#: Something the day did that is legal but worth a human's eye — a missing
#: broker value, a suspicious fill. Never used for a broken guarantee.
WARNING = "warning"

Severity = Literal["violation", "warning"]


@dataclass(frozen=True)
class AuditLimits:
    """The rules being checked. Passed in rather than read here, so the caller
    is explicit about WHICH limits it is auditing against.

    Note honestly: these are the limits in force *now*, not necessarily the
    ones in force on the audited day — guardrails are live-editable and the
    engine keeps no per-day history of them. `SessionAudit.limits_note` says so
    in the report rather than letting the reader assume otherwise. For an audit
    run at 15:35 on the day itself (the scheduled case) they are the same
    thing.
    """

    max_trades_per_day: int
    max_concurrent_positions: int
    max_daily_loss_paise: int
    max_consecutive_losses: int
    max_entries_per_underlying_per_day: int
    #: `None` when no rupee cap is configured (the older percentage geometries).
    max_loss_per_trade_paise: int | None
    max_lots: int | None


@dataclass(frozen=True)
class AuditFinding:
    rule: str
    severity: Severity
    expected: str
    actual: str
    detail: str


@dataclass(frozen=True)
class EntryFact:
    """One entry the day made — joined from its `open_positions` row (always
    present) and its `trades` row (present only once it closed)."""

    client_order_id: str
    symbol: str
    underlying: str
    lots: int
    lot_size: int
    entry_paise: int
    stop_paise: int
    opened_at: dt.datetime
    closed_at: dt.datetime | None
    exit_paise: int | None
    gross_paise: int | None
    costs_paise: int | None
    net_paise: int | None
    exit_reason: str | None

    @property
    def quantity(self) -> int:
        return self.lots * self.lot_size

    @property
    def is_closed(self) -> bool:
        return self.net_paise is not None


@dataclass(frozen=True)
class SessionAudit:
    on: dt.date
    checked_at: dt.datetime
    limits: AuditLimits
    limits_note: str
    entries: list[EntryFact]
    findings: list[AuditFinding] = field(default_factory=list)

    @property
    def violations(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.severity == VIOLATION]

    @property
    def warnings(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def clean(self) -> bool:
        return not self.violations

    @property
    def net_paise(self) -> int:
        return sum(e.net_paise or 0 for e in self.entries)


def ist_day_bounds_utc(on: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Half-open `[start, end)` in UTC for the IST calendar day `on`.

    Deliberately not modelled on `te.persistence.repos.paper_trading._day_bounds`,
    which builds its bounds by attaching `tzinfo=dt.UTC` to an IST date and
    closes the interval at `time.max`. That is wrong twice over — it treats an
    IST calendar date as a UTC one (harmless only because NSE/BSE hours,
    03:45-10:00 UTC, never straddle a UTC midnight) and its closed upper bound
    drops the final microsecond. Neither bug can bite an audit that uses this
    instead.
    """
    start_ist = dt.datetime.combine(on, dt.time.min, tzinfo=IST)
    return start_ist.astimezone(dt.UTC), (start_ist + dt.timedelta(days=1)).astimezone(dt.UTC)


def _underlying_of(symbol: str) -> str:
    """The index a contract belongs to, by longest-prefix match.

    Longest-first matters: `BANKNIFTY25AUG26...` starts with `BANKNIFTY` but
    ALSO would match a naive `NIFTY` substring test, and BANKEX/SENSEX share no
    prefix but are easy to get wrong the same way if this is ever extended.
    """
    for base in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "BANKEX", "SENSEX"):
        if symbol.startswith(base):
            return base
    return symbol


def collect_entries(session: Session, on: dt.date) -> list[EntryFact]:
    """Every entry opened during the IST day `on`, with its exit joined in.

    Reads `open_positions` (the entry ledger) as the driving table — see this
    module's docstring for why counting from `trades` is the bug being audited
    for.
    """
    start, end = ist_day_bounds_utc(on)
    rows = (
        session.execute(
            select(OpenPositionRow)
            .where(OpenPositionRow.opened_at >= start, OpenPositionRow.opened_at < end)
            .order_by(OpenPositionRow.opened_at)
        )
        .scalars()
        .all()
    )
    if not rows:
        return []

    coids = [r.client_order_id for r in rows]
    trades = {
        t.client_order_id: t
        for t in session.execute(select(TradeRow).where(TradeRow.client_order_id.in_(coids))).scalars().all()
    }

    facts: list[EntryFact] = []
    for row in rows:
        trade = trades.get(row.client_order_id)
        facts.append(
            EntryFact(
                client_order_id=row.client_order_id,
                symbol=row.symbol,
                underlying=_underlying_of(row.symbol),
                lots=row.lots,
                lot_size=row.lot_size,
                entry_paise=row.entry_premium_paise,
                stop_paise=row.stop_paise,
                opened_at=row.opened_at,
                closed_at=row.closed_at,
                exit_paise=trade.exit_premium_paise if trade else None,
                gross_paise=trade.gross_pnl_paise if trade else None,
                costs_paise=trade.costs_paise if trade else None,
                net_paise=trade.net_pnl_paise if trade else None,
                exit_reason=trade.exit_reason if trade else None,
            )
        )
    return facts


def peak_concurrent(entries: Sequence[EntryFact], *, day_end: dt.datetime) -> int:
    """Most positions held at once, by sweep line over open/close instants.

    A still-open position is treated as open until `day_end` rather than
    skipped — an unclosed position is exposure, and pretending it is not is how
    a concurrency breach would hide.
    """
    events: list[tuple[dt.datetime, int]] = []
    for e in entries:
        events.append((e.opened_at, 1))
        events.append((e.closed_at or day_end, -1))
    # Closes before opens at an identical timestamp: a position that closes in
    # the same instant another opens never actually overlapped it, and counting
    # it as overlap would manufacture a breach that did not happen.
    events.sort(key=lambda ev: (ev[0], ev[1]))
    peak = running = 0
    for _, delta in events:
        running += delta
        peak = max(peak, running)
    return peak


def longest_loss_streak(entries: Sequence[EntryFact]) -> int:
    """Longest run of consecutive losing trades, ordered by when they CLOSED.

    Close order, not open order: a stand-down rule is about the sequence of
    outcomes as they became known. Breakeven (net == 0) resets the streak, the
    same convention `check_consecutive_losses` documents — matched deliberately
    so a difference here would mean a real disagreement, not a definitional one.
    """
    closed = sorted((e for e in entries if e.is_closed), key=lambda e: e.closed_at or dt.datetime.max)
    best = run = 0
    for e in closed:
        if (e.net_paise or 0) < 0:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _rupees(paise: int) -> str:
    return f"Rs {paise / 100:,.2f}"


def audit_session(
    session: Session,
    *,
    on: dt.date,
    limits: AuditLimits,
    now: dt.datetime,
    limits_note: str = "limits read live at audit time, not as-of the audited day",
) -> SessionAudit:
    """The whole audit. Pure with respect to the DB — reads only, writes nothing."""
    entries = collect_entries(session, on)
    _, day_end = ist_day_bounds_utc(on)
    findings: list[AuditFinding] = []

    def add(rule: str, severity: Severity, expected: str, actual: str, detail: str) -> None:
        findings.append(AuditFinding(rule=rule, severity=severity, expected=expected, actual=actual, detail=detail))

    # --- Rule 1: entries per day ------------------------------------------
    # THE 2026-08-07 breach. Counted from the entry ledger, so a position still
    # open at the moment of counting is included.
    if len(entries) > limits.max_trades_per_day:
        add(
            "max_trades_per_day",
            VIOLATION,
            f"<= {limits.max_trades_per_day} entries",
            f"{len(entries)} entries",
            "the engine opened more positions than its own per-day cap allows; "
            f"entries at {', '.join(e.opened_at.astimezone(IST).strftime('%H:%M') for e in entries)} IST",
        )

    # --- Rule 2: per-underlying entries -----------------------------------
    per_underlying: dict[str, int] = {}
    for e in entries:
        per_underlying[e.underlying] = per_underlying.get(e.underlying, 0) + 1
    for underlying, count in sorted(per_underlying.items()):
        if count > limits.max_entries_per_underlying_per_day:
            add(
                "max_entries_per_underlying_per_day",
                VIOLATION,
                f"<= {limits.max_entries_per_underlying_per_day} on {underlying}",
                f"{count} on {underlying}",
                f"{underlying} was traded {count} times in one session",
            )

    # --- Rule 3: concurrent positions -------------------------------------
    peak = peak_concurrent(entries, day_end=day_end)
    if peak > limits.max_concurrent_positions:
        add(
            "max_concurrent_positions",
            VIOLATION,
            f"<= {limits.max_concurrent_positions} at once",
            f"{peak} at once",
            "more positions were held simultaneously than the cap allows",
        )

    # --- Rule 4: consecutive losses ---------------------------------------
    streak = longest_loss_streak(entries)
    if streak > limits.max_consecutive_losses:
        add(
            "max_consecutive_losses",
            VIOLATION,
            f"<= {limits.max_consecutive_losses} in a row",
            f"{streak} in a row",
            "the losing-streak stand-down did not stop new entries",
        )

    # --- Rule 5: daily loss -----------------------------------------------
    net = sum(e.net_paise or 0 for e in entries)
    if net < -limits.max_daily_loss_paise:
        add(
            "max_daily_loss",
            VIOLATION,
            f">= -{_rupees(limits.max_daily_loss_paise)}",
            _rupees(net),
            "the day's realised net loss finished beyond the daily cap",
        )

    # --- Rule 6: per-trade loss, GROSS and NET separately -----------------
    # Two rules, not one, because they fail for different reasons and the
    # difference is the point. `RupeeRiskGeometry` divides the rupee cap by
    # quantity to get a PRICE distance, so the cap it enforces is a gross price
    # move. Costs are charged on top and were never part of the arithmetic, so
    # a "Rs 700 stop" reliably nets a Rs 760-860 loss. Reporting only the net
    # rule would read as "the stop is broken"; reporting only the gross rule
    # would hide the gap the owner actually cares about.
    if limits.max_loss_per_trade_paise is not None:
        cap = limits.max_loss_per_trade_paise
        for e in entries:
            if not e.is_closed or (e.net_paise or 0) >= 0:
                continue
            gross_loss = -(e.gross_paise or 0)
            net_loss = -(e.net_paise or 0)
            if gross_loss > cap:
                add(
                    "max_loss_per_trade_gross",
                    VIOLATION,
                    f"<= {_rupees(cap)} of price move",
                    _rupees(gross_loss),
                    f"{e.symbol}: the price moved further against the position than the stop allowed "
                    f"(entry {e.entry_paise}p, stop {e.stop_paise}p, exit {e.exit_paise}p) — "
                    "the stop was crossed between two exit checks, not respected at the level",
                )
            if net_loss > cap:
                add(
                    "max_loss_per_trade_net",
                    VIOLATION,
                    f"<= {_rupees(cap)} net",
                    _rupees(net_loss),
                    f"{e.symbol}: gross {_rupees(gross_loss)} + costs {_rupees(e.costs_paise or 0)}; "
                    "the rupee cap is applied as a price distance and does not include costs",
                )

    # --- Rule 7: the fill was not worse than the stop level ---------------
    # Separates "the stop level was wrong" from "the fill was late". Every
    # position here is long an option, so a fill BELOW the stop is slippage
    # past it. Quantifies exactly what a faster exit loop would recover.
    for e in entries:
        if e.exit_reason != "stop" or e.exit_paise is None:
            continue
        if e.exit_paise < e.stop_paise:
            slip = (e.stop_paise - e.exit_paise) * e.quantity
            add(
                "stop_fill_slippage",
                WARNING,
                f"fill at or above the stop ({e.stop_paise}p)",
                f"filled at {e.exit_paise}p",
                f"{e.symbol}: {_rupees(slip)} lost below the stop level because the price crossed it "
                "between two exit checks",
            )

    # --- Rule 8: position sizing ------------------------------------------
    for e in entries:
        if e.lots < 1:
            add(
                "lots_at_least_one",
                VIOLATION,
                ">= 1 lot",
                f"{e.lots} lots",
                f"{e.symbol}: a zero-lot position is not a trade and must never reach the position store",
            )
        if limits.max_lots is not None and e.lots > limits.max_lots:
            add(
                "max_lots",
                VIOLATION,
                f"<= {limits.max_lots} lots",
                f"{e.lots} lots",
                f"{e.symbol}: sized above the lot ceiling, which multiplies the intended rupee risk",
            )

    # --- Rule 9: P&L arithmetic -------------------------------------------
    # net == gross - costs is an identity, not a judgement. If it ever fails,
    # a reported P&L is fabricated somewhere and nothing else in this report
    # can be trusted.
    for e in entries:
        if not e.is_closed:
            continue
        expected_net = (e.gross_paise or 0) - (e.costs_paise or 0)
        if expected_net != e.net_paise:
            add(
                "pnl_arithmetic",
                VIOLATION,
                f"net == gross - costs ({expected_net}p)",
                f"{e.net_paise}p",
                f"{e.symbol}: the stored net does not equal gross minus costs — a P&L number is fabricated",
            )
        if (e.costs_paise or 0) <= 0:
            add(
                "costs_charged",
                VIOLATION,
                "> 0 paise of costs",
                f"{e.costs_paise}p",
                f"{e.symbol}: a real round trip always costs something; zero means the cost model did not run",
            )

    # --- Rule 10: every entry carried a usable stop ------------------------
    for e in entries:
        if not (0 < e.stop_paise < e.entry_paise):
            add(
                "entry_has_working_stop",
                VIOLATION,
                f"0 < stop < entry ({e.entry_paise}p)",
                f"stop {e.stop_paise}p",
                f"{e.symbol}: a stop at or above entry fires instantly; at or below zero it can never fire",
            )

    # --- Rule 11: nothing left open past its hard exit --------------------
    for e in entries:
        if e.closed_at is None:
            add(
                "closed_by_session_end",
                VIOLATION,
                "closed before the session ended",
                "still open",
                f"{e.symbol}: an intraday position carried past the close is unintended overnight exposure",
            )

    # --- Rule 12: a halt actually stopped new entries ----------------------
    # The halt is the last line of defence; if it fires and entries continue,
    # nothing protects the account at all.
    start, end = ist_day_bounds_utc(on)
    halts = (
        session.execute(
            select(RiskEventRow)
            .where(RiskEventRow.ts >= start, RiskEventRow.ts < end, RiskEventRow.kind == "daily_loss_halt")
            .order_by(RiskEventRow.ts)
        )
        .scalars()
        .all()
    )
    if halts:
        first_halt = halts[0].ts
        after = [e for e in entries if e.opened_at > first_halt]
        if after:
            add(
                "no_entries_after_halt",
                VIOLATION,
                "no entries after the halt",
                f"{len(after)} entries after it",
                f"the daily-loss halt fired at {first_halt.astimezone(IST).strftime('%H:%M')} IST "
                "and the engine kept opening positions",
            )

    # --- Rule 13: broker values the engine had to work around -------------
    odd = (
        session.execute(
            select(RiskEventRow)
            .where(RiskEventRow.ts >= start, RiskEventRow.ts < end, RiskEventRow.kind != "daily_loss_halt")
            .order_by(RiskEventRow.ts)
        )
        .scalars()
        .all()
    )
    for event in odd:
        add(
            f"risk_event:{event.kind}",
            WARNING,
            "no risk event",
            event.kind,
            event.detail,
        )

    return SessionAudit(
        on=on,
        checked_at=now,
        limits=limits,
        limits_note=limits_note,
        entries=entries,
        findings=findings,
    )


def format_report(audit: SessionAudit) -> str:
    """Plain-text report. Read by a human at 15:35, so it leads with the verdict."""
    lines: list[str] = []
    day = audit.on.isoformat()
    lines.append(f"SESSION AUDIT  {day}")
    lines.append("=" * 78)

    if not audit.entries:
        lines.append("no entries — nothing to audit")
        return "\n".join(lines)

    verdict = "CLEAN" if audit.clean else f"{len(audit.violations)} VIOLATION(S)"
    lines.append(f"verdict: {verdict}    entries: {len(audit.entries)}    net: {_rupees(audit.net_paise)}")
    lines.append(f"note: {audit.limits_note}")
    lines.append("")

    lines.append("entries")
    lines.append("-" * 78)
    lines.append(f"{'time':>6}  {'symbol':26} {'lots':>4} {'net Rs':>10}  {'reason':8}")
    for e in audit.entries:
        net = f"{(e.net_paise or 0) / 100:,.2f}" if e.is_closed else "open"
        lines.append(
            f"{e.opened_at.astimezone(IST).strftime('%H:%M'):>6}  {e.symbol:26} {e.lots:>4} {net:>10}  "
            f"{e.exit_reason or '-':8}"
        )
    lines.append("")

    if audit.violations:
        lines.append("VIOLATIONS — the engine broke a rule it claims to enforce")
        lines.append("-" * 78)
        for f in audit.violations:
            lines.append(f"  [{f.rule}]")
            lines.append(f"      expected: {f.expected}")
            lines.append(f"      actual:   {f.actual}")
            lines.append(f"      {f.detail}")
        lines.append("")

    if audit.warnings:
        lines.append("warnings")
        lines.append("-" * 78)
        for f in audit.warnings:
            lines.append(f"  [{f.rule}] {f.actual} — {f.detail}")
        lines.append("")

    if audit.clean:
        lines.append("every checked rule held.")
    return "\n".join(lines)
