from AlgorithmImports import *
from datetime import datetime, timedelta, timezone


class WeeklyMultiTradeExecution(QCAlgorithm):
    def initialize(self, day_of_week: int = 3, hour_of_day: int = 10, trade_duration_hours: int = 336) -> None:
        self.set_start_date(2023, 1, 1)
        self.set_end_date(2023, 12, 31)
        self.set_cash(100000)

        day_of_week_enum = self._convert_int_to_day_of_week(day_of_week)
        self._xauusd = self.add_cfd("XAUUSD", Resolution.MINUTE).symbol

        # --- Parameters for the weekly trade ---
        self._order_ticks: int = 5
        self._order_above: bool = True
        self._trade_quantity: int = 10
        self._trade_duration = timedelta(hours=trade_duration_hours)

        # --- Data structure to track each trade individually ---
        self.open_trade_details = {}

        # --- MODIFIED: Schedule the weekly trade entry in UTC ---
        self.schedule.on(
            self.date_rules.every(day_of_week_enum),
            # Add the time_zone=TimeZones.Utc parameter to ensure the schedule is in UTC.
            self.time_rules.at(hour_of_day, 0, time_zone=TimeZones.UTC),
            self.weekly_trade_entry
        )

    def weekly_trade_entry(self) -> None:
        price = self.securities[self._xauusd].price
        tick_size = self.securities[self._xauusd].symbol_properties.minimum_price_variation

        # --- ADDED LOGGING ---
        # 1. Log the attempt to place a new trade each week.
        self.debug(f"--- ATTEMPTING ENTRY on {self.utc_time.strftime('%Y-%m-%d %H:%M')} (UTC) ---")

        if self._order_above:
            stop_price = price + (self._order_ticks * tick_size)
            self.stop_market_order(self._xauusd, self._trade_quantity, stop_price)
        else:
            limit_price = price - (self._order_ticks * tick_size)
            self.limit_order(self._xauusd, self._trade_quantity, limit_price)

    def on_order_event(self, order_event: OrderEvent) -> None:
        if order_event.status == OrderStatus.FILLED:
            ticket = self.transactions.get_order_ticket(order_event.order_id)
            if not ticket:
                return

            # --- MODIFIED LOGGING ---
            # 2. Log when an ENTRY order is filled.
            if ticket.quantity > 0:
                close_time_object = order_event.utc_time + self._trade_duration
                close_timestamp = close_time_object.timestamp()
                self.open_trade_details[order_event.order_id] = close_timestamp

                self.debug(f"ENTRY EXECUTED: OrderID {order_event.order_id} filled at ${order_event.fill_price:.2f}. "
                           f"Scheduled exit at {datetime.fromtimestamp(close_timestamp, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}.")

            # 4. Log when an EXIT order is filled.
            elif ticket.quantity < 0:
                self.debug(f"EXIT EXECUTED: Closing OrderID {order_event.order_id} filled at ${order_event.fill_price:.2f}.")
                self.debug("------------------------------------------------------") # Separator for clarity

    def on_data(self, slice: Slice) -> None:
        for order_id, close_timestamp in list(self.open_trade_details.items()):
            if self.time.timestamp() >= close_timestamp:

                # --- ADDED LOGGING ---
                # 3. Log the decision to close a trade.
                self.debug(f"EXIT TRIGGERED: Time limit reached for original OrderID {order_id}. Submitting closing order.")

                ticket = self.transactions.get_order_ticket(order_id)
                if ticket:
                    # Place a market order for the opposite quantity to close the position
                    self.market_order(self._xauusd, -ticket.quantity_filled)

                if order_id in self.open_trade_details:
                    del self.open_trade_details[order_id]

    def _convert_int_to_day_of_week(self, day_of_week: int) -> DayOfWeek:
        mapping = {1: DayOfWeek.MONDAY, 2: DayOfWeek.TUESDAY, 3: DayOfWeek.WEDNESDAY,
                   4: DayOfWeek.THURSDAY, 5: DayOfWeek.FRIDAY, 6: DayOfWeek.SATURDAY, 7: DayOfWeek.SUNDAY}
        return mapping.get(day_of_week, DayOfWeek.WEDNESDAY)