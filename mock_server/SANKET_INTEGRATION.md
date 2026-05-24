# Sanket Engine Integration

Use the mock broker as the Nubra-compatible endpoint for local strategy testing.

## Server

Start the mock server from `C:\Projects\GitHub\virtual-broker`:

```powershell
$env:START_DATE = "2024-02-23"
uvicorn mock_server.main:app --host 127.0.0.1 --port 8765
```

Optional environment variables:

| Variable | Default | Description |
|---|---|---|
| `START_DATE` | first date in dataset | Trading date for the virtual clock |
| `MOCK_BROKER_DATA_DIR` | `./1m_Interval` | Override CSV data folder |
| `MOCK_BROKER_STATE` | `mock_server/session_state.json` | Persisted orders and clock position |
| `MOCK_BROKER_SEED` | `nubra-mock` | Seed for deterministic tick projection |
| `MOCK_BROKER_SPEED` | `1.0` | Virtual seconds per real second |
| `MOCK_BROKER_INITIAL_BALANCE` | `1000000000` | Opening balance in paise (₹1 crore) |

## Engine Configuration

Configure `C:\Projects\GitHub\sanket-engine` to use:

- REST base URL: `http://127.0.0.1:8765`
- WebSocket URL: `ws://127.0.0.1:8765/ws/ticks`
- Access token: value returned by `POST /generate_session`, currently `mock_access_token`
- Exchange: `NSE`
- Symbols: use either `SBIN`/`RELIANCE` or source-file aliases such as `SBIN.NS`

## Method Mapping

| Sanket/Nubra call | Mock endpoint |
|---|---|
| `generate_session(api_key, api_secret)` | `POST /generate_session` |
| `InstrumentData.get_instruments_dataframe()` | `GET /get_instruments` |
| `InstrumentData.get_instrument_by_symbol("SBIN")` | `GET /instrument/SBIN` |
| `MarketData.current_price(symbol)` | `GET /current_price/{symbol}` |
| `MarketData.quote(ref_id, levels)` | `GET /quote?ref_id={ref_id}&levels={levels}` |
| `MarketData.historical_data(request)` | `POST /historical_data` |
| `NubraTrader.create_order(request)` | `POST /create_order` |
| `NubraTrader.cancel_orders_v2(order_ids=[...])` | `POST /cancel_orders_v2` |
| `NubraTrader.modify_order_v2(request)` | `POST /modify_order_v2` |
| `NubraTrader.orders()` | `GET /orders` |
| `NubraTrader.get_order(order_id)` | `GET /orders/{order_id}` |
| `NubraPortfolio.positions(version="V2")` | `GET /positions` |
| `NubraPortfolio.holdings()` | `GET /holdings` |
| `NubraPortfolio.funds()` | `GET /funds` |
| `NubraTrader.get_margin(request)` | `POST /get_margin` |

## Order Types

| `price_type` | Behaviour |
|---|---|
| `MARKET` | Fill immediately at current LTP |
| `LIMIT` | Pending; fill when LTP crosses limit price |
| `IOC` | Fill immediately if price available; otherwise cancel |
| `STOPLOSS` / `SL` / `SL-M` | Pending; fill at LTP when trigger price is crossed |
| `SL-L` / `SL_LIMIT` | Pending; fill at `order_price` when trigger crossed and limit met |

For stoploss orders set `algo_params.trigger_price` as the activation level. If omitted, `order_price` is used as the trigger.

## Market Hours

- Orders are only accepted between `09:15` and `15:30` virtual time.
- Pending `DAY` orders are automatically cancelled at `15:30`.
- The virtual clock advances across trading days automatically — after `15:30` it resumes at `09:15` on the next date in the dataset.
- Clock position is saved on shutdown and restored on restart, so the portfolio state and time position stay consistent across restarts.

## Historical Candles

The mock server returns only data at or before virtual broker time. It includes active partial higher-timeframe candles by default, so a strategy can observe a forming `5m` or `15m` candle without look-ahead.

If Sanket expects only completed candles, set this in historical requests:

```json
{ "includePartial": false }
```

## WebSocket

Subscribe by symbols at connection time:

```
ws://127.0.0.1:8765/ws/ticks?symbols=SBIN,RELIANCE
```

Subscribe by Nubra-style ref IDs:

```
ws://127.0.0.1:8765/ws/ticks?ref_ids=100001,100002
```

After connecting, send JSON messages to change subscriptions at runtime:

```json
{"action": "subscribe",   "symbols": ["INFY", "TCS"]}
{"action": "unsubscribe", "symbols": ["TCS"]}
```

Each tick is pushed as an individual message once per second:

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

## Example Requests

### Place a limit order
```json
POST /create_order
{
  "ref_id": 100001,
  "order_qty": 10,
  "order_side": "ORDER_SIDE_BUY",
  "order_delivery_type": "ORDER_DELIVERY_TYPE_CNC",
  "validity_type": "DAY",
  "price_type": "LIMIT",
  "order_price": 76000,
  "tag": "my_strategy"
}
```

### Place a stoploss order
```json
POST /create_order
{
  "ref_id": 100001,
  "order_qty": 10,
  "order_side": "ORDER_SIDE_SELL",
  "price_type": "SL-L",
  "order_price": 75000,
  "algo_params": {"trigger_price": 75500}
}
```

### Cancel orders
```json
POST /cancel_orders_v2
{"order_ids": [1, 2, 3]}
```

### Modify an order
```json
POST /modify_order_v2
{"order_id": 1, "order_qty": 20, "order_price": 77000}
```

### Batch quotes
```json
POST /quotes
{"symbols": ["SBIN", "RELIANCE"], "levels": 5}
```
