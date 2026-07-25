from datetime import datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))

PYTHON = r"C:\Users\Anuj\AppData\Local\Programs\Python\Python312\python.exe"


def make_bar(
    hour: int,
    minute: int,
    open_p: float,
    high: float,
    low: float,
    close: float,
    volume: float = 10000,
) -> dict[str, Any]:
    return {
        "time": datetime(2024, 1, 1, hour, minute, tzinfo=IST).isoformat(),
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def make_bars_from_series(prices: list[tuple[float, float, float, float]], start_hour: int = 9, start_min: int = 15, volume: float = 10000) -> list[dict[str, Any]]:
    bars = []
    h, m = start_hour, start_min
    for o, hi, lo, c in prices:
        bars.append(make_bar(h, m, o, hi, lo, c, volume))
        m += 5
        if m >= 60:
            h += 1
            m = 0
    return bars
