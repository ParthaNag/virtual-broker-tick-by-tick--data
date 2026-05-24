const API_BASE = "/api";

async function request(path, options) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }

  return response.json();
}

export async function getHealth() {
  return request("/health");
}

export async function getInstruments() {
  const data = await request("/instruments");
  return data.instruments || data.result || [];
}

export async function getHistoricalCandles({ symbol, interval, startDate, endDate, limit }) {
  const data = await request("/historical_data", {
    method: "POST",
    body: JSON.stringify({
      symbol,
      interval,
      startDate,
      endDate,
      limit,
      includePartial: true,
    }),
  });

  const candles = data.candles?.[symbol] || [];
  return { candles, marketTime: data.market_time };
}

export function tickStreamUrl(symbol) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams({ symbols: symbol });
  return `${protocol}//${window.location.host}${API_BASE}/ws/ticks?${params.toString()}`;
}
