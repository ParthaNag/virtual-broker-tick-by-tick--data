from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect

from .engine import (
    Candle,
    Instrument,
    create_default_stack,
    epoch_ms,
    parse_datetime,
    to_paise,
)
from .oms import MockOMS


store, clock, simulator, _state_path = create_default_stack()
oms = MockOMS(
    simulator,
    _state_path,
    initial_balance=int(os.getenv("MOCK_BROKER_INITIAL_BALANCE", "1000000000")),
)


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[type-arg]
    yield
    oms._save()


app = FastAPI(title="Nubra High-Fidelity Mock Broker", version="0.2.0", lifespan=lifespan)


# ------------------------------------------------------------------ helpers

def instrument_payload(instrument: Instrument) -> dict[str, Any]:
    return {
        "ref_id": instrument.ref_id,
        "symbol": instrument.symbol,
        "exchange": instrument.exchange,
        "asset": instrument.asset,
        "asset_type": instrument.asset,
        "nubra_name": instrument.nubra_name,
        "tick_size": instrument.tick_size,
        "lot_size": instrument.lot_size,
        "trading_symbol": instrument.symbol,
        "display_name": instrument.symbol,
    }


def order_levels(ltp: int, levels: int) -> tuple[list[dict[str, int]], list[dict[str, int]]]:
    depth = max(1, min(20, levels))
    tick = 5
    bid = [{"price": ltp - (i * tick), "quantity": 100 * i, "num_orders": i} for i in range(1, depth + 1)]
    ask = [{"price": ltp + (i * tick), "quantity": 100 * i, "num_orders": i} for i in range(1, depth + 1)]
    return bid, ask


def stock_chart(candles: list[Candle], fields: list[str]) -> dict[str, Any]:
    wanted = set(fields or ["open", "high", "low", "close", "cumulative_volume"])
    aliases = {"volume": "cumulative_volume", "tick_volume": "cumulative_volume"}
    wanted = {aliases.get(f, f) for f in wanted}
    payload: dict[str, Any] = {}
    for field in ["open", "high", "low", "close", "cumulative_volume"]:
        if field not in wanted:
            payload[field] = None
            continue
        if field == "cumulative_volume":
            payload[field] = [{"timestamp": epoch_ms(row.timestamp), "value": row.volume} for row in candles]
        else:
            payload[field] = [{"timestamp": epoch_ms(row.timestamp), "value": to_paise(getattr(row, field))} for row in candles]
    for field in ["cumulative_oi", "theta", "delta", "gamma", "vega", "iv_mid"]:
        payload[field] = None
    return payload


def convenient_candles(candles: list[Candle]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": row.timestamp.isoformat(),
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
            "partial": row.timestamp == simulator.clock.now.replace(second=0, microsecond=0),
        }
        for row in candles
    ]


# ------------------------------------------------------------------ session / instruments

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "virtual_time": clock.now.isoformat(),
        "symbols": len(store.instruments),
        "partial_active_candles": True,
    }


@app.post("/generate_session")
def generate_session(_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "message": "success",
        "access_token": "mock_access_token",
        "token_type": "Bearer",
        "virtual_time": clock.now.isoformat(),
    }


@app.get("/instruments")
@app.get("/get_instruments")
def get_instruments() -> dict[str, Any]:
    rows = [instrument_payload(item) for item in store.instruments.values()]
    return {"message": "success", "result": rows, "instruments": rows}


@app.get("/instrument/{symbol}")
def get_instrument_by_symbol(symbol: str) -> dict[str, Any]:
    try:
        return instrument_payload(store.instrument_for_symbol(symbol))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ------------------------------------------------------------------ market data

@app.get("/quote")
def quote(ref_id: int, levels: int = 5) -> dict[str, Any]:
    try:
        instrument = store.instrument_for_ref_id(ref_id)
        tick = simulator.current_tick(instrument.symbol)
        ltp = to_paise(tick["lp"]) or 0
        bid, ask = order_levels(ltp, levels)
        return {
            "orderBook": {
                "ref_id": ref_id,
                "timestamp": epoch_ms(clock.now),
                "bid": bid,
                "ask": ask,
                "last_traded_price": ltp,
                "last_traded_quantity": 1,
                "volume": tick["v"],
            }
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/quotes")
def batch_quotes(request: dict[str, Any]) -> dict[str, Any]:
    results = []
    levels = int(request.get("levels", 5))
    for ref_id in request.get("ref_ids", []):
        try:
            instrument = store.instrument_for_ref_id(int(ref_id))
            tick = simulator.current_tick(instrument.symbol)
            ltp = to_paise(tick["lp"]) or 0
            bid, ask = order_levels(ltp, levels)
            results.append({"orderBook": {
                "ref_id": instrument.ref_id, "symbol": instrument.symbol,
                "timestamp": epoch_ms(clock.now), "bid": bid, "ask": ask,
                "last_traded_price": ltp, "last_traded_quantity": 1, "volume": tick["v"],
            }})
        except KeyError:
            pass
    for sym in request.get("symbols", []):
        try:
            instrument = store.instrument_for_symbol(str(sym))
            tick = simulator.current_tick(instrument.symbol)
            ltp = to_paise(tick["lp"]) or 0
            bid, ask = order_levels(ltp, levels)
            results.append({"orderBook": {
                "ref_id": instrument.ref_id, "symbol": instrument.symbol,
                "timestamp": epoch_ms(clock.now), "bid": bid, "ask": ask,
                "last_traded_price": ltp, "last_traded_quantity": 1, "volume": tick["v"],
            }})
        except KeyError:
            pass
    return {"quotes": results, "count": len(results)}


@app.get("/current_price/{symbol}")
def current_price(symbol: str) -> dict[str, Any]:
    try:
        instrument = store.instrument_for_symbol(symbol)
        tick = simulator.current_tick(instrument.symbol)
        rows = store.candles[instrument.symbol]
        prev_close = rows[0].close
        change = ((tick["lp"] - prev_close) / prev_close) * 100 if prev_close else 0.0
        return {
            "message": "success",
            "exchange": instrument.exchange,
            "price": to_paise(tick["lp"]),
            "prev_close": to_paise(prev_close),
            "change": round(change, 4),
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/historical_data")
def historical_data(request: dict[str, Any]) -> dict[str, Any]:
    try:
        values = request.get("values") or request.get("symbols") or request.get("symbol") or []
        symbols = [values] if isinstance(values, str) else list(values)
        interval = request.get("interval", "1m")
        fields = request.get("fields") or ["open", "high", "low", "close", "cumulative_volume"]
        end = parse_datetime(request.get("endDate") or request.get("to_date")) or clock.now
        start = parse_datetime(request.get("startDate") or request.get("from_date")) or (end - timedelta(days=1))
        include_partial = bool(request.get("includePartial", request.get("include_partial", True)))

        chart_values = []
        raw_values = {}
        for symbol in symbols:
            resolved = store.resolve_symbol(symbol)
            candles = simulator.historical_candles(resolved, interval, start, end, include_partial=include_partial)
            chart_values.append({resolved: stock_chart(candles, fields)})
            raw_values[resolved] = convenient_candles(candles)

        return {
            "market_time": clock.now.isoformat(),
            "message": "success",
            "result": [
                {
                    "exchange": request.get("exchange", "NSE"),
                    "type": request.get("type", "STOCK"),
                    "values": chart_values,
                }
            ],
            "candles": raw_values,
            "include_partial": include_partial,
        }
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ------------------------------------------------------------------ trading

@app.post("/create_order")
@app.post("/place_order")
def create_order(request: dict[str, Any]) -> dict[str, Any]:
    try:
        return oms.create_order(request)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/cancel_order")
def cancel_order_single(request: dict[str, Any]) -> dict[str, Any]:
    try:
        return oms.cancel_order(int(request["order_id"]))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/orders/{order_id}")
def cancel_order_by_id(order_id: int) -> dict[str, Any]:
    try:
        return oms.cancel_order(order_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/cancel_orders_v2")
def cancel_orders_v2(request: dict[str, Any]) -> dict[str, Any]:
    order_ids = [int(i) for i in request.get("order_ids", [])]
    return oms.cancel_orders(order_ids)


@app.post("/modify_order")
@app.post("/modify_order_v2")
def modify_order(request: dict[str, Any]) -> dict[str, Any]:
    try:
        return oms.modify_order(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/orders")
@app.get("/get_order_history")
def orders(live: bool = False, executed: bool = False, tag: str | None = None) -> dict[str, Any]:
    return oms.orders(live=live, executed=executed, tag=tag)


@app.get("/orders/{order_id}")
def get_order(order_id: int) -> dict[str, Any]:
    try:
        return oms.get_order(order_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ------------------------------------------------------------------ portfolio

@app.get("/positions")
@app.get("/get_positions")
def positions() -> dict[str, Any]:
    return oms.positions()


@app.get("/holdings")
@app.get("/get_holdings")
def holdings() -> dict[str, Any]:
    return oms.holdings()


@app.get("/funds")
@app.get("/margins")
@app.get("/get_margins")
def funds() -> dict[str, Any]:
    return oms.funds()


@app.post("/get_margin")
def get_margin(request: dict[str, Any]) -> dict[str, Any]:
    return oms.get_margin(request)


# ------------------------------------------------------------------ WebSocket

@app.websocket("/ws/ticks")
async def ticks(
    websocket: WebSocket,
    symbols: str = Query(default=""),
    ref_ids: str = Query(default=""),
) -> None:
    await websocket.accept()

    try:
        if ref_ids:
            subscribed: set[str] = {
                store.instrument_for_ref_id(int(item)).symbol
                for item in ref_ids.split(",")
                if item
            }
        elif symbols:
            subscribed = {store.resolve_symbol(item) for item in symbols.split(",") if item}
        else:
            subscribed = set(list(store.instruments.keys())[:1])
    except (KeyError, ValueError) as exc:
        await websocket.close(code=1008, reason=str(exc))
        return

    async def sender() -> None:
        try:
            while True:
                oms.process_pending()
                for symbol in list(subscribed):
                    await websocket.send_json(simulator.current_tick(symbol))
                await asyncio.sleep(1)
        except Exception:
            pass

    send_task = asyncio.create_task(sender())
    try:
        while True:
            try:
                msg = await websocket.receive_json()
            except (WebSocketDisconnect, RuntimeError):
                break
            action = str(msg.get("action", "")).lower()
            sym_list = msg.get("symbols", [])
            if isinstance(sym_list, str):
                sym_list = [sym_list]
            resolved: list[str] = []
            for s in sym_list:
                try:
                    resolved.append(store.resolve_symbol(s))
                except KeyError:
                    pass
            if action == "subscribe":
                subscribed.update(resolved)
            elif action == "unsubscribe":
                subscribed.difference_update(resolved)
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
