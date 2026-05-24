# Nubra Mock Broker Server

This server implements the high-fidelity mock broker described in `mock-broker.md`.

## Active Candle Policy

Historical data includes the active partial candle by default. This applies to `1m` and higher intervals such as `5m`, `15m`, and `1h`.

That means a request at virtual time `09:20:17` for `5m` data can include:

- completed `09:15:00` bucket
- active `09:20:00` bucket, built only from data visible up to `09:20:17`

Set `includePartial` or `include_partial` to `false` in `/historical_data` to return only completed candles.

## Run

```powershell
pip install fastapi uvicorn
$env:START_DATE = "2024-02-23"
uvicorn mock_server.main:app --host 127.0.0.1 --port 8765
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `START_DATE` | first date in `1m_Interval` | Trading date for the virtual clock |
| `MOCK_BROKER_DATA_DIR` | `./1m_Interval` | Override CSV data folder |
| `MOCK_BROKER_STATE` | `mock_server/session_state.json` | Path for persisted orders and clock position |
| `MOCK_BROKER_SEED` | `nubra-mock` | Seed for deterministic tick projection |
| `MOCK_BROKER_SPEED` | `1.0` | Virtual seconds per real second |
| `MOCK_BROKER_INITIAL_BALANCE` | `1000000000` | Opening balance in paise (default = ₹1 crore) |

## Multi-Day Simulation

The virtual clock advances across trading days automatically. After `15:30` it skips overnight hours and resumes at `09:15` on the next available date in the dataset. The clock position is saved to `session_state.json` on shutdown and restored on restart.

## Market Hours Enforcement

Order placement is rejected outside `09:15–15:30` virtual time. Pending `DAY` orders are automatically cancelled when the clock reaches `15:30`.

## Order Types Supported

| `price_type` | Behaviour |
|---|---|
| `MARKET` | Fill immediately at current LTP |
| `LIMIT` | Store as pending; fill when LTP crosses limit price |
| `IOC` | Fill immediately if price is available; otherwise cancel |
| `STOPLOSS` / `SL` / `SL-M` | Pending; fill at LTP when trigger price is crossed |
| `SL-L` / `SL_LIMIT` | Pending; fill at `order_price` when trigger price crossed and limit met |

For stoploss orders, `algo_params.trigger_price` is the activation level. If omitted, `order_price` is used as the trigger.

## Nubra-Compatible Endpoints

### Session & Instruments
- `POST /generate_session`
- `GET /get_instruments`
- `GET /instrument/{symbol}`

### Market Data
- `GET /quote?ref_id=100001&levels=5`
- `POST /quotes` — batch quotes by `ref_ids` or `symbols`
- `GET /current_price/{symbol}`
- `POST /historical_data`

### Trading
- `POST /create_order` (alias: `/place_order`)
- `POST /cancel_order` — body: `{"order_id": 1}`
- `DELETE /orders/{order_id}`
- `POST /cancel_orders_v2` — body: `{"order_ids": [1, 2]}`
- `POST /modify_order` (alias: `/modify_order_v2`)
- `GET /orders` (alias: `/get_order_history`)
- `GET /orders/{order_id}`

### Portfolio
- `GET /positions` (alias: `/get_positions`)
- `GET /holdings` (alias: `/get_holdings`)
- `GET /funds` (aliases: `/margins`, `/get_margins`)
- `POST /get_margin`

### WebSocket
- `WS /ws/ticks?symbols=SBIN,RELIANCE`
- `WS /ws/ticks?ref_ids=100001,100002`

## Historical Data Request Example

```json
{
  "exchange": "NSE",
  "type": "STOCK",
  "values": ["SBIN", "RELIANCE"],
  "fields": ["open", "high", "low", "close", "cumulative_volume"],
  "startDate": "2024-02-23T09:15:00+05:30",
  "endDate": "2024-02-23T10:15:00+05:30",
  "interval": "5m",
  "includePartial": true
}
```

## Order Request Example

```json
{
  "ref_id": 100001,
  "order_type": "ORDER_TYPE_REGULAR",
  "order_qty": 10,
  "order_side": "ORDER_SIDE_BUY",
  "order_delivery_type": "ORDER_DELIVERY_TYPE_CNC",
  "validity_type": "DAY",
  "price_type": "LIMIT",
  "order_price": 76000,
  "exchange": "NSE",
  "tag": "my_strategy"
}
```

## Stoploss Order Example

```json
{
  "ref_id": 100001,
  "order_qty": 10,
  "order_side": "ORDER_SIDE_SELL",
  "price_type": "SL-L",
  "order_price": 75000,
  "algo_params": {"trigger_price": 75500}
}
```

## WebSocket Dynamic Subscription

After connecting, send JSON messages to change subscriptions at runtime:

```json
{"action": "subscribe",   "symbols": ["INFY", "TCS"]}
{"action": "unsubscribe", "symbols": ["TCS"]}
```

Each tick is pushed as an individual message:

```json
{
  "type": "tick",
  "symbol": "SBIN",
  "lp": 764.4,
  "v": 0,
  "o": 767.2,
  "h": 767.2,
  "l": 764.4,
  "c": 764.4,
  "timestamp": "2024-02-23T09:15:00+05:30"
}
```
