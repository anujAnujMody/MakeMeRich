#!/usr/bin/env python
"""Did the session obey the rules the engine claims to enforce?

    docker exec trading-engine-1 python3 -m scripts.session_audit_report
    docker exec trading-engine-1 python3 -m scripts.session_audit_report --date 2026-08-07

Exits non-zero when a violation is found, so this is usable as a check, not
only as a report.

See `te.audit.session_audit`'s module docstring for why this exists and why it
re-derives every number instead of calling the engine's own counters. The
short version: on 2026-08-07 the engine broke four of its own limits in one
session with 1,535 tests passing, because the tests and the code shared the
same wrong idea of what "a trade today" means.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from te.audit.session_audit import AuditLimits, audit_session, format_report
from te.domain.clock import IST
from te.engine.state import get_guardrails, guardrails_defaults_from_settings
from te.persistence.db import make_engine, make_session_factory
from te.settings import Settings


def build_limits(session, settings: Settings) -> AuditLimits:  # noqa: ANN001
    """The rules in force, read from the same places the engine reads them.

    Reading the LIMITS from live config is correct and different from reading
    the COUNTS from live helpers: a limit is a stated intention, and auditing
    against a hand-copied duplicate of it would just drift. The counts are what
    must stay independent.
    """
    guardrails = get_guardrails(session, defaults=guardrails_defaults_from_settings(settings))
    return AuditLimits(
        max_trades_per_day=guardrails.max_trades_per_day,
        max_concurrent_positions=guardrails.max_concurrent_positions,
        max_daily_loss_paise=int(guardrails.max_daily_loss),
        max_consecutive_losses=settings.paper_cycle_max_consecutive_losses,
        max_entries_per_underlying_per_day=settings.paper_cycle_max_entries_per_underlying_per_day,
        max_loss_per_trade_paise=settings.paper_cycle_max_loss_per_trade_paise,
        max_lots=settings.paper_cycle_max_lots,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="IST trading date, YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    now = dt.datetime.now(IST)
    on = dt.date.fromisoformat(args.date) if args.date else now.date()

    settings = Settings()
    session_factory = make_session_factory(make_engine(settings.database_url))
    with session_factory() as session:
        limits = build_limits(session, settings)
        audit = audit_session(session, on=on, limits=limits, now=now)

    print(format_report(audit))
    return 1 if audit.violations else 0


if __name__ == "__main__":
    sys.exit(main())
