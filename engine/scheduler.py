import signal
import sys
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import schedule

from engine.config import EngineConfig
from engine.executor import Executor

def is_market_hours(
    dt: datetime,
    open_hour: int = 9,
    open_minute: int = 15,
    close_hour: int = 15,
    close_minute: int = 30,
) -> bool:
    if dt.tzinfo:
        ist = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
    else:
        ist = dt
    minutes = ist.hour * 60 + ist.minute
    open_min = open_hour * 60 + open_minute
    close_min = close_hour * 60 + close_minute
    return open_min <= minutes <= close_min


class Scheduler:
    def __init__(self, config: EngineConfig, executor: Executor | None = None) -> None:
        self.config = config
        self.executor = executor or Executor(config)
        self._running = False

    def setup_schedule(self) -> None:
        if not is_market_hours(
            datetime.now(UTC),
            self.config.market_open_hour,
            self.config.market_open_minute,
            self.config.market_close_hour,
            self.config.market_close_minute,
        ):
            return
        schedule.every(self.config.interval_minutes).minutes.do(self._run_cycle)

    def start(self) -> None:
        self._running = True
        self.setup_schedule()
        self._register_signal_handlers()
        while self._running:
            schedule.run_pending()

    def _run_cycle(self) -> None:
        self.executor.run()

    def stop(self) -> None:
        self._running = False
        schedule.clear()
        self.executor.close()

    def _register_signal_handlers(self) -> None:
        def _shutdown(sig: int, frame: Any) -> None:
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
