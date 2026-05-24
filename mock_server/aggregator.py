from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from .engine import MARKET_OPEN, Candle, IST


def resample(rows: Iterable[Candle], minutes: int) -> list[Candle]:
    """Resample 1-minute candles into a higher timeframe."""
    buckets: dict[datetime, list[Candle]] = {}
    for row in rows:
        anchor = bucket_start(row.timestamp, minutes)
        buckets.setdefault(anchor, []).append(row)
    output: list[Candle] = []
    for ts in sorted(buckets):
        group = sorted(buckets[ts], key=lambda c: c.timestamp)
        output.append(
            Candle(
                timestamp=ts,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            )
        )
    return output


def bucket_start(timestamp: datetime, minutes: int) -> datetime:
    """Return the anchor timestamp for the resampling bucket containing timestamp."""
    if minutes >= 24 * 60:
        return datetime.combine(timestamp.date(), MARKET_OPEN, tzinfo=IST)
    market_open = datetime.combine(timestamp.date(), MARKET_OPEN, tzinfo=IST)
    elapsed = int((timestamp - market_open).total_seconds() // 60)
    bucket = (elapsed // minutes) * minutes
    return market_open + timedelta(minutes=bucket)
