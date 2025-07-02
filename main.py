from AlgorithmImports import *
from datetime import datetime, timedelta, timezone
import uuid

class RuleDrivenExecution(QCAlgorithm):
    def initialize(self, rule_string: str = "4,0,2122,18003,326,336,8") -> None:
        self.set_start_date(2024, 3, 29)
        self.set_end_date(2024, 6, 29)
        self.set_cash(100000)
        self._xauusd = self.add_cfd("XAUUSD", Resolution.MINUTE).symbol

        try:
            parts = rule_string.split(',')
            if len(parts) != 7:
                raise ValueError(f"Expected 7 comma-separated values, but got {len(parts)}.")
            day_of_week = int(parts[0])
            hour_of_day = int(parts[1])
            self._stop_loss_ticks = abs(int(parts[2]))
            self._take_profit_ticks = abs(int(parts[3]))
            self._entry_offset_ticks = int(parts[4])
            self._trade_duration = timedelta(hours=int(parts[5]))
            self._order_expiry_hours = timedelta(hours=int(parts[6]))
        except Exception as e:
            raise ValueError(f"Failed to parse rule_string. Error: {e}")

        # --- State Management Dictionaries ---
        # entry_order_id -> expiry_timestamp
        self._pending_orders: Dict[int, float] = {}
        # entry_order_id -> { tp_id, sl_id, entry_timestamp, quantity, position_id }
        self._bracket_orders: Dict[int, Dict[str, Any]] = {}
        # order_id -> position_id (for sl, tp, and time-exit market orders)
        self._order_to_position_id: Dict[int, str] = {}

        day_of_week_enum = self._convert_int_to_day_of_week(day_of_week)
        self.schedule.on(
            self.date_rules.every(day_of_week_enum),
            self.time_rules.at(hour_of_day, 0, time_zone=TimeZones.UTC),
            self.execute_rule
        )

    def execute_rule(self) -> None:
        if self._stop_loss_ticks <= 0 or self._take_profit_ticks <= 0:
            self.error("Stop-loss and take-profit ticks must be positive values.")
            return

        price = self.securities[self._xauusd].price
        quantity = 10
        self.debug(f"--- EXECUTING RULE on {self.utc_time.strftime('%Y-%m-%d %H:%M')} (UTC) ---")

        is_buy_stop = self._entry_offset_ticks > 0
        entry_price_offset = abs(self._entry_offset_ticks) / 100.0

        # Generate a unique position-group ID for this logical trade cycle
        position_id = str(uuid.uuid4())

        if is_buy_stop:
            entry_price = price + entry_price_offset
            tag = f"Entry Order | pos_id={position_id}"
            ticket = self.stop_market_order(self._xauusd, quantity, entry_price, tag=tag)
        else:
            entry_price = price - entry_price_offset
            tag = f"Entry Order | pos_id={position_id}"
            ticket = self.limit_order(self._xauusd, quantity, entry_price, tag=tag)

        if ticket.status != OrderStatus.INVALID:
            expiry_time = self.utc_time + self._order_expiry_hours
            self._pending_orders[ticket.order_id] = expiry_time.timestamp()
            # Track the position ID for this entry order
            self._order_to_position_id[ticket.order_id] = position_id
            self.debug(f"Entry order {ticket.order_id} submitted. Expires if not filled by {expiry_time.strftime('%Y-%m-%d %H:%M')}. pos_id={position_id}")

    def on_order_event(self, order_event: OrderEvent) -> None:
        order_id = order_event.order_id
        ticket = self.transactions.get_order_ticket(order_id)
        if not ticket:
            return

        # Check for attached position id in tag or lookup
        position_id = None
        if ticket.tag and "pos_id=" in ticket.tag:
            position_id = ticket.tag.split("pos_id=")[-1].strip()
        elif order_id in self._order_to_position_id:
            position_id = self._order_to_position_id[order_id]

        # If a new Entry Order is filled, create the SL/TP bracket
        if order_event.status == OrderStatus.FILLED and ticket.tag.startswith("Entry Order"):
            fill_price = order_event.fill_price
            quantity = ticket.quantity
            direction = 1 if quantity > 0 else -1

            tp_price = fill_price + direction * (self._take_profit_ticks / 100.0)
            sl_price = fill_price - direction * (self._stop_loss_ticks / 100.0)

            tp_tag = f"TakeProfit | pos_id={position_id}"
            sl_tag = f"StopLoss | pos_id={position_id}"
            tp_ticket = self.limit_order(self._xauusd, -quantity, tp_price, tag=tp_tag)
            sl_ticket = self.stop_market_order(self._xauusd, -quantity, sl_price, tag=sl_tag)
            # Track the position ID for these orders
            self._order_to_position_id[tp_ticket.order_id] = position_id
            self._order_to_position_id[sl_ticket.order_id] = position_id

            # Store all info needed to manage this independent trade
            self._bracket_orders[order_id] = {
                'tp_id': tp_ticket.order_id,
                'sl_id': sl_ticket.order_id,
                'entry_timestamp': order_event.utc_time.timestamp(),
                'quantity': quantity,
                'position_id': position_id
            }
            self.debug(f"ENTRY EXECUTED: OrderID {order_id} filled at ${fill_price:.2f}. TP: {tp_price:.2f}, SL: {sl_price:.2f}, pos_id={position_id}")

        # If a TP/SL order for any trade fills, find its parent entry and clean up
        entry_id_to_remove = None
        for entry_id, ids in list(self._bracket_orders.items()):
            if order_id in [ids['tp_id'], ids['sl_id']]:
                if order_event.status == OrderStatus.FILLED:
                    other_id = ids['sl_id'] if order_id == ids['tp_id'] else ids['tp_id']
                    self.transactions.cancel_order(other_id, f"Opposite bracket leg filled | pos_id={ids['position_id']}")
                    self.debug(f"EXIT EXECUTED: OrderID {order_id} ({ticket.tag}) filled at ${order_event.fill_price:.2f} | pos_id={ids['position_id']}")
                    entry_id_to_remove = entry_id
                break

        if entry_id_to_remove is not None:
            # Clean up the mapping for the child orders
            ids = self._bracket_orders[entry_id_to_remove]
            for oid in [entry_id_to_remove, ids['tp_id'], ids['sl_id']]:
                if oid in self._order_to_position_id:
                    del self._order_to_position_id[oid]
            del self._bracket_orders[entry_id_to_remove]

        # Clean up the pending order list if the entry order is resolved
        if order_id in self._pending_orders:
            if order_event.status in [OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.INVALID]:
                del self._pending_orders[order_id]
                if order_id in self._order_to_position_id and order_id not in self._bracket_orders:
                    del self._order_to_position_id[order_id]

    def on_data(self, slice: Slice) -> None:
        # 1. Cancel expired pending entry orders
        for order_id, expiry_timestamp in list(self._pending_orders.items()):
            if self.utc_time.timestamp() >= expiry_timestamp:
                position_id = self._order_to_position_id.get(order_id, "N/A")
                self.transactions.cancel_order(order_id, f"Order expired before fill | pos_id={position_id}")

        # 2. Check for time-based exits for all open positions
        for entry_id, details in list(self._bracket_orders.items()):
            entry_timestamp = details['entry_timestamp']
            time_exit_timestamp = entry_timestamp + self._trade_duration.total_seconds()
            if self.utc_time.timestamp() >= time_exit_timestamp:
                position_id = details['position_id']
                self.debug(f"EXIT TRIGGERED (TIME LIMIT): Trade from Entry Order {entry_id} has expired. pos_id={position_id}")

                # Cancel the outstanding SL and TP orders
                self.transactions.cancel_order(details['tp_id'], f"Time limit exit | pos_id={position_id}")
                self.transactions.cancel_order(details['sl_id'], f"Time limit exit | pos_id={position_id}")

                # Liquidate only the specific trade's quantity, and mark with the same position id
                quantity_to_close = details['quantity']
                tag = f"Time Limit Exit | pos_id={position_id}"
                close_ticket = self.market_order(self._xauusd, -quantity_to_close, tag=tag)
                self._order_to_position_id[close_ticket.order_id] = position_id

                self.debug(f"Closing {-quantity_to_close} units for timed-out trade {entry_id}. pos_id={position_id}")

                # Remove the entry from our tracker
                for oid in [entry_id, details['tp_id'], details['sl_id']]:
                    if oid in self._order_to_position_id:
                        del self._order_to_position_id[oid]
                del self._bracket_orders[entry_id]

    def _convert_int_to_day_of_week(self, day_of_week: int) -> DayOfWeek:
        mapping = {
            1: DayOfWeek.MONDAY,
            2: DayOfWeek.TUESDAY,
            3: DayOfWeek.WEDNESDAY,
            4: DayOfWeek.THURSDAY,
            5: DayOfWeek.FRIDAY,
            6: DayOfWeek.SATURDAY,
            7: DayOfWeek.SUNDAY
        }
        day = mapping.get(day_of_week)
        if day is None:
            raise ValueError(f"Invalid day_of_week '{day_of_week}'. Please use a value from 1 (Monday) to 7 (Sunday).")
        return day
