"""Tests for te.data.vix.get_vix_snapshot — level + near/next term slope."""

from __future__ import annotations

from te.broker.openalgo_rest import OpenAlgoRestClient, Quote
from te.data.vix import get_vix_snapshot


class _FakeClient(OpenAlgoRestClient):
    def __init__(self, quotes_by_symbol: dict[str, float]) -> None:
        self._quotes = quotes_by_symbol

    def quotes(self, symbol: str, exchange: str) -> Quote:  # type: ignore[override]
        return Quote(
            symbol=symbol,
            exchange=exchange,
            ltp=self._quotes[symbol],
            open=0.0,
            high=0.0,
            low=0.0,
            prev_close=0.0,
            volume=0.0,
            oi=0.0,
            bid=0.0,
            ask=0.0,
        )


def test_get_vix_snapshot_level_only() -> None:
    client = _FakeClient({"INDIAVIX": 12.2})
    snap = get_vix_snapshot(client)

    assert snap.level == 12.2
    assert snap.near_month is None
    assert snap.next_month is None
    assert snap.term_slope is None


def test_get_vix_snapshot_computes_term_slope() -> None:
    client = _FakeClient({"INDIAVIX": 12.2, "INDIAVIX28AUG25FUT": 13.0, "INDIAVIX25SEP25FUT": 14.0})
    snap = get_vix_snapshot(
        client,
        near_month_symbol="INDIAVIX28AUG25FUT",
        next_month_symbol="INDIAVIX25SEP25FUT",
    )

    assert snap.near_month == 13.0
    assert snap.next_month == 14.0
    assert round(snap.term_slope, 6) == round((14.0 - 13.0) / 13.0, 6)
