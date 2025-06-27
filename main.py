from AlgorithmImports import *
from datetime import datetime, timedelta, timezone


class WeeklyMultiTradeExecution(QCAlgorithm):
    def initialize(self,
                   day_of_week: int = 3,
                   hour_of_day: int = 10,
                   trade_duration_hours: int = 336,
                   order_offset_ticks: int = 5,
                   order_expiry_hours: int = 24) -> None:
        """
        Adds two new parameters:
        :param order_offset_ticks: How many ticks above/below the current price to place the stop/limit order.
        :param order_expiry_hours: How many hours a pending order can wait to be filled before it's canceled.
        """
        self.set_start_date(2023, 1, 1)
        self.set_end_date(2023, 12, 31)
        self.set_cash(100000)

        day_of_week_enum = self._convert_int_to_day_of_week(day_of_week)
        self._xauusd = self.add_cfd("XAUUSD", Resolution.MINUTE).symbol

        # --- Parameters for the weekly trade ---
        self._order_offset_ticks: int = order_offset_ticks # NEWLY PARAMETERIZED
        self._order_expiry_hours = timedelta(hours=order_expiry_hours) # NEW PARAMETER
        self._order_above: bool = True
        self._trade_quantity: int = 10
        self._trade_duration = timedelta(hours=trade_duration_hours)

        # --- Data structures to track trades ---
        # For filled trades (positions)
        self.open_trade_details = {}
        # NEW: For pending orders that are not yet filled
        self.pending_orders = {}

        # Schedule the weekly trade entry
        self.schedule.on(
            self.date_rules.every(day_of_week_enum),
            self.time_rules.at(hour_of_day, 0, time_zone=TimeZones.UTC),
            self.weekly_trade_entry
        )

    def weekly_trade_entry(self) -> None:
        price = self.securities[self._xauusd].price
        tick_size = self.securities[self._xauusd].symbol_properties.minimum_price_variation

        self.debug(f"--- ATTEMPTING ENTRY on {self.utc_time.strftime('%Y-%m-%d %H:%M')} (UTC) ---")

        # --- MODIFIED: Use the new parameter for the order price ---
        if self._order_above:
            stop_price = price + (self._order_offset_ticks * tick_size)
            ticket = self.stop_market_order(self._xauusd, self._trade_quantity, stop_price)
        else:
            limit_price = price - (self._order_offset_ticks * tick_size)
            ticket = self.limit_order(self._xauusd, self._trade_quantity, limit_price)

        # --- NEW: Track the pending order for expiration ---
        if ticket.status != OrderStatus.INVALID:
            expiry_time = self.utc_time + self._order_expiry_hours
            self.pending_orders[ticket.order_id] = expiry_time.timestamp()
            self.debug(f"Order {ticket.order_id} submitted. It will expire if not filled by {expiry_time.strftime('%Y-%m-%d %H:%M')}.")

    def on_order_event(self, order_event: OrderEvent) -> None:
        # No longer need to check for filled status here first, as we handle all statuses
        order_id = order_event.order_id

        # If the order is no longer pending, remove it from our tracking dictionary
        if order_id in self.pending_orders:
            if order_event.status == OrderStatus.FILLED or \
                    order_event.status == OrderStatus.CANCELED or \
                    order_event.status == OrderStatus.INVALID:
                del self.pending_orders[order_id]
                self.debug(f"Order {order_id} is no longer pending (Status: {order_event.status}). Removed from expiry tracking.")

        # Handle logging for filled entry and exit orders
        if order_event.status == OrderStatus.FILLED:
            ticket = self.transactions.get_order_ticket(order_id)
            if not ticket: return

            if ticket.quantity > 0:
                close_time_object = order_event.utc_time + self._trade_duration
                close_timestamp = close_time_object.timestamp()
                self.open_trade_details[order_id] = close_timestamp
                self.debug(f"ENTRY EXECUTED: OrderID {order_id} filled at ${order_event.fill_price:.2f}. "
                           f"Scheduled exit at {datetime.fromtimestamp(close_timestamp, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}.")
            elif ticket.quantity < 0:
                self.debug(f"EXIT EXECUTED: Closing OrderID {order_id} filled at ${order_event.fill_price:.2f}.")
                self.debug("------------------------------------------------------")

    def on_data(self, slice: Slice) -> None:
        # --- NEW: Loop to check for and cancel expired pending orders ---
        for order_id, expiry_timestamp in list(self.pending_orders.items()):
            if self.utc_time.timestamp() >= expiry_timestamp:
                ticket = self.transactions.get_order_ticket(order_id)
                if ticket:
                    ticket.cancel() # Cancel the order
                    self.log(f"CANCELED: Order {order_id} expired without being filled.")

        # --- Existing loop to manage open positions ---
        for order_id, close_timestamp in list(self.open_trade_details.items()):
            if self.time.timestamp() >= close_timestamp:
                self.debug(f"EXIT TRIGGERED: Time limit reached for original OrderID {order_id}. Submitting closing order.")
                ticket = self.transactions.get_order_ticket(order_id)
                if ticket:
                    self.market_order(self._xauusd, -ticket.quantity_filled)
                if order_id in self.open_trade_details:
                    del self.open_trade_details[order_id]

    def _convert_int_to_day_of_week(self, day_of_week: int) -> DayOfWeek:
        mapping = {1: DayOfWeek.MONDAY, 2: DayOfWeek.TUESDAY, 3: DayOfWeek.WEDNESDAY,
                   4: DayOfWeek.THURSDAY, 5: DayOfWeek.FRIDAY, 6: DayOfWeek.SATURDAY, 7: DayOfWeek.SUNDAY}
        return mapping.get(day_of_week, DayOfWeek.WEDNESDAY)