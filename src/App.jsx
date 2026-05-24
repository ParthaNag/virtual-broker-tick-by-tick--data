import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getHealth, getHistoricalCandles, getInstruments, tickStreamUrl } from "./api";

const INTERVALS = ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "1d"];
const WINDOWS = [
  { label: "60 candles", value: 60 },
  { label: "120 candles", value: 120 },
  { label: "240 candles", value: 240 },
  { label: "Full day", value: 0 },
];

function formatMarketTime(value) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: "Asia/Kolkata",
  }).format(new Date(value));
}

function marketOpenFor(value) {
  const date = value ? new Date(value) : new Date();
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const get = (type) => parts.find((part) => part.type === type)?.value;
  return `${get("year")}-${get("month")}-${get("day")}T09:15:00+05:30`;
}

function trimCandles(candles, windowSize) {
  if (!windowSize || candles.length <= windowSize) return candles;
  return candles.slice(-windowSize);
}

function minuteTimestamp(value) {
  const date = new Date(value);
  date.setSeconds(0, 0);
  return date.toISOString();
}

function candleFromTick(tick) {
  return {
    timestamp: minuteTimestamp(tick.timestamp),
    open: tick.o,
    high: tick.h,
    low: tick.l,
    close: tick.c,
    volume: tick.v,
    partial: true,
  };
}

function getIntervalMinutes(interval) {
  if (interval === "1d") return 24 * 60;
  if (interval === "1h") return 60;
  const match = interval.match(/^(\d+)m$/);
  if (match) return parseInt(match[1], 10);
  return 1;
}

function getBucketStart(tickTimestamp, interval) {
  const tickTime = new Date(tickTimestamp);
  const marketOpenTime = new Date(marketOpenFor(tickTimestamp));
  
  if (interval === "1d") {
    return marketOpenTime.toISOString();
  }
  
  const intervalMins = getIntervalMinutes(interval);
  const elapsedMinutes = Math.floor((tickTime - marketOpenTime) / 60000);
  const bucketIndex = Math.max(0, Math.floor(elapsedMinutes / intervalMins));
  const bucketStart = new Date(marketOpenTime.getTime() + bucketIndex * intervalMins * 60000);
  return bucketStart.toISOString();
}

function mergeTick(candles, tick, interval, lastTick) {
  const bucketStart = getBucketStart(tick.timestamp, interval);
  
  if (!candles.length) {
    return [{
      timestamp: bucketStart,
      open: tick.o,
      high: tick.h,
      low: tick.l,
      close: tick.lp,
      volume: tick.v,
      partial: true,
    }];
  }

  const next = candles.slice();
  const last = next[next.length - 1];
  const lastTime = new Date(last.timestamp).getTime();
  const targetTime = new Date(bucketStart).getTime();

  if (lastTime === targetTime) {
    if (interval === "1m") {
      next[next.length - 1] = {
        timestamp: bucketStart,
        open: tick.o,
        high: tick.h,
        low: tick.l,
        close: tick.lp,
        volume: tick.v,
        partial: true,
      };
    } else {
      let volDelta = 0;
      if (lastTick) {
        const lastTickTime = new Date(lastTick.timestamp);
        const currentTickTime = new Date(tick.timestamp);
        if (lastTickTime.getMinutes() === currentTickTime.getMinutes() && lastTickTime.getDate() === currentTickTime.getDate()) {
          volDelta = Math.max(0, tick.v - lastTick.v);
        } else {
          volDelta = tick.v;
        }
      }
      next[next.length - 1] = {
        ...last,
        high: Math.max(last.high, tick.h, tick.lp),
        low: Math.min(last.low, tick.l, tick.lp),
        close: tick.lp,
        volume: last.volume + volDelta,
        partial: true,
      };
    }
  } else if (lastTime < targetTime) {
    next.push({
      timestamp: bucketStart,
      open: tick.o,
      high: tick.h,
      low: tick.l,
      close: tick.lp,
      volume: tick.v,
      partial: true,
    });
  }
  return next;
}

function priceRange(candles) {
  if (!candles.length) return { min: 0, max: 1 };
  const low = Math.min(...candles.map((candle) => candle.low));
  const high = Math.max(...candles.map((candle) => candle.high));
  const padding = Math.max((high - low) * 0.08, high * 0.0008, 1);
  return { min: low - padding, max: high + padding };
}

function OhlcvChart({ candles }) {
  const canvasRef = useRef(null);
  const [hover, setHover] = useState(null);
  const visible = candles;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(rect.width * dpr);
    canvas.height = Math.floor(rect.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const width = rect.width;
    const height = rect.height;
    const plot = { left: 58, right: 18, top: 20, bottom: 96 };
    const volumeTop = height - 78;
    const chartHeight = volumeTop - plot.top - 18;
    const chartWidth = width - plot.left - plot.right;

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#f8f7f4";
    ctx.fillRect(0, 0, width, height);

    if (!visible.length) {
      ctx.fillStyle = "#6d6a64";
      ctx.font = "14px Inter, system-ui, sans-serif";
      ctx.fillText("No candles available for this selection.", 24, 42);
      return;
    }

    const { min, max } = priceRange(visible);
    const priceToY = (price) => plot.top + ((max - price) / (max - min)) * chartHeight;
    const maxVolume = Math.max(...visible.map((candle) => candle.volume), 1);
    const step = chartWidth / visible.length;
    const bodyWidth = Math.max(3, Math.min(14, step * 0.58));

    ctx.strokeStyle = "#e1ded7";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#6d6a64";
    ctx.font = "11px Inter, system-ui, sans-serif";

    for (let i = 0; i < 5; i += 1) {
      const y = plot.top + (chartHeight / 4) * i;
      const price = max - ((max - min) / 4) * i;
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(width - plot.right, y);
      ctx.stroke();
      ctx.fillText(price.toFixed(2), 10, y + 4);
    }

    visible.forEach((candle, index) => {
      const x = plot.left + index * step + step / 2;
      const rising = candle.close >= candle.open;
      const color = rising ? "#0f8b5f" : "#c7433e";
      const yOpen = priceToY(candle.open);
      const yClose = priceToY(candle.close);
      const yHigh = priceToY(candle.high);
      const yLow = priceToY(candle.low);
      const bodyTop = Math.min(yOpen, yClose);
      const bodyHeight = Math.max(1, Math.abs(yClose - yOpen));

      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(x, yHigh);
      ctx.lineTo(x, yLow);
      ctx.stroke();
      ctx.fillRect(x - bodyWidth / 2, bodyTop, bodyWidth, bodyHeight);

      const volumeHeight = (candle.volume / maxVolume) * 56;
      ctx.globalAlpha = 0.32;
      ctx.fillRect(x - bodyWidth / 2, height - 20 - volumeHeight, bodyWidth, volumeHeight);
      ctx.globalAlpha = 1;
    });

    ctx.strokeStyle = "#d8d4ca";
    ctx.beginPath();
    ctx.moveTo(plot.left, volumeTop);
    ctx.lineTo(width - plot.right, volumeTop);
    ctx.stroke();

    const first = visible[0];
    const last = visible[visible.length - 1];
    ctx.fillStyle = "#6d6a64";
    ctx.fillText(new Date(first.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }), plot.left, height - 6);
    ctx.fillText(new Date(last.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }), width - 84, height - 6);

    if (hover !== null && hover >= 0 && hover < visible.length) {
      const x = plot.left + hover * step + step / 2;
      ctx.strokeStyle = "#2e2b26";
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(x, plot.top);
      ctx.lineTo(x, height - 20);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }, [visible, hover]);

  function updateHover(event) {
    const canvas = canvasRef.current;
    if (!canvas || !visible.length) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const plotLeft = 58;
    const plotRight = 18;
    const chartWidth = rect.width - plotLeft - plotRight;
    const step = chartWidth / visible.length;
    const index = Math.floor((x - plotLeft) / step);
    setHover(Math.max(0, Math.min(visible.length - 1, index)));
  }

  const hovered = hover !== null ? visible[hover] : visible[visible.length - 1];

  return (
    <section className="chart-shell">
      <div className="chart-head">
        <div>
          <p className="eyebrow">OHLCV</p>
          <h2>Candles and volume</h2>
        </div>
        {hovered ? (
          <div className="ohlc-strip">
            <span>O {hovered.open.toFixed(2)}</span>
            <span>H {hovered.high.toFixed(2)}</span>
            <span>L {hovered.low.toFixed(2)}</span>
            <span>C {hovered.close.toFixed(2)}</span>
            <span>V {hovered.volume.toLocaleString("en-IN")}</span>
          </div>
        ) : null}
      </div>
      <canvas
        ref={canvasRef}
        className="ohlcv-canvas"
        onMouseMove={updateHover}
        onMouseLeave={() => setHover(null)}
      />
    </section>
  );
}

export default function App() {
  const [instruments, setInstruments] = useState([]);
  const [symbol, setSymbol] = useState("");
  const [interval, setInterval] = useState("1m");
  const [windowSize, setWindowSize] = useState(120);
  const [candles, setCandles] = useState([]);
  const [marketTime, setMarketTime] = useState("");
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [error, setError] = useState("");
  const [latestTick, setLatestTick] = useState(null);

  useEffect(() => {
    let active = true;
    async function boot() {
      try {
        const [health, rows] = await Promise.all([getHealth(), getInstruments()]);
        if (!active) return;
        setMarketTime(health.virtual_time);
        setInstruments(rows);
        setSymbol(rows[0]?.symbol || "");
      } catch (err) {
        setError(err.message);
      } finally {
        if (active) setLoading(false);
      }
    }
    boot();
    return () => {
      active = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!symbol) return;
    setError("");
    try {
      const health = await getHealth();
      const endDate = health.virtual_time;
      const data = await getHistoricalCandles({
        symbol,
        interval,
        endDate,
        ...(windowSize > 0 ? { limit: windowSize } : { startDate: marketOpenFor(endDate) }),
      });
      setCandles(data.candles);
      setMarketTime(data.marketTime || endDate);
      setLatestTick(null);
    } catch (err) {
      setError(err.message);
    }
  }, [interval, symbol, windowSize]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!autoRefresh || !symbol) return undefined;

    const lastTickRef = { current: null };
    const socket = new WebSocket(tickStreamUrl(symbol));
    socket.onmessage = (event) => {
      try {
        const tick = JSON.parse(event.data);
        setError("");
        setMarketTime(tick.timestamp);
        setLatestTick(tick);
        setCandles((rows) => mergeTick(rows, tick, interval, lastTickRef.current));
        lastTickRef.current = tick;
      } catch (err) {
        setError(err.message);
      }
    };
    socket.onerror = () => {
      setError("Live tick stream disconnected. Check that the mock server is running.");
    };

    return () => socket.close();
  }, [autoRefresh, interval, symbol]);

  const visibleCandles = useMemo(() => trimCandles(candles, windowSize), [candles, windowSize]);
  const latest = candles[candles.length - 1];
  const selectedInstrument = instruments.find((item) => item.symbol === symbol);

  return (
    <main className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">VB</span>
          <div>
            <h1>Virtual Broker</h1>
            <p>Mock market data viewer</p>
          </div>
        </div>
        <div className="market-clock" title="Time reported by the mock server">
          <span aria-hidden="true">Time</span>
          <span>{formatMarketTime(marketTime)}</span>
        </div>
      </header>

      <section className="workspace">
        <aside className="controls" aria-label="Chart controls">
          <label>
            Script
            <select value={symbol} onChange={(event) => setSymbol(event.target.value)} disabled={!instruments.length}>
              {instruments.map((instrument) => (
                <option key={instrument.ref_id} value={instrument.symbol}>
                  {instrument.symbol}
                </option>
              ))}
            </select>
          </label>

          <label>
            Interval
            <select value={interval} onChange={(event) => setInterval(event.target.value)}>
              {INTERVALS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>

          <label>
            Range
            <select value={windowSize} onChange={(event) => setWindowSize(Number(event.target.value))}>
              {WINDOWS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>

          <button className="primary-button" type="button" onClick={refresh}>
            Refresh
          </button>

          <button
            className={autoRefresh ? "toggle active" : "toggle"}
            type="button"
            onClick={() => setAutoRefresh((value) => !value)}
            aria-pressed={autoRefresh}
          >
            Live ticks
          </button>

          <div className="instrument-meta">
            <span>Exchange</span>
            <strong>{selectedInstrument?.exchange || "--"}</strong>
            <span>Ref ID</span>
            <strong>{selectedInstrument?.ref_id || "--"}</strong>
            <span>Candles</span>
            <strong>{visibleCandles.length.toLocaleString("en-IN")}</strong>
            <span>Last tick</span>
            <strong>{latestTick ? latestTick.lp.toFixed(2) : "--"}</strong>
          </div>
        </aside>

        <div className="main-panel">
          <div className="summary-row">
            <div>
              <span>Selected script</span>
              <strong>{symbol || "--"}</strong>
            </div>
            <div>
              <span>Latest close</span>
              <strong>{latest ? latest.close.toFixed(2) : "--"}</strong>
            </div>
            <div>
              <span>Latest volume</span>
              <strong>{latest ? latest.volume.toLocaleString("en-IN") : "--"}</strong>
            </div>
            <div>
              <span>Last candle</span>
              <strong>{latest ? new Date(latest.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : "--"}</strong>
            </div>
          </div>

          {error ? <div className="error-banner">{error}</div> : null}
          {loading ? <div className="loading">Loading instruments...</div> : <OhlcvChart candles={visibleCandles} />}
        </div>
      </section>
    </main>
  );
}
