"""`te.audit.session_audit` — does the audit actually catch a broken day?

The load-bearing test in this file is
`test_a_position_still_open_is_counted_as_an_entry`. It is the exact bug that
cost Rs 5,612 on 2026-08-07: three separate risk guards counted CLOSED trades
and called the result "trades today", so five live positions read as two and
the caps never fired. Every unit test those guards had passed, because every
one of them inserted a closed trade — the case the buggy code got right.

So this file's fixtures deliberately build the OPPOSITE shape: entries that
have not closed yet. If the audit is ever rewritten to count from `trades`,
that one test dies immediately.

`test_replays_the_real_2026_08_07_session` is the other anchor — the real six
entries, with the real premiums and real costs, taken from the live database.
Synthetic fixtures inherit whatever the author believed; a recorded day does
not.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.audit.session_audit import (
    VIOLATION,
    AuditLimits,
    audit_session,
    collect_entries,
    format_report,
    ist_day_bounds_utc,
    longest_loss_streak,
    peak_concurrent,
)
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, OpenPositionRow, RiskEventRow, TradeRow

ON = dt.date(2026, 8, 7)
NOW = dt.datetime(2026, 8, 7, 15, 35, tzinfo=dt.UTC)


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _limits(**overrides: object) -> AuditLimits:
    defaults: dict[str, object] = {
        "max_trades_per_day": 3,
        "max_concurrent_positions": 5,
        "max_daily_loss_paise": 250_000,
        "max_consecutive_losses": 3,
        "max_entries_per_underlying_per_day": 2,
        "max_loss_per_trade_paise": 70_000,
        "max_lots": 1,
    }
    defaults.update(overrides)
    return AuditLimits(**defaults)  # type: ignore[arg-type]


def _utc(hh: int, mm: int) -> dt.datetime:
    return dt.datetime(2026, 8, 7, hh, mm, tzinfo=dt.UTC)


def _entry(
    session,  # noqa: ANN001
    *,
    coid: str,
    symbol: str = "NIFTY11AUG2624550PE",
    lots: int = 1,
    lot_size: int = 65,
    entry: int = 12_130,
    stop: int = 11_054,
    opened_at: dt.datetime,
    # Exit fields. `closed_at=None` means the position is STILL OPEN — the
    # case the whole audit exists for, so it is the default nothing.
    closed_at: dt.datetime | None = None,
    exit_premium: int | None = None,
    costs: int = 6_436,
    exit_reason: str = "stop",
) -> None:
    """Writes the entry-ledger row, and the exit-ledger row only if it closed.

    Mirrors what the engine really does: `open_positions` gets a row at entry
    and keeps it forever; `trades` gets one only at close.
    """
    session.add(
        OpenPositionRow(
            client_order_id=coid,
            symbol=symbol,
            exchange="NFO",
            strategy="orb",
            direction="long_put",
            lots=lots,
            lot_size=lot_size,
            entry_premium_paise=entry,
            stop_paise=stop,
            current_stop_paise=stop,
            target_paise=entry * 2,
            max_hold_seconds=10_800,
            hard_exit_by="15:15:00",
            opened_at=opened_at,
            closed_at=closed_at,
        )
    )
    if closed_at is None:
        return
    assert exit_premium is not None, "a closed entry needs an exit price"
    gross = (exit_premium - entry) * lots * lot_size
    session.add(
        TradeRow(
            client_order_id=coid,
            symbol=symbol,
            exchange="NFO",
            strategy="orb",
            direction="long_put",
            lots=lots,
            lot_size=lot_size,
            entry_premium_paise=entry,
            exit_premium_paise=exit_premium,
            gross_pnl_paise=gross,
            costs_paise=costs,
            net_pnl_paise=gross - costs,
            exit_reason=exit_reason,
            mode="paper",
            opened_at=opened_at,
            closed_at=closed_at,
            stop_paise=stop,
        )
    )


def _rules(audit) -> list[str]:  # noqa: ANN001
    return [f.rule for f in audit.findings]


def _violated(audit) -> list[str]:  # noqa: ANN001
    return [f.rule for f in audit.violations]


# --- the bug this module exists for -----------------------------------------


def test_a_position_still_open_is_counted_as_an_entry(session_factory) -> None:  # noqa: ANN001
    """THE test. Three closed trades and one STILL OPEN, against a limit of 3.

    The engine's own `check_max_trades_per_day` reads `closed_at` and would
    see 3 here — at or under the limit, no complaint. The day really took 4.

    That single distinction is what let 2026-08-07 run to 6 entries on a cap of
    3: because every stop resolved in 15-30 minutes, the closed-count always
    trailed the live-count, and each new entry was waved through against a
    stale number.
    """
    with session_factory() as session:
        for i, (opened, closed) in enumerate(
            [(_utc(4, 0), _utc(4, 30)), (_utc(5, 0), _utc(5, 30)), (_utc(6, 0), _utc(6, 30))]
        ):
            _entry(session, coid=f"closed-{i}", opened_at=opened, closed_at=closed, exit_premium=11_000)
        _entry(session, coid="still-open", opened_at=_utc(7, 0))
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert "max_trades_per_day" in _violated(audit)
    breach = next(f for f in audit.findings if f.rule == "max_trades_per_day")
    assert breach.actual == "4 entries", (
        f"counted {breach.actual} — an open position was skipped, which is exactly the 2026-08-07 bug"
    )


def test_four_closed_entries_also_breach_so_the_test_above_is_about_openness(
    session_factory,  # noqa: ANN001
) -> None:
    """Control for the test above. If this one failed too, the previous test
    would prove nothing about open-vs-closed — only that 4 > 3."""
    with session_factory() as session:
        for i in range(4):
            _entry(
                session,
                coid=f"c-{i}",
                opened_at=_utc(4 + i, 0),
                closed_at=_utc(4 + i, 30),
                exit_premium=11_000,
            )
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert next(f for f in audit.findings if f.rule == "max_trades_per_day").actual == "4 entries"


def test_three_entries_against_a_limit_of_three_is_clean(session_factory) -> None:  # noqa: ANN001
    """The boundary. `>` not `>=`, or a compliant day reports a violation and
    the whole report becomes noise a human learns to ignore."""
    with session_factory() as session:
        for i in range(3):
            _entry(
                session,
                coid=f"c-{i}",
                symbol=f"{'NIFTY' if i == 0 else 'SENSEX' if i == 1 else 'BANKNIFTY'}11AUG2624550PE",
                opened_at=_utc(4 + i, 0),
                closed_at=_utc(4 + i, 30),
                exit_premium=12_500,  # a winner, so no loss rules fire either
            )
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert audit.clean, f"a compliant day reported violations: {_violated(audit)}"


# --- the recorded day --------------------------------------------------------

#: The real 2026-08-07 session, copied from the live database. Columns:
#: symbol, lot size, entry, stop, exit, costs, opened, closed (all UTC).
REAL_SESSION = [
    ("BANKNIFTY25AUG2657800PE", 30, 61_570, 59_237, 59_010, 8_951, _utc(5, 35), _utc(5, 51)),
    ("SENSEX13AUG2678500PE", 20, 50_505, 47_005, 46_900, 6_906, _utc(5, 35), _utc(5, 55)),
    ("BANKNIFTY25AUG2657800PE", 30, 59_475, 57_142, 54_980, 8_691, _utc(5, 50), _utc(8, 17)),
    ("SENSEX13AUG2678500PE", 20, 49_500, 46_000, 45_555, 6_849, _utc(6, 43), _utc(7, 17)),
    ("NIFTY11AUG2624550PE", 65, 12_130, 11_054, 10_900, 6_436, _utc(6, 47), _utc(7, 5)),
    ("NIFTY11AUG2624550PE", 65, 11_490, 10_414, 10_345, 6_347, _utc(6, 56), _utc(7, 17)),
]


def _load_real_session(session) -> None:  # noqa: ANN001
    for i, (symbol, lot_size, entry, stop, exit_p, costs, opened, closed) in enumerate(REAL_SESSION):
        _entry(
            session,
            coid=f"real-{i}",
            symbol=symbol,
            lot_size=lot_size,
            entry=entry,
            stop=stop,
            opened_at=opened,
            closed_at=closed,
            exit_premium=exit_p,
            costs=costs,
        )


def test_replays_the_real_2026_08_07_session(session_factory) -> None:  # noqa: ANN001
    """The day that prompted all of this, replayed from the live rows.

    Asserts the specific totals rather than "some violation happened", because
    a report that fires on the right day for the wrong reason is worthless.
    """
    with session_factory() as session:
        _load_real_session(session)
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert len(audit.entries) == 6
    assert audit.net_paise == -561_205, "the day's real net was Rs 5,612.05 down"

    violated = _violated(audit)
    assert "max_trades_per_day" in violated
    assert "max_consecutive_losses" in violated
    assert "max_daily_loss" in violated
    # Every one of the six lost more than the Rs 700 cap, net.
    assert violated.count("max_loss_per_trade_net") == 6
    # Gross too — so this is not merely the cost gap; the stop level itself
    # was overshot on all six.
    assert violated.count("max_loss_per_trade_gross") == 6


def test_the_worst_trade_is_attributed_to_late_exit_not_a_wrong_stop(
    session_factory,  # noqa: ANN001
) -> None:
    """The Rs 1,435 BANKNIFTY loss against a Rs 700 cap.

    Its stop was set correctly (2,333 paise x 30 = Rs 700). The premium fell
    from 600.00 to a low of 545.35 inside the 13:46 IST minute and the engine,
    checking once a minute, filled at 549.80 — Rs 648.60 below its own stop.

    The audit must say WHICH of those two things went wrong, because the fixes
    are opposite: a wrong stop level means fix the geometry, a late fill means
    check more often. Reporting only "lost too much" leaves that undecidable.
    """
    with session_factory() as session:
        _load_real_session(session)
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    slippage = [f for f in audit.findings if f.rule == "stop_fill_slippage"]
    worst = max(slippage, key=lambda f: float(f.detail.split("Rs ")[1].split(" ")[0].replace(",", "")))
    assert "648.60" in worst.detail
    # And the stop itself was sound — no entry is flagged for a broken stop.
    assert "entry_has_working_stop" not in _violated(audit)


# --- individual rules --------------------------------------------------------


def test_peak_concurrent_counts_an_unclosed_position_as_held_to_day_end(
    session_factory,  # noqa: ANN001
) -> None:
    """An open position is exposure. Skipping it would let the worst case —
    positions accumulating because nothing closes them — audit as clean."""
    with session_factory() as session:
        _entry(session, coid="a", opened_at=_utc(4, 0))
        _entry(session, coid="b", opened_at=_utc(5, 0))
        _entry(session, coid="c", opened_at=_utc(6, 0))
        session.commit()

    with session_factory() as session:
        entries = collect_entries(session, ON)
    _, day_end = ist_day_bounds_utc(ON)
    assert peak_concurrent(entries, day_end=day_end) == 3


def test_a_close_at_the_same_instant_as_an_open_is_not_an_overlap(
    session_factory,  # noqa: ANN001
) -> None:
    """Ordering at equal timestamps. The engine's cycle closes and opens
    within one tick, so counting these as simultaneous would manufacture a
    breach that never happened."""
    with session_factory() as session:
        _entry(session, coid="a", opened_at=_utc(4, 0), closed_at=_utc(5, 0), exit_premium=11_000)
        _entry(session, coid="b", opened_at=_utc(5, 0), closed_at=_utc(6, 0), exit_premium=11_000)
        session.commit()

    with session_factory() as session:
        entries = collect_entries(session, ON)
    _, day_end = ist_day_bounds_utc(ON)
    assert peak_concurrent(entries, day_end=day_end) == 1


def test_a_breakeven_trade_resets_the_loss_streak(session_factory) -> None:  # noqa: ANN001
    """Matches `check_consecutive_losses`'s stated convention deliberately, so
    a disagreement between audit and engine means a real bug rather than two
    defensible definitions."""
    with session_factory() as session:
        _entry(session, coid="l1", opened_at=_utc(4, 0), closed_at=_utc(4, 5), exit_premium=11_000)
        _entry(session, coid="l2", opened_at=_utc(5, 0), closed_at=_utc(5, 5), exit_premium=11_000)
        # Exactly breakeven net. The premium must rise by a whole number of
        # paise, so the costs are chosen to be divisible by the quantity:
        # +99p x 65 = 6,435 gross, against 6,435 of costs, nets to zero. (An
        # earlier version used 6,436 and landed one paise DOWN — a loss, which
        # is the very thing the test claims not to be testing.)
        _entry(
            session,
            coid="be",
            opened_at=_utc(6, 0),
            closed_at=_utc(6, 5),
            exit_premium=12_130 + 99,
            costs=99 * 65,
        )
        _entry(session, coid="l3", opened_at=_utc(7, 0), closed_at=_utc(7, 5), exit_premium=11_000)
        session.commit()

    with session_factory() as session:
        entries = collect_entries(session, ON)
    assert longest_loss_streak(entries) == 2


def test_entries_after_a_halt_are_a_violation(session_factory) -> None:  # noqa: ANN001
    """The halt is the last line of defence. If it fires and entries continue,
    nothing is protecting the account."""
    with session_factory() as session:
        session.add(RiskEventRow(ts=_utc(5, 0), kind="daily_loss_halt", detail="breached"))
        _entry(session, coid="after", opened_at=_utc(6, 0), closed_at=_utc(6, 5), exit_premium=11_000)
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert "no_entries_after_halt" in _violated(audit)


def test_a_halt_with_no_later_entries_is_clean(session_factory) -> None:  # noqa: ANN001
    """The real 2026-08-07 case: the halt fired at 12:27 IST and did stop new
    entries. That part worked, and the report must say so rather than blaming
    the one guard that held."""
    with session_factory() as session:
        session.add(RiskEventRow(ts=_utc(7, 0), kind="daily_loss_halt", detail="breached"))
        _entry(session, coid="before", opened_at=_utc(6, 0), closed_at=_utc(6, 5), exit_premium=12_500)
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert "no_entries_after_halt" not in _violated(audit)


def test_a_fabricated_pnl_is_caught(session_factory) -> None:  # noqa: ANN001
    """`net == gross - costs` is an identity. If it ever fails, some P&L number
    was invented and nothing else in the report can be trusted — which is the
    exact failure this whole project was rebuilt to prevent."""
    with session_factory() as session:
        _entry(session, coid="ok", opened_at=_utc(4, 0), closed_at=_utc(4, 5), exit_premium=11_000)
        session.commit()
    with session_factory() as session:
        trade = session.query(TradeRow).one()
        trade.net_pnl_paise = trade.net_pnl_paise + 50_000  # a nicer number
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert "pnl_arithmetic" in _violated(audit)


def test_a_zero_cost_trade_is_caught(session_factory) -> None:  # noqa: ANN001
    """A real round trip always costs something. Zero means the cost model did
    not run, and every P&L on the day is then overstated."""
    with session_factory() as session:
        _entry(session, coid="free", opened_at=_utc(4, 0), closed_at=_utc(4, 5), exit_premium=11_000, costs=0)
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert "costs_charged" in _violated(audit)


def test_a_zero_lot_position_is_caught(session_factory) -> None:  # noqa: ANN001
    """A zero-lot position is not a trade. One reached the position store
    during the 2026-08-06 freeze-quantity work and flowed through sizing,
    execution and persistence entirely unrefused."""
    with session_factory() as session:
        _entry(session, coid="zero", lots=0, opened_at=_utc(4, 0), closed_at=_utc(4, 5), exit_premium=11_000)
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert "lots_at_least_one" in _violated(audit)


def test_more_concurrent_positions_than_the_cap_is_a_violation(session_factory) -> None:  # noqa: ANN001
    """Positive case for `max_concurrent_positions` (cap of 5, the default in
    `_limits()`). Six positions opened at the same instant and never closed
    all overlap at once — nothing else in this file ever pushes `peak`
    anywhere near the cap, so without this test the rule could be deleted
    outright and the suite would stay green."""
    with session_factory() as session:
        for i in range(6):
            _entry(session, coid=f"conc-{i}", opened_at=_utc(4, 0))
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert "max_concurrent_positions" in _violated(audit)
    breach = next(f for f in audit.findings if f.rule == "max_concurrent_positions")
    assert breach.actual == "6 at once"


def test_more_entries_on_one_underlying_than_the_cap_is_a_violation(session_factory) -> None:  # noqa: ANN001
    """Positive case for `max_entries_per_underlying_per_day` (cap of 2, the
    default). Three NIFTY entries — kept at exactly `max_trades_per_day` (3)
    so this does not also trip the day-wide cap, isolating the per-underlying
    rule the way the "clean day" test's three different underlyings never
    could."""
    with session_factory() as session:
        for i in range(3):
            _entry(
                session,
                coid=f"nifty-{i}",
                symbol="NIFTY11AUG2624550PE",
                opened_at=_utc(4 + i, 0),
                closed_at=_utc(4 + i, 30),
                exit_premium=12_500,  # a winner, so no loss rules fire alongside it
            )
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert "max_entries_per_underlying_per_day" in _violated(audit)
    breach = next(f for f in audit.findings if f.rule == "max_entries_per_underlying_per_day")
    assert breach.actual == "3 on NIFTY"


def test_a_stop_at_or_above_entry_is_caught_as_not_working(session_factory) -> None:  # noqa: ANN001
    """Positive case for `entry_has_working_stop`. The only existing
    reference to this rule was a NEGATIVE assertion
    (`test_the_worst_trade_is_attributed_to_late_exit_not_a_wrong_stop`),
    which a deleted rule satisfies perfectly. This is the audit's one check
    on the stop-clamped-to-zero / stop-above-entry shape."""
    with session_factory() as session:
        _entry(session, coid="stop-at-entry", opened_at=_utc(4, 0), entry=12_130, stop=12_130)
        _entry(session, coid="stop-zero", opened_at=_utc(5, 0), entry=12_130, stop=0)
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    working_stop_violations = [f for f in audit.findings if f.rule == "entry_has_working_stop"]
    assert len(working_stop_violations) == 2


def test_more_lots_than_the_ceiling_is_a_violation(session_factory) -> None:  # noqa: ANN001
    """Positive case for `max_lots` (ceiling of 1, the default). Every other
    fixture in this file uses `lots=1`, which never exercises the `>`
    branch at all."""
    with session_factory() as session:
        _entry(session, coid="over-lots", lots=2, opened_at=_utc(4, 0), closed_at=_utc(4, 30), exit_premium=12_500)
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert "max_lots" in _violated(audit)
    breach = next(f for f in audit.findings if f.rule == "max_lots")
    assert breach.actual == "2 lots"


def test_an_odd_risk_event_is_reported_as_a_warning(session_factory) -> None:  # noqa: ANN001
    """Positive case for rule 13. Every existing `RiskEventRow` fixture in
    this file uses `kind="daily_loss_halt"`, which this query explicitly
    excludes — so a real broker-oddity event (a freeze-quantity the engine
    had to work around) was never actually produced by any test."""
    with session_factory() as session:
        session.add(
            RiskEventRow(ts=_utc(5, 0), kind="freeze_qty_unusable", detail="freeze qty reported as 1, capped order")
        )
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert "risk_event:freeze_qty_unusable" in _rules(audit)
    finding = next(f for f in audit.findings if f.rule == "risk_event:freeze_qty_unusable")
    assert finding.severity == "warning"
    assert "freeze qty reported as 1" in finding.detail


def test_a_position_left_open_at_session_end_is_a_violation(session_factory) -> None:  # noqa: ANN001
    """An intraday engine holding overnight is unintended exposure — the
    failure mode `run_exit_cycle`'s unpriceable-position branch was written for
    after a position stayed open indefinitely on 2026-08-04."""
    with session_factory() as session:
        _entry(session, coid="stuck", opened_at=_utc(4, 0))
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert "closed_by_session_end" in _violated(audit)


# --- day boundaries ----------------------------------------------------------


def test_the_day_is_an_ist_day_not_a_utc_one(session_factory) -> None:  # noqa: ANN001
    """A trade at 09:20 IST is 03:50 UTC — same calendar date, so a naive UTC
    filter happens to work for NSE hours and hides the bug. A trade at 01:00
    IST is 19:30 UTC the PREVIOUS day, and that is where a UTC-based filter
    silently attributes it to the wrong session.

    `te.persistence.repos.paper_trading._day_bounds` has exactly this bug. It
    cannot bite there today only because the exchange never trades at 01:00.
    The audit must not inherit a correctness accident.
    """
    with session_factory() as session:
        # 01:00 IST on 2026-08-07 == 19:30 UTC on 2026-08-06.
        _entry(session, coid="early-ist", opened_at=dt.datetime(2026, 8, 6, 19, 30, tzinfo=dt.UTC))
        # 09:20 IST on 2026-08-07 == 03:50 UTC, the ordinary case.
        _entry(session, coid="normal", opened_at=dt.datetime(2026, 8, 7, 3, 50, tzinfo=dt.UTC))
        session.commit()

    with session_factory() as session:
        entries = collect_entries(session, ON)

    coids = {e.client_order_id for e in entries}
    assert coids == {"early-ist", "normal"}, (
        f"IST day 2026-08-07 should hold both entries, got {coids} — the bounds are being treated as UTC"
    )


def test_an_entry_from_the_previous_session_is_excluded(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        _entry(session, coid="yesterday", opened_at=dt.datetime(2026, 8, 6, 5, 0, tzinfo=dt.UTC))
        _entry(session, coid="today", opened_at=_utc(5, 0))
        session.commit()

    with session_factory() as session:
        entries = collect_entries(session, ON)

    assert [e.client_order_id for e in entries] == ["today"]


# --- the report --------------------------------------------------------------


def test_an_empty_day_audits_clean_and_says_so(session_factory) -> None:  # noqa: ANN001
    """A holiday, or a day the strategy never fired. Must not read as a pass
    that proves anything, and must not crash on zero rows."""
    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert audit.clean
    assert audit.entries == []
    assert "nothing to audit" in format_report(audit)


def test_the_report_names_every_violation_it_found(session_factory) -> None:  # noqa: ANN001
    """The report is the product — a finding the text drops is a finding
    nobody acts on."""
    with session_factory() as session:
        _load_real_session(session)
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    report = format_report(audit)
    for finding in audit.violations:
        assert finding.rule in report, f"{finding.rule} was found but never printed"
    assert "VIOLATION" in report


def test_the_report_states_that_limits_are_current_not_historical(
    session_factory,  # noqa: ANN001
) -> None:
    """Guardrails are live-editable and no per-day history is kept, so a report
    on an old day may be checking against limits that were not in force then.
    Saying so is the difference between a report and a misleading one."""
    with session_factory() as session:
        _load_real_session(session)
        session.commit()

    with session_factory() as session:
        audit = audit_session(session, on=ON, limits=_limits(), now=NOW)

    assert "not as-of the audited day" in format_report(audit)
