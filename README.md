# Virtual Broker: High-Fidelity Market Data & Trading Simulator

Virtual Broker is a complete pairing of a **High-Fidelity Mock Broker Server** (built with FastAPI) and a **Real-Time Interactive UI** (built with React and Vite). It allows developers and traders to simulate live trading environments, backtest strategies, and visualize market feeds using historical 1-minute data transformed into 1-second ticks.

---

## 🚀 Key Features

*   **⏱️ Virtual clock & temporal isolation**: Strictly enforces data visibility up to the current virtual time (starts at `09:15:00` on the configured start date).
*   **📊 Multi-resolution resampling**: Supports 1m, 3m, 5m, 10m, 15m, 30m, 1h, and 1d intervals.
*   **📡 Real-time websockets**: Stream 1-second price ticks dynamically to any subscriber.
*   **📈 Interactive UI**: Beautiful OHLCV candlestick chart with volume bars, script selector, and configurable range limit filters.
*   **📝 Configuration flexibility**: Set the start date via `config.py`, `config.yaml`, `config.json`, or environment variables.
*   **💼 Mock Order Management (OMS)**: Supports `MARKET`, `LIMIT`, `IOC`, `STOPLOSS`, `SL-L` order types, real-time positions, margins, and automatic portfolio state persistence.

---

## 🛠️ Project Structure

```text
/virtual-broker
  ├── 1m_Interval/        # Source CSV data folder (e.g., SBIN.NS.csv)
  ├── mock_server/        # Python Mock Broker Server
  │     ├── main.py       # API entrypoint and WebSocket routing
  │     ├── engine.py     # Virtual Clock & tick projection engine
  │     └── oms.py        # Order management, positions, and margin system
  ├── src/                # React (Vite) UI source code
  │     ├── App.jsx       # Chart & controls layout
  │     ├── api.js        # API endpoints integrations
  │     └── styles.css    # Premium CSS design variables and layouts
  ├── config.py           # Local configuration overrides (e.g., START_DATE)
  ├── start_mock_server.bat
  └── start_ui.bat
```

---

## ⚡ Quick Start

### 1. Configure the Start Date
You can set your virtual start date inside the template file `config.py` in the root folder:
```python
START_DATE = "2024-02-26"
```
*Note: You can also specify the start date in a `config.yaml`, `config.json`, or by setting the `START_DATE` environment variable.*

### 2. Start the Mock Server
In one terminal, run:
```powershell
.\start_mock_server.bat
```
The server will start on `http://127.0.0.1:8765`.

### 3. Start the React UI
In a second terminal, run:
```powershell
.\start_ui.bat
```
This will install any missing dependencies and start Vite on `http://127.0.0.1:5173`. Open this URL in your browser.

---

## ⚙️ Configuration Variables (Server)

| Variable | Default Source | Description |
|---|---|---|
| `START_DATE` | `config.py` / Environment | Virtual clock start date (e.g. `2024-02-26`) |
| `MOCK_BROKER_DATA_DIR` | `./1m_Interval` | Folder path for stock CSV files |
| `MOCK_BROKER_STATE` | `mock_server/session_state.json` | Local file to persist session/portfolio state |
| `MOCK_BROKER_SPEED` | `1.0` | Simulation speed (virtual seconds per wall second) |
| `MOCK_BROKER_INITIAL_BALANCE` | `1000000000` | Opening mock balance in Paise (Default: ₹1 Crore) |

---

## 🔌 API Endpoints Reference

### Sessions & Master Data
*   `POST /generate_session` - Generate dynamic mock access tokens.
*   `GET /get_instruments` - List all discoverable stock symbols.
*   `GET /instrument/{symbol}` - Fetch details of a specific instrument.

### Market Data
*   `POST /historical_data` - Fetch historical candles. Supports `limit` query parameters for cross-day loading.
*   `GET /quote` - Fetch live market depth levels.
*   `POST /quotes` - Batch quotes query.
*   `GET /current_price/{symbol}` - Get LTP and daily price change percentage.

### Trading & Orders
*   `POST /create_order` / `POST /place_order` - Place market, limit, stop-loss orders.
*   `POST /cancel_order` / `DELETE /orders/{order_id}` - Cancel order.
*   `GET /orders` - Fetch all orders history.
*   `GET /positions` / `GET /holdings` - Real-time portfolio tracking.
*   `GET /margins` - Check available cash and funds.
