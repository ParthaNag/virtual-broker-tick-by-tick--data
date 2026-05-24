from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = dt_time(9, 15, 0)
MARKET_CLOSE = dt_time(15, 30, 0)
MARKET_SECONDS_PER_DAY: int = 22500  # 09:15 to 15:30 = 375 minutes


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Instrument:
    ref_id: int
    symbol: str
    file_symbol: str
    exchange: str = "NSE"
    asset: str = "STOCK"
    tick_size: int = 5
    lot_size: int = 1

    @property
    def nubra_name(self) -> str:
        return f"STOCK_{self.symbol}.NSECM"


def to_paise(value: float | int | None) -> int | None:
    if value is None:
        return None
    return int(round(float(value) * 100))


def from_paise(value: float | int | None) -> float | None:
    if value is None:
        return None
    return float(value) / 100.0


def epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def parse_datetime(value: str | None, default_tz: ZoneInfo = IST) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(default_tz)


def interval_minutes(interval: str) -> int:
    normalized = interval.lower().strip()
    aliases = {
        "minute": "1m",
        "1min": "1m",
        "3min": "3m",
        "5min": "5m",
        "10min": "10m",
        "15min": "15m",
        "30min": "30m",
        "60min": "1h",
        "day": "1d",
        "daily": "1d",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {"1d", "d"}:
        return 24 * 60
    if normalized in {"1h", "60m"}:
        return 60
    if normalized.endswith("m"):
        return int(normalized[:-1])
    raise ValueError(f"Unsupported interval: {interval}")


class VirtualClock:
    def __init__(self, start: datetime, trading_days: list[date] | None = None, speed: float = 1.0) -> None:
        self.start = start.astimezone(IST)
        self._trading_days: list[date] = sorted(set(trading_days)) if trading_days else [self.start.date()]
        self._real_started = time.monotonic()
        self._speed = speed
        self._start_elapsed: float = self._elapsed_at(self.start)

    def _elapsed_at(self, dt: datetime) -> float:
        """Market seconds elapsed from the start of the trading-day series up to dt."""
        dt = dt.astimezone(IST)
        market_open = datetime.combine(dt.date(), MARKET_OPEN, tzinfo=IST)
        secs_today = max(0.0, min(float(MARKET_SECONDS_PER_DAY), (dt - market_open).total_seconds()))
        days_before = sum(1 for d in self._trading_days if d < dt.date())
        return days_before * MARKET_SECONDS_PER_DAY + secs_today

    @property
    def now(self) -> datetime:
        total = self._start_elapsed + (time.monotonic() - self._real_started) * self._speed
        day_idx = int(total // MARKET_SECONDS_PER_DAY)
        sec_in_day = total % MARKET_SECONDS_PER_DAY
        if day_idx >= len(self._trading_days):
            last = self._trading_days[-1]
            return datetime.combine(last, MARKET_CLOSE, tzinfo=IST)
        sim_date = self._trading_days[day_idx]
        sim_dt = datetime.combine(sim_date, MARKET_OPEN, tzinfo=IST) + timedelta(seconds=sec_in_day)
        return min(sim_dt, datetime.combine(sim_date, MARKET_CLOSE, tzinfo=IST))


class MarketDataStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.instruments: dict[str, Instrument] = {}
        self.by_ref_id: dict[int, Instrument] = {}
        self.aliases: dict[str, str] = {}
        self.candles: dict[str, list[Candle]] = {}
        self.candle_index: dict[str, dict[datetime, Candle]] = {}
        self.available_dates: set[date] = set()
        self._load_all()

    def _load_all(self) -> None:
        files = sorted(self.data_dir.glob("*.csv"))
        if not files:
            raise RuntimeError(f"No CSV files found under {self.data_dir}")

        for idx, path in enumerate(files, start=100001):
            file_symbol = path.stem
            symbol = file_symbol[:-3] if file_symbol.upper().endswith(".NS") else file_symbol
            instrument = Instrument(ref_id=idx, symbol=symbol.upper(), file_symbol=file_symbol)
            self.instruments[instrument.symbol] = instrument
            self.by_ref_id[instrument.ref_id] = instrument
            self.aliases[instrument.symbol] = instrument.symbol
            self.aliases[file_symbol.upper()] = instrument.symbol

            rows = self._read_csv(path)
            filled = self._forward_fill_gaps(rows)
            self.candles[instrument.symbol] = filled
            self.candle_index[instrument.symbol] = {row.timestamp: row for row in filled}
            self.available_dates.update(row.timestamp.date() for row in filled)

    def _read_csv(self, path: Path) -> list[Candle]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows: list[Candle] = []
            for row in reader:
                timestamp_text = row.get("timestamp") or row.get("Datetime") or row.get("datetime")
                timestamp = parse_datetime(timestamp_text)
                if timestamp is None:
                    continue
                rows.append(
                    Candle(
                        timestamp=timestamp.replace(second=0, microsecond=0),
                        open=float(row.get("open") or row.get("Open") or 0),
                        high=float(row.get("high") or row.get("High") or 0),
                        low=float(row.get("low") or row.get("Low") or 0),
                        close=float(row.get("close") or row.get("Close") or 0),
                        volume=int(float(row.get("volume") or row.get("Volume") or 0)),
                    )
                )
        return sorted(rows, key=lambda item: item.timestamp)

    def _forward_fill_gaps(self, rows: list[Candle]) -> list[Candle]:
        if not rows:
            return rows
        filled = [rows[0]]
        for row in rows[1:]:
            prev = filled[-1]
            cursor = prev.timestamp + timedelta(minutes=1)
            while cursor < row.timestamp and cursor.date() == prev.timestamp.date() and cursor.time() <= MARKET_CLOSE:
                filled.append(Candle(cursor, prev.close, prev.close, prev.close, prev.close, 0))
                cursor += timedelta(minutes=1)
            filled.append(row)
        return filled

    def resolve_symbol(self, symbol: str) -> str:
        key = symbol.upper().strip()
        if key not in self.aliases:
            raise KeyError(f"Unknown symbol: {symbol}")
        return self.aliases[key]

    def instrument_for_symbol(self, symbol: str) -> Instrument:
        return self.instruments[self.resolve_symbol(symbol)]

    def instrument_for_ref_id(self, ref_id: int) -> Instrument:
        if ref_id not in self.by_ref_id:
            raise KeyError(f"Unknown ref_id: {ref_id}")
        return self.by_ref_id[ref_id]

    def first_date(self) -> date:
        return min(self.available_dates)

    def minute_candle(self, symbol: str, minute: datetime) -> Candle:
        resolved = self.resolve_symbol(symbol)
        minute = minute.astimezone(IST).replace(second=0, microsecond=0)
        found = self.candle_index[resolved].get(minute)
        if found:
            return found

        earlier = [row for row in self.candles[resolved] if row.timestamp <= minute]
        if earlier:
            prev = earlier[-1]
            return Candle(minute, prev.close, prev.close, prev.close, prev.close, 0)
        raise KeyError(f"No data for {resolved} at or before {minute.isoformat()}")

    def candles_between(self, symbol: str, start: datetime, end: datetime) -> list[Candle]:
        resolved = self.resolve_symbol(symbol)
        start = start.astimezone(IST)
        end = end.astimezone(IST)
        return [row for row in self.candles[resolved] if start <= row.timestamp <= end]


class MarketSimulator:
    def __init__(self, store: MarketDataStore, clock: VirtualClock, seed: str = "nubra-mock") -> None:
        self.store = store
        self.clock = clock
        self.seed = seed

    def current_tick(self, symbol: str, now: datetime | None = None) -> dict:
        now = now or self.clock.now
        candle = self.partial_minute_candle(symbol, now)
        return {
            "type": "tick",
            "symbol": self.store.resolve_symbol(symbol),
            "lp": candle.close,
            "v": candle.volume,
            "o": candle.open,
            "h": candle.high,
            "l": candle.low,
            "c": candle.close,
            "timestamp": now.replace(microsecond=0).isoformat(),
        }

    def projected_price(self, candle: Candle, second: int, symbol: str) -> float:
        second = max(0, min(59, second))
        first_high = self._touch_high_first(symbol, candle.timestamp)
        first_extreme = candle.high if first_high else candle.low
        second_extreme = candle.low if first_high else candle.high

        if second <= 15:
            price = self._lerp(candle.open, first_extreme, second / 15)
        elif second <= 45:
            price = self._lerp(first_extreme, second_extreme, (second - 15) / 30)
        else:
            price = self._lerp(second_extreme, candle.close, (second - 45) / 14)

        wave = math.sin(second * math.pi / 7.0) * (candle.high - candle.low) * 0.015
        return round(min(candle.high, max(candle.low, price + wave)), 2)

    def partial_minute_candle(self, symbol: str, now: datetime | None = None) -> Candle:
        now = now or self.clock.now
        minute = now.replace(second=0, microsecond=0)
        base = self.store.minute_candle(symbol, minute)
        second = now.second
        prices = [self.projected_price(base, sec, symbol) for sec in range(second + 1)]
        return Candle(
            timestamp=minute,
            open=base.open,
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=int(round(base.volume * ((second + 1) / 60.0))),
        )

    def historical_candles(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        include_partial: bool = True,
    ) -> list[Candle]:
        now = self.clock.now
        effective_end = min(end.astimezone(IST), now)
        start = start.astimezone(IST)
        if effective_end < start:
            return []

        minute = interval_minutes(interval)
        if minute == 1:
            rows = self.store.candles_between(symbol, start, effective_end.replace(second=0, microsecond=0))
            if include_partial and effective_end.second > 0:
                rows = [row for row in rows if row.timestamp < effective_end.replace(second=0, microsecond=0)]
                rows.append(self.partial_minute_candle(symbol, effective_end))
            return rows

        rows = self.store.candles_between(symbol, start, effective_end.replace(second=0, microsecond=0))
        if include_partial and effective_end.second > 0:
            rows = [row for row in rows if row.timestamp < effective_end.replace(second=0, microsecond=0)]
            rows.append(self.partial_minute_candle(symbol, effective_end))
        return self._resample(rows, minute)

    def _resample(self, rows: Iterable[Candle], minutes: int) -> list[Candle]:
        buckets: dict[datetime, list[Candle]] = {}
        for row in rows:
            anchor = self._bucket_start(row.timestamp, minutes)
            buckets.setdefault(anchor, []).append(row)

        output: list[Candle] = []
        for timestamp in sorted(buckets):
            group = sorted(buckets[timestamp], key=lambda item: item.timestamp)
            output.append(
                Candle(
                    timestamp=timestamp,
                    open=group[0].open,
                    high=max(item.high for item in group),
                    low=min(item.low for item in group),
                    close=group[-1].close,
                    volume=sum(item.volume for item in group),
                )
            )
        return output

    def _bucket_start(self, timestamp: datetime, minutes: int) -> datetime:
        if minutes >= 24 * 60:
            return datetime.combine(timestamp.date(), MARKET_OPEN, tzinfo=IST)
        market_open = datetime.combine(timestamp.date(), MARKET_OPEN, tzinfo=IST)
        elapsed = int((timestamp - market_open).total_seconds() // 60)
        bucket = (elapsed // minutes) * minutes
        return market_open + timedelta(minutes=bucket)

    def _touch_high_first(self, symbol: str, timestamp: datetime) -> bool:
        material = f"{self.seed}:{symbol}:{timestamp.isoformat()}".encode("utf-8")
        return hashlib.sha256(material).digest()[0] % 2 == 0

    @staticmethod
    def _lerp(start: float, end: float, fraction: float) -> float:
        return start + ((end - start) * max(0.0, min(1.0, fraction)))


def load_config_start_date(root: Path) -> str | None:
    # 1. Try env variable
    val = os.getenv("START_DATE")
    if val:
        return val.strip()

    # 2. Try config.py in root or config.py in mock_server
    for search_dir in [root, root / "mock_server"]:
        config_py = search_dir / "config.py"
        if config_py.exists():
            try:
                scope: dict = {}
                exec(config_py.read_text(encoding="utf-8"), scope)
                if "START_DATE" in scope and scope["START_DATE"]:
                    return str(scope["START_DATE"]).strip()
            except Exception:
                pass

    # 3. Try config.yaml / config.yml in root or mock_server
    for search_dir in [root, root / "mock_server"]:
        for name in ["config.yaml", "config.yml"]:
            config_yaml = search_dir / name
            if config_yaml.exists():
                try:
                    for line in config_yaml.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if ":" in line:
                            k, v = line.split(":", 1)
                            if k.strip().upper() == "START_DATE":
                                return v.strip().strip("'\"")
                except Exception:
                    pass

    # 4. Try config.json in root or mock_server
    for search_dir in [root, root / "mock_server"]:
        config_json = search_dir / "config.json"
        if config_json.exists():
            try:
                data = json.loads(config_json.read_text(encoding="utf-8"))
                if "START_DATE" in data and data["START_DATE"]:
                    return str(data["START_DATE"]).strip()
            except Exception:
                pass

    return None


def create_default_stack() -> tuple[MarketDataStore, VirtualClock, MarketSimulator, Path]:
    root = Path(__file__).resolve().parents[1]
    data_dir = Path(os.getenv("MOCK_BROKER_DATA_DIR", str(root / "1m_Interval")))
    state_path = Path(os.getenv("MOCK_BROKER_STATE", str(Path(__file__).parent / "session_state.json")))
    store = MarketDataStore(data_dir)
    trading_days = sorted(store.available_dates)

    start_date_text = load_config_start_date(root)
    start_date = date.fromisoformat(start_date_text) if start_date_text else store.first_date()
    clock_start = datetime.combine(start_date, MARKET_OPEN, tzinfo=IST)

    if state_path.exists() and not start_date_text:
        try:
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            saved_time = saved.get("clock_virtual_time")
            if saved_time:
                restored = datetime.fromisoformat(saved_time)
                clock_start = restored.replace(tzinfo=IST) if restored.tzinfo is None else restored.astimezone(IST)
        except Exception:
            pass

    clock = VirtualClock(clock_start, trading_days=trading_days, speed=float(os.getenv("MOCK_BROKER_SPEED", "1.0")))
    simulator = MarketSimulator(store, clock, seed=os.getenv("MOCK_BROKER_SEED", "nubra-mock"))
    return store, clock, simulator, state_path
