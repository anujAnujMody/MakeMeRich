# mypy: ignore-errors
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from engine.config import EngineConfig
from engine.scheduler import Scheduler, is_market_hours

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def cfg() -> EngineConfig:
    return EngineConfig(
        interval_minutes=5,
        market_open_hour=9,
        market_open_minute=15,
        market_close_hour=15,
        market_close_minute=30,
    )


@pytest.fixture
def mock_executor() -> MagicMock:
    return MagicMock()


class TestIsMarketHours:
    def test_during_market_hours(self) -> None:
        t = datetime(2024, 1, 1, 10, 30, tzinfo=IST)
        assert is_market_hours(t, 9, 15, 15, 30) is True

    def test_before_market_open(self) -> None:
        t = datetime(2024, 1, 1, 8, 0, tzinfo=IST)
        assert is_market_hours(t, 9, 15, 15, 30) is False

    def test_after_market_close(self) -> None:
        t = datetime(2024, 1, 1, 15, 45, tzinfo=IST)
        assert is_market_hours(t, 9, 15, 15, 30) is False

    def test_at_open_boundary(self) -> None:
        t = datetime(2024, 1, 1, 9, 15, tzinfo=IST)
        assert is_market_hours(t, 9, 15, 15, 30) is True

    def test_at_close_boundary(self) -> None:
        t = datetime(2024, 1, 1, 15, 30, tzinfo=IST)
        assert is_market_hours(t, 9, 15, 15, 30) is True

    def test_handles_naive_datetime(self) -> None:
        t = datetime(2024, 1, 1, 10, 0, tzinfo=IST)
        assert is_market_hours(t, 9, 15, 15, 30) is True


class TestScheduler:
    def test_setup_schedule_during_market_hours(self, cfg, mock_executor) -> None:
        with (
            patch("engine.scheduler.is_market_hours", return_value=True),
            patch("engine.scheduler.schedule") as mock_schedule,
        ):
            sched = Scheduler(cfg, mock_executor)
            sched.setup_schedule()
            mock_schedule.every.assert_called_once()

    def test_setup_schedule_skips_outside_hours(self, cfg, mock_executor) -> None:
        with (
            patch("engine.scheduler.is_market_hours", return_value=False),
            patch("engine.scheduler.schedule") as mock_schedule,
        ):
            sched = Scheduler(cfg, mock_executor)
            sched.setup_schedule()
            mock_schedule.every.assert_not_called()

    def test_run_cycle_calls_executor(self, cfg, mock_executor) -> None:
        sched = Scheduler(cfg, mock_executor)
        sched._run_cycle()
        mock_executor.run.assert_called_once()

    def test_stop_clears_schedule(self, cfg, mock_executor) -> None:
        with patch("engine.scheduler.schedule") as mock_schedule:
            sched = Scheduler(cfg, mock_executor)
            sched.stop()
            mock_schedule.clear.assert_called_once()

    def test_setup_schedule_uses_configured_interval(self, cfg, mock_executor) -> None:
        with (
            patch("engine.scheduler.is_market_hours", return_value=True),
            patch("engine.scheduler.schedule") as mock_schedule,
        ):
            sched = Scheduler(cfg, mock_executor)
            sched.setup_schedule()
            mock_schedule.every.assert_called_with(cfg.interval_minutes)

    def test_stop_closes_executor(self, cfg, mock_executor) -> None:
        sched = Scheduler(cfg, mock_executor)
        sched.stop()
        mock_executor.close.assert_called_once()
