from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.domain.money import Paise
from te.engine.approvals import ApprovalRequest, create_approval, decide, pending_approvals
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base

NOW = dt.datetime(2026, 7, 29, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'approvals_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _request(**overrides: object) -> ApprovalRequest:
    defaults: dict[str, object] = {
        "instrument": "NIFTY30JUN2626500CE",
        "side": "BUY",
        "lots": 1,
        "premium": Paise(3_500),
        "stop_loss": Paise(2_800),
        "target": Paise(4_500),
        "estimated_cost": Paise(200),
        "model_verdict": "favorable",
    }
    defaults.update(overrides)
    return ApprovalRequest(**defaults)  # type: ignore[arg-type]


def test_create_and_list_pending(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        create_approval(session, _request(), now=NOW, ttl=dt.timedelta(minutes=5))
        session.commit()

    with session_factory() as session:
        pending = pending_approvals(session, now=NOW + dt.timedelta(minutes=1))
        assert len(pending) == 1
        assert pending[0].status == "pending"


def test_ttl_expiry(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        create_approval(session, _request(), now=NOW, ttl=dt.timedelta(minutes=5))
        session.commit()

    with session_factory() as session:
        pending = pending_approvals(session, now=NOW + dt.timedelta(minutes=10))
        session.commit()
        assert pending == []


def test_decide_approve(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        row = create_approval(session, _request(), now=NOW, ttl=dt.timedelta(minutes=5))
        approval_id = row.id
        session.commit()

    with session_factory() as session:
        updated = decide(session, approval_id, "approve", now=NOW + dt.timedelta(minutes=1))
        session.commit()
        assert updated is not None
        assert updated.status == "approved"


def test_decide_reject(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        row = create_approval(session, _request(), now=NOW, ttl=dt.timedelta(minutes=5))
        approval_id = row.id
        session.commit()

    with session_factory() as session:
        updated = decide(session, approval_id, "reject", now=NOW + dt.timedelta(minutes=1))
        assert updated is not None
        assert updated.status == "rejected"


def test_decide_after_ttl_expires_instead(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        row = create_approval(session, _request(), now=NOW, ttl=dt.timedelta(minutes=5))
        approval_id = row.id
        session.commit()

    with session_factory() as session:
        updated = decide(session, approval_id, "approve", now=NOW + dt.timedelta(minutes=10))
        assert updated is not None
        assert updated.status == "expired"


def test_decide_unknown_id_returns_none(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        assert decide(session, "no-such-id", "approve", now=NOW) is None
