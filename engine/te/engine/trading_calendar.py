"""Fetch, persist and read back the exchange holiday calendar.

Joins the three layers that a calendar needs and that the layer rule keeps
apart: `te.broker` fetches the rows, `te.domain.calendar` parses them into a
pure `TradingCalendar`, and `engine_state` stores the parsed form so the
engine is not one broker outage away from not knowing what day it is.

### Why it is cached rather than fetched per check

"Is today a trading day?" is asked by the paper cycle (every minute), the
recorder supervisor, the bhavcopy job and the market-status endpoint. A
per-check REST call would be several hundred requests a day for a list that
changes at most a handful of times a year, and would make every one of those
callers fail when the broker is unreachable — including, worst of all, at
09:14 on a normal trading morning.

### Refresh policy

Refreshed by a scheduler job, and lazily whenever the stored calendar's year
does not cover the date being asked about (a running engine crossing 1
January must not spend the first day of the year with last year's
calendar). A failed refresh leaves the previous calendar in place and logs;
it never clears it, because a stale calendar is enormously better than none.
"""

from __future__ import annotations

import datetime as dt
import json

import structlog
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from te.broker.openalgo_rest import OpenAlgoRestClient
from te.domain.calendar import SpecialSession, TradingCalendar, from_holiday_rows
from te.domain.clock import IST, SessionWindow
from te.engine.state import upsert_engine_state
from te.persistence.db import session_scope
from te.persistence.models import EngineState

logger = structlog.get_logger(__name__)

_CALENDAR_KEY = "trading_calendar_json"


def get_calendar(session: Session) -> TradingCalendar:
    """The stored calendar, or `TradingCalendar.unknown()` when nothing has
    been fetched or the stored row fails to parse.

    `unknown()` reports every date as a non-session, which stands the engine
    down. That is the deliberate choice: an engine that refuses to trade
    because it cannot tell a holiday from a Tuesday gets noticed and fixed,
    whereas one that assumes every weekday is a session trades into a dead
    feed on Republic Day and looks fine doing it."""
    try:
        row = session.get(EngineState, _CALENDAR_KEY)
    except OperationalError:
        # The table may not exist yet — this is read from the FastAPI
        # lifespan, which runs before/independently of migrations against a
        # DB the API's own `create_all` has not necessarily touched. Reading
        # a calendar must never be the thing that stops the app booting.
        logger.warning("engine_state is not queryable yet — treating the trading calendar as unknown")
        return TradingCalendar.unknown()
    if row is None:
        return TradingCalendar.unknown()
    try:
        payload = json.loads(row.value)
        return TradingCalendar(
            known=True,
            closed={
                exchange: frozenset(dt.date.fromisoformat(d) for d in dates)
                for exchange, dates in payload["closed"].items()
            },
            special={
                exchange: {
                    dt.date.fromisoformat(d): _session_from_json(d, entry)
                    for d, entry in sessions.items()
                }
                for exchange, sessions in payload.get("special", {}).items()
            },
        )
    except (ValueError, KeyError, TypeError):
        logger.warning("stored trading calendar failed to parse — standing down until refreshed")
        return TradingCalendar.unknown()


def _session_from_json(date_str: str, entry: dict[str, str]) -> SpecialSession:
    return SpecialSession(
        date=dt.date.fromisoformat(date_str),
        window=SessionWindow(
            start=dt.time.fromisoformat(entry["start"]),
            end=dt.time.fromisoformat(entry["end"]),
        ),
        description=entry.get("description", ""),
    )


def set_calendar(session: Session, calendar: TradingCalendar, *, years: list[int]) -> None:
    """Stores the parsed calendar. `years` is recorded so `needs_refresh`
    can tell "this year has no holidays" from "we never fetched this year".
    Does not commit — caller owns the transaction, matching every setter in
    `te.engine.state`."""
    payload = {
        "years": years,
        "closed": {
            exchange: sorted(d.isoformat() for d in dates) for exchange, dates in calendar.closed.items()
        },
        "special": {
            exchange: {
                d.isoformat(): {
                    "start": s.window.start.isoformat(),
                    "end": s.window.end.isoformat(),
                    "description": s.description,
                }
                for d, s in sessions.items()
            }
            for exchange, sessions in calendar.special.items()
        },
    }
    upsert_engine_state(session, _CALENDAR_KEY, json.dumps(payload))


def stored_years(session: Session) -> set[int]:
    row = session.get(EngineState, _CALENDAR_KEY)
    if row is None:
        return set()
    try:
        return {int(y) for y in json.loads(row.value).get("years", [])}
    except (ValueError, KeyError, TypeError):
        return set()


def refresh_calendar(
    session_factory: sessionmaker[Session], client: OpenAlgoRestClient, *, year: int | None = None
) -> bool:
    """Fetches and stores the calendar for `year` (default: the current IST
    year). Returns whether the stored calendar changed.

    Never clears an existing calendar on failure — it logs and leaves the
    previous one in place. A stale holiday list is wrong on at most a few
    dates; no holiday list is wrong on all of them."""
    target = year if year is not None else dt.datetime.now(IST).year
    try:
        rows = client.holiday_rows(target)
    except Exception as exc:  # noqa: BLE001 — a calendar refresh must never take the engine down
        logger.warning(
            "trading calendar refresh failed — keeping the previously stored one",
            year=target,
            error=str(exc),
        )
        return False

    calendar = from_holiday_rows(rows)
    if calendar.dropped_rows:
        # `te.domain.calendar` is pure and cannot log (see its module
        # docstring) — this is the wiring layer, so the drop happens here,
        # loudly, one row at a time. A dropped holiday row is NOT the same
        # as "not a holiday": silence here would let it masquerade as the
        # second, exactly the failure `TradingCalendar`'s "Failing safe"
        # section exists to prevent.
        for row in calendar.dropped_rows:
            logger.warning("trading calendar row dropped — could not parse", year=target, row=row)
        logger.warning(
            "trading calendar refresh dropped rows",
            year=target,
            dropped_count=len(calendar.dropped_rows),
        )
    with session_scope(session_factory) as session:
        years = sorted(stored_years(session) | {target})
        existing = get_calendar(session)
        merged = _merge(existing, calendar, year=target) if existing.known else calendar
        set_calendar(session, merged, years=years)

    logger.info(
        "trading calendar refreshed",
        year=target,
        nse_holidays=len(calendar.holidays_for("NSE")),
        special_sessions=len(calendar.special.get("NSE", {})),
    )
    return True


def _merge(existing: TradingCalendar, fresh: TradingCalendar, *, year: int) -> TradingCalendar:
    """Combines two calendars — used when a second year is fetched so the
    engine can answer questions either side of 1 January without a gap.

    Within `year`, and only for the exchanges the fresh payload actually
    reports, that payload is AUTHORITATIVE and replaces what was stored.
    Every other year, and every exchange the payload says nothing about, is
    left untouched. All three halves of that are load-bearing:

    * Replacing rather than unioning is what lets a holiday be WITHDRAWN. A
      union can only ever grow, so a date the exchange later removes from
      its list could never come back off ours, and the engine would stand
      down on a real trading day forever.
    * Scoping the replacement to `year` is what keeps multi-year
      accumulation working. This function exists precisely so the engine can
      answer questions either side of 1 January; replacing an exchange's
      dates outright would make fetching 2027 erase every 2026 holiday, and
      the engine would then trade straight through them.

    * Restricting it to exchanges present in `fresh.closed` is what stops a
      partial payload erasing an exchange it never mentioned. In practice
      `from_holiday_rows` always emits all four `TRADED_EXCHANGES`, so this
      only bites hand-constructed calendars — but "said nothing" and "said
      none" are different claims and must not collapse into one.

    `year` is passed in rather than inferred from the fresh calendar's own
    dates because a payload can legitimately contain none — "this exchange
    has no holidays in 2027" is a real answer, and it must still be able to
    clear a stale 2027 entry.

    `special` follows the same rule, which also preserves its per-date
    overwrite behaviour for a revision inside `year` (it happens; election
    dates move)."""
    exchanges = set(existing.closed) | set(fresh.closed) | set(existing.special) | set(fresh.special)
    return TradingCalendar(
        known=True,
        closed={
            exchange: (
                frozenset(
                    {on for on in existing.closed.get(exchange, frozenset()) if on.year != year}
                    | set(fresh.closed[exchange])
                )
                if exchange in fresh.closed
                else existing.closed.get(exchange, frozenset())
            )
            for exchange in exchanges
        },
        special={
            exchange: (
                {
                    **{
                        on: session
                        for on, session in existing.special.get(exchange, {}).items()
                        if on.year != year
                    },
                    **fresh.special[exchange],
                }
                if exchange in fresh.special
                else existing.special.get(exchange, {})
            )
            for exchange in exchanges
        },
    )
