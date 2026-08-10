"""`te.risk.limits` — mutants not covered by `test_limits.py`/
`test_pure_predicates.py`: the peak-watermark ratchet-up path in
`check_max_drawdown` (only exercised there with a peak that stays flat or
drops, never rises across two calls), and the exact drawdown-percentage
figure in its breach message."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from te.domain.money import Paise
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base
from te.risk.limits import LimitBreachError, RiskLimitsConfig, check_max_drawdown

NOW = dt.datetime(2026, 7, 29, 14, 0, tzinfo=dt.UTC)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "limits_boundaries_test.db"


def _session_factory(db_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _config(**overrides: object) -> RiskLimitsConfig:
    defaults: dict[str, object] = {
        "max_daily_loss_paise": Paise(10_000_00), "max_concurrent_positions": 5, "max_trades_per_day": 50,
        "max_drawdown_pct": Decimal(10),
    }
    defaults.update(overrides)
    return RiskLimitsConfig(**defaults)  # type: ignore[arg-type]


def test_peak_watermark_ratchets_up_on_a_second_higher_observation(db_path: Path) -> None:
    """The FIRST call establishes a peak of 10,000. A SECOND call with equity
    at 15,000 must RAISE the stored peak to 15,000 (not merely accept the new
    high without persisting it) — proven by a THIRD call dropping to 12,000:
    that is a 20% drawdown from the true 15,000 peak, over the 10% cap, and
    must raise. If the watermark never ratcheted past the first observation
    (`stored_peak is None or peak != stored_peak` weakened to `and`, or the
    `!=` weakened to `==`), the stale stored peak of 10,000 would see 12,000
    as ABOVE peak — no drawdown, no breach."""
    factory = _session_factory(db_path)
    config = _config(max_drawdown_pct=Decimal(10))

    with factory() as session:
        check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(10_000_00))
        session.commit()
    with factory() as session:
        check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(15_000_00))  # must not raise
        session.commit()
    with factory() as session, pytest.raises(LimitBreachError):
        check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(12_000_00))


def test_drawdown_breach_reason_reports_the_exact_percentage(db_path: Path) -> None:
    """`drawdown_pct = (peak - current) / peak x 100` in the breach reason —
    pinned against the exact figure, so a `-` -> `+`, `/` -> `*`, or `*` ->
    `//` on that formula produces a visibly wrong number in a message a human
    reads to decide whether to trust the halt."""
    factory = _session_factory(db_path)
    config = _config(max_drawdown_pct=Decimal(10))

    with factory() as session:
        check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(10_000_00))
        session.commit()

    with factory() as session, pytest.raises(LimitBreachError) as exc:
        check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(7_500_00))

    # (10_000_00 - 7_500_00) / 10_000_00 * 100 = 25.00%
    assert "25.00%" in exc.value.reason
