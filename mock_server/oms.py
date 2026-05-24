from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .engine import (
    IST,
    MARKET_CLOSE,
    MARKET_OPEN,
    Instrument,
    MarketSimulator,
    epoch_ms,
    to_paise,
)

_STOPLOSS_TYPES = {"STOPLOSS", "SL", "SL_MARKET", "SL-M", "PRICE_TYPE_SL_MARKET"}
_SL_LIMIT_TYPES = {"SL_LIMIT", "SL-L", "PRICE_TYPE_SL_LIMIT"}
_IOC_TYPES = {"IOC", "PRICE_TYPE_IOC"}
_MARKET_TYPES = {"MARKET", "PRICE_TYPE_MARKET"}


class MockOMS:
    def __init__(self, simulator: MarketSimulator, state_path: Path, initial_balance: int = 1_000_000_000) -> None:
        self.simulator = simulator
        self.state_path = state_path
        self._initial_balance = initial_balance
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {
            "next_order_id": 1,
            "opening_balance": self._initial_balance,
            "orders": [],
        }

    def _save(self) -> None:
        self.state["clock_virtual_time"] = self.simulator.clock.now.isoformat()
        self.state_path.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")

    # ------------------------------------------------------------------ market hours

    def _assert_market_open(self, now: datetime) -> None:
        t = now.astimezone(IST).time()
        if t < MARKET_OPEN or t >= MARKET_CLOSE:
            raise ValueError(f"Market is closed at {t.isoformat()}; orders only accepted between {MARKET_OPEN} and {MARKET_CLOSE}")

    # ------------------------------------------------------------------ orders

    def create_order(self, request: dict[str, Any]) -> dict[str, Any]:
        instrument = self._instrument_from_request(request)
        self.process_pending()

        now = self.simulator.clock.now
        self._assert_market_open(now)

        order_id = int(self.state["next_order_id"])
        self.state["next_order_id"] = order_id + 1
        ltp = to_paise(self.simulator.current_tick(instrument.symbol, now)["lp"]) or 0
        price_type = str(request.get("price_type") or request.get("order_type") or "LIMIT").upper()
        order_price_raw = request.get("order_price")
        order_price = int(order_price_raw) if order_price_raw is not None else ltp

        order: dict[str, Any] = {
            "order_id": order_id,
            "exchange_order_id": f"MOCK-{order_id}",
            "ref_id": instrument.ref_id,
            "order_type": request.get("order_type", "ORDER_TYPE_REGULAR"),
            "order_side": request.get("order_side") or self._side(request),
            "order_price": order_price,
            "order_qty": int(request.get("order_qty") or request.get("quantity") or 1),
            "filled_qty": 0,
            "avg_filled_price": None,
            "order_status": "PENDING",
            "price_type": price_type,
            "validity_type": request.get("validity_type", "DAY"),
            "order_delivery_type": request.get("order_delivery_type", "ORDER_DELIVERY_TYPE_CNC"),
            "exchange": request.get("exchange", "NSE"),
            "tag": request.get("tag"),
            "algo_params": request.get("algo_params"),
            "creation_time": epoch_ms(now),
            "last_modified_time": epoch_ms(now),
            "last_traded_price": ltp,
            "LTP": ltp,
            "display_name": instrument.symbol,
            "ref_data": self._ref_data(instrument),
        }

        if price_type in _MARKET_TYPES or order_price_raw is None:
            self._fill(order, ltp, now)
        elif price_type in _IOC_TYPES:
            if self._limit_crossed(order, ltp):
                self._fill(order, order_price, now)
            else:
                order["order_status"] = "CANCELLED"
                order["last_modified_time"] = epoch_ms(now)
        elif price_type in _STOPLOSS_TYPES or price_type in _SL_LIMIT_TYPES:
            pass  # stoploss orders always start pending; process_pending handles trigger
        elif self._limit_crossed(order, ltp):
            self._fill(order, order_price, now)

        self.state["orders"].append(order)
        self._save()
        return order

    def cancel_order(self, order_id: int) -> dict[str, Any]:
        self.process_pending()
        for order in self.state["orders"]:
            if int(order["order_id"]) == order_id:
                if order["order_status"] != "PENDING":
                    raise ValueError(f"Order {order_id} has status {order['order_status']} and cannot be cancelled")
                order["order_status"] = "CANCELLED"
                order["last_modified_time"] = epoch_ms(self.simulator.clock.now)
                self._save()
                return order
        raise KeyError(f"Unknown order_id: {order_id}")

    def cancel_orders(self, order_ids: list[int]) -> dict[str, Any]:
        results = []
        errors = []
        for oid in order_ids:
            try:
                results.append(self.cancel_order(oid))
            except (KeyError, ValueError) as exc:
                errors.append({"order_id": oid, "error": str(exc)})
        return {"cancelled": results, "errors": errors}

    def modify_order(self, request: dict[str, Any]) -> dict[str, Any]:
        self.process_pending()
        order_id = int(request.get("order_id") or 0)
        for order in self.state["orders"]:
            if int(order["order_id"]) != order_id:
                continue
            if order["order_status"] != "PENDING":
                raise ValueError(f"Order {order_id} has status {order['order_status']} and cannot be modified")
            for field in ("order_qty", "order_price"):
                if field in request:
                    order[field] = int(request[field])
            for field in ("price_type", "validity_type"):
                if field in request:
                    order[field] = str(request[field]).upper()
            if "algo_params" in request:
                order["algo_params"] = request["algo_params"]
            order["last_modified_time"] = epoch_ms(self.simulator.clock.now)
            self._save()
            return order
        raise KeyError(f"Unknown order_id: {order_id}")

    def orders(self, live: bool = False, executed: bool = False, tag: str | None = None) -> dict[str, Any]:
        self.process_pending()
        rows = list(self.state["orders"])
        if live:
            rows = [r for r in rows if r["order_status"] == "PENDING"]
        if executed:
            rows = [r for r in rows if r["order_status"] == "COMPLETE"]
        if tag:
            rows = [r for r in rows if r.get("tag") == tag]
        return {"root": rows}

    def get_order(self, order_id: int) -> dict[str, Any]:
        self.process_pending()
        for order in self.state["orders"]:
            if int(order["order_id"]) == int(order_id):
                return order
        raise KeyError(f"Unknown order_id: {order_id}")

    # ------------------------------------------------------------------ process_pending

    def process_pending(self) -> None:
        changed = False
        now = self.simulator.clock.now
        is_eod = now.astimezone(IST).time() >= MARKET_CLOSE

        for order in self.state["orders"]:
            if order["order_status"] != "PENDING":
                continue

            instrument = self.simulator.store.instrument_for_ref_id(int(order["ref_id"]))
            ltp = to_paise(self.simulator.current_tick(instrument.symbol, now)["lp"]) or 0
            order["last_traded_price"] = ltp
            order["LTP"] = ltp

            if is_eod and str(order.get("validity_type", "DAY")).upper() == "DAY":
                order["order_status"] = "CANCELLED"
                order["last_modified_time"] = epoch_ms(now)
                changed = True
                continue

            price_type = str(order.get("price_type", "LIMIT")).upper()

            if price_type in _STOPLOSS_TYPES:
                if self._stoploss_triggered(order, ltp):
                    self._fill(order, ltp, now)
                    changed = True
            elif price_type in _SL_LIMIT_TYPES:
                if self._stoploss_triggered(order, ltp) and self._limit_crossed(order, ltp):
                    self._fill(order, int(order["order_price"]), now)
                    changed = True
            elif self._limit_crossed(order, ltp):
                self._fill(order, int(order["order_price"]), now)
                changed = True

        if changed:
            self._save()

    # ------------------------------------------------------------------ portfolio

    def positions(self) -> dict[str, Any]:
        self.process_pending()
        grouped: dict[int, dict[str, Any]] = {}
        for order in self.state["orders"]:
            if order["order_status"] != "COMPLETE":
                continue
            ref_id = int(order["ref_id"])
            row = grouped.setdefault(
                ref_id,
                {
                    "ref_id": ref_id,
                    "symbol": order["display_name"],
                    "exchange": order["exchange"],
                    "asset": "STOCK",
                    "product": order["order_delivery_type"],
                    "buy_quantity": 0,
                    "sell_quantity": 0,
                    "buy_value": 0,
                    "sell_value": 0,
                },
            )
            qty = int(order["filled_qty"])
            value = int(order["avg_filled_price"]) * qty
            if "BUY" in str(order["order_side"]).upper():
                row["buy_quantity"] += qty
                row["buy_value"] += value
            else:
                row["sell_quantity"] += qty
                row["sell_value"] += value

        positions = []
        total_pnl = 0
        for row in grouped.values():
            instrument = self.simulator.store.instrument_for_ref_id(row["ref_id"])
            ltp = to_paise(self.simulator.current_tick(instrument.symbol)["lp"]) or 0
            net_qty = row["buy_quantity"] - row["sell_quantity"]
            avg_buy = int(row["buy_value"] / row["buy_quantity"]) if row["buy_quantity"] else 0
            avg_sell = int(row["sell_value"] / row["sell_quantity"]) if row["sell_quantity"] else 0
            realized = row["sell_value"] - (avg_buy * row["sell_quantity"])
            unrealized = net_qty * (ltp - avg_buy) if net_qty else 0
            pnl = realized + unrealized
            total_pnl += pnl
            positions.append(
                {
                    **row,
                    "net_quantity": net_qty,
                    "quantity": net_qty,
                    "last_traded_price": ltp,
                    "avg_buy_price": avg_buy,
                    "avg_sell_price": avg_sell,
                    "avg_price": avg_buy or avg_sell,
                    "pnl": pnl,
                    "pnl_chg": 0.0,
                }
            )

        return {
            "message": "success",
            "portfolio": {
                "client_code": "MOCK_CLIENT",
                "position_stats": {"total_pnl": total_pnl, "total_pnl_chg": 0.0},
                "positions": positions,
            },
        }

    def holdings(self) -> dict[str, Any]:
        self.process_pending()
        net: dict[int, dict[str, Any]] = {}
        for order in self.state["orders"]:
            if order["order_status"] != "COMPLETE":
                continue
            if "CNC" not in str(order.get("order_delivery_type", "CNC")).upper():
                continue
            ref_id = int(order["ref_id"])
            row = net.setdefault(
                ref_id,
                {
                    "ref_id": ref_id,
                    "symbol": order["display_name"],
                    "exchange": order["exchange"],
                    "buy_quantity": 0,
                    "sell_quantity": 0,
                    "buy_value": 0,
                },
            )
            qty = int(order["filled_qty"])
            if "BUY" in str(order["order_side"]).upper():
                row["buy_quantity"] += qty
                row["buy_value"] += int(order["avg_filled_price"]) * qty
            else:
                row["sell_quantity"] += qty

        rows = []
        for row in net.values():
            net_qty = row["buy_quantity"] - row["sell_quantity"]
            if net_qty <= 0:
                continue
            avg_price = int(row["buy_value"] / row["buy_quantity"]) if row["buy_quantity"] else 0
            instrument = self.simulator.store.instrument_for_ref_id(row["ref_id"])
            ltp = to_paise(self.simulator.current_tick(instrument.symbol)["lp"]) or 0
            rows.append(
                {
                    "ref_id": row["ref_id"],
                    "symbol": row["symbol"],
                    "exchange": row["exchange"],
                    "quantity": net_qty,
                    "avg_price": avg_price,
                    "last_traded_price": ltp,
                    "current_value": ltp * net_qty,
                    "invested_value": avg_price * net_qty,
                    "pnl": (ltp - avg_price) * net_qty,
                }
            )

        return {"message": "success", "holdings": rows, "count": len(rows)}

    def funds(self) -> dict[str, Any]:
        self.process_pending()
        cash = int(self.state["opening_balance"])
        traded = 0
        blocked = 0
        for order in self.state["orders"]:
            side = str(order["order_side"]).upper()
            qty = int(order["order_qty"])
            price = int(order["avg_filled_price"] or order["order_price"] or 0)
            if order["order_status"] == "COMPLETE":
                traded += (-price * qty) if "BUY" in side else (price * qty)
            elif order["order_status"] == "PENDING" and "BUY" in side:
                blocked += int(order["order_price"]) * qty

        available = cash + traded - blocked
        return {
            "message": "success",
            "port_funds_and_margin": {
                "client_code": "MOCK_CLIENT",
                "start_of_day_funds": cash,
                "net_trading_amount": traded,
                "net_withdrawal_amount": 0,
                "total_collateral": 0,
                "net_margin_available": available,
                "total_margin_blocked": blocked,
                "derivative_margin_blocked": 0,
                "brokerage": 0,
            },
        }

    def get_margin(self, request: dict[str, Any]) -> dict[str, Any]:
        orders = request.get("order_req", {}).get("orders", [])
        total = sum(int(order.get("order_qty", 0)) * int(order.get("order_price", 0)) for order in orders)
        return {
            "total_margin": total,
            "span": 0,
            "exposure": 0,
            "delivery_margin": total,
            "opt_prem": 0,
            "var": 0,
            "margin_benefit": 0,
            "leg_margin": [],
            "message": "success",
        }

    # ------------------------------------------------------------------ helpers

    def _fill(self, order: dict[str, Any], price: int, now: datetime) -> None:
        order["filled_qty"] = order["order_qty"]
        order["avg_filled_price"] = int(price)
        order["order_status"] = "COMPLETE"
        order["last_modified_time"] = epoch_ms(now)

    def _limit_crossed(self, order: dict[str, Any], ltp: int) -> bool:
        side = str(order["order_side"]).upper()
        limit = int(order["order_price"])
        return ltp <= limit if "BUY" in side else ltp >= limit

    def _stoploss_triggered(self, order: dict[str, Any], ltp: int) -> bool:
        algo = order.get("algo_params") or {}
        trigger = int(algo.get("trigger_price", order["order_price"]))
        side = str(order["order_side"]).upper()
        # SL-BUY: protect a short — trigger when price rises above trigger
        # SL-SELL: protect a long  — trigger when price falls below trigger
        return ltp >= trigger if "BUY" in side else ltp <= trigger

    def _instrument_from_request(self, request: dict[str, Any]) -> Instrument:
        if "ref_id" in request:
            return self.simulator.store.instrument_for_ref_id(int(request["ref_id"]))
        return self.simulator.store.instrument_for_symbol(str(request["symbol"]))

    def _side(self, request: dict[str, Any]) -> str:
        side = str(request.get("side", "BUY")).upper()
        return "ORDER_SIDE_SELL" if side == "SELL" else "ORDER_SIDE_BUY"

    def _ref_data(self, instrument: Instrument) -> dict[str, Any]:
        return {
            "ref_id": instrument.ref_id,
            "asset": instrument.asset,
            "symbol": instrument.symbol,
            "nubra_name": instrument.nubra_name,
            "exchange": instrument.exchange,
            "tick_size": instrument.tick_size,
            "lot_size": instrument.lot_size,
        }
