from datetime import timedelta

from AlgorithmImports import *


class RuleDrivenExecution(QCAlgorithm):
    def initialize(self, rule_string: str = "4,0,2122,18003,326,336,8") -> None:
        self.set_start_date(2023, 1, 1)
        self.set_end_date(2023, 12, 31)
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
            # --- ADDED: Correctly parse and store the trade duration ---
            self._trade_duration = timedelta(hours=int(parts[5]))
            self._order_expiry_hours = timedelta(hours=int(parts[6]))
        except Exception as e:
            raise ValueError(f"Failed to parse rule_string. Error: {e}")

        self._pending_orders: dict[int, float] = {}
        # --- MODIFIED: This will now also store the entry timestamp ---
        # entry_order_id -> { 'tp_id': id, 'sl_id': id, 'entry_timestamp': timestamp }
        self._bracket_orders: dict[int, dict] = {}

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

        if self.portfolio.invested or len(self.transactions.get_open_orders()) > 0:
            self.debug("Skipping rule execution: Already have open positions or pending orders.")
            return

        price = self.securities[self._xauusd].price
        quantity = 10
        self.debug(f"--- EXECUTING RULE on {self.utc_time.strftime('%Y-%m-%d %H:%M')} (UTC) ---")

        is_buy_stop = self._entry_offset_ticks > 0
        entry_price_offset = abs(self._entry_offset_ticks) / 100.0

        if is_buy_stop:
            entry_price = price + entry_price_offset
            ticket = self.stop_market_order(self._xauusd, quantity, entry_price, tag="Entry Order")
        else:
            entry_price = price - entry_price_offset
            ticket = self.limit_order(self._xauusd, quantity, entry_price, tag="Entry Order")

        if ticket.status != OrderStatus.INVALID:
            expiry_time = self.utc_time + self._order_expiry_hours
            self._pending_orders[ticket.order_id] = expiry_time.timestamp()
            self.debug(f"Entry order {ticket.order_id} submitted. Expires if not filled by {expiry_time.strftime('%Y-%m-%d %H:%M')}.")

    def on_order_event(self, order_event: OrderEvent) -> None:
        order_id = order_event.order_id
        ticket = self.transactions.get_order_ticket(order_id)
        if not ticket:
            return

        if order_event.status == OrderStatus.FILLED and ticket.tag == "Entry Order":
            fill_price = order_event.fill_price
            quantity = abs(ticket.quantity)
            direction = 1 if ticket.quantity > 0 else -1

            tp_price = fill_price + direction * (self._take_profit_ticks / 100.0)
            sl_price = fill_price - direction * (self._stop_loss_ticks / 100.0)

            tp_ticket = self.limit_order(self._xauusd, -direction * quantity, tp_price, tag="TakeProfit")
            sl_ticket = self.stop_market_order(self._xauusd, -direction * quantity, sl_price, tag="StopLoss")

            # --- MODIFIED: Store the entry timestamp along with the SL/TP order IDs ---
            self._bracket_orders[order_id] = {
                'tp_id': tp_ticket.order_id,
                'sl_id': sl_ticket.order_id,
                'entry_timestamp': order_event.utc_time.timestamp()
            }
            self.debug(f"ENTRY EXECUTED: OrderID {order_id} filled at ${fill_price:.2f}. TP: {tp_price:.2f}, SL: {sl_price:.2f}")

        # If a TP/SL order fills, clean up the corresponding entry from our tracker
        entry_id_to_remove = -1
        for entry_id, ids in self._bracket_orders.items():
            if order_id in [ids['tp_id'], ids['sl_id']]:
                if order_event.status == OrderStatus.FILLED:
                    # Cancel the other OCO order
                    other_id = ids['sl_id'] if order_id == ids['tp_id'] else ids['tp_id']
                    cticket = self.transactions.get_order_ticket(other_id)
                    if cticket and cticket.status in [OrderStatus.NEW, OrderStatus.SUBMITTED, OrderStatus.NONE]:
                        cticket.cancel("Opposite bracket leg filled")

                    self.debug(f"EXIT EXECUTED: OrderID {order_id} ({ticket.tag}) filled at ${order_event.fill_price:.2f}.")
                    entry_id_to_remove = entry_id
                break

        if entry_id_to_remove != -1:
            del self._bracket_orders[entry_id_to_remove]

        # Clean up expired pending entry orders
        if order_id in self._pending_orders and ticket.tag == "Entry Order":
            if order_event.status in [OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.INVALID]:
                del self._pending_orders[order_id]
                self.debug(f"Entry Order {order_id} is no longer pending (Status: {order_event.status}).")

    def on_data(self, slice: Slice) -> None:
        # 1. Cancel expired pending entry orders
        for order_id, expiry_timestamp in list(self._pending_orders.items()):
            if self.utc_time.timestamp() >= expiry_timestamp:
                ticket = self.transactions.get_order_ticket(order_id)
                if ticket and ticket.status in [OrderStatus.NEW, OrderStatus.SUBMITTED, OrderStatus.NONE]:
                    ticket.cancel("Order expired before fill")
                    self.log(f"CANCELED: Entry Order {order_id} expired without being filled.")
                del self._pending_orders[order_id]

        # --- NEW: Check for time-based exits for open positions ---
        for entry_id, details in list(self._bracket_orders.items()):
            entry_timestamp = details['entry_timestamp']

            # Calculate when the trade should be closed due to time
            time_exit_timestamp = entry_timestamp + self._trade_duration.total_seconds()

            if self.utc_time.timestamp() >= time_exit_timestamp:
                self.debug(f"EXIT TRIGGERED (TIME LIMIT): Trade from Entry Order {entry_id} has expired.")

                # IMPORTANT: Cancel the outstanding SL and TP orders before liquidating
                tp_id = details['tp_id']
                sl_id = details['sl_id']

                tp_ticket = self.transactions.get_order_ticket(tp_id)
                if tp_ticket and tp_ticket.status in [OrderStatus.NEW, OrderStatus.SUBMITTED, OrderStatus.NONE]:
                    tp_ticket.cancel("Time limit exit")

                sl_ticket = self.transactions.get_order_ticket(sl_id)
                if sl_ticket and sl_ticket.status in [OrderStatus.NEW, OrderStatus.SUBMITTED, OrderStatus.NONE]:
                    sl_ticket.cancel("Time limit exit")

                # Liquidate the position
                self.liquidate(self._xauusd)
                self.debug("Position liquidated due to time limit.")

                # Remove the entry from our tracker
                del self._bracket_orders[entry_id]

    def _convert_int_to_day_of_week(self, day_of_week: int) -> DayOfWeek:
        mapping = {1: DayOfWeek.MONDAY, 2: DayOfWeek.TUESDAY, 3: DayOfWeek.WEDNESDAY,
                   4: DayOfWeek.THURSDAY, 5: DayOfWeek.FRIDAY, 6: DayOfWeek.SATURDAY, 7: DayOfWeek.SUNDAY}
        day = mapping.get(day_of_week)
        if day is None:
            raise ValueError(f"Invalid day_of_week '{day_of_week}'. Please use a value from 1 (Monday) to 7 (Sunday).")
        return day