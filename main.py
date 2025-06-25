from AlgorithmImports import *
from datetime import timedelta

class WeeklyTradeExecution(QCAlgorithm):
    def initialize(self, day_of_week: int = 3, hour_of_day: int = 10, trade_duration_hours: int = 336) -> None:
        self.set_start_date(2023, 1, 1)
        self.set_end_date(2023, 12, 31)
        self.set_cash(100000)

        day_of_week_enum = self._convert_int_to_day_of_week(day_of_week)
        self._xauusd = self.add_cfd("XAUUSD", Resolution.MINUTE).symbol
        self._order_ticks: int = 5
        self._order_above: bool = True
        self._day_of_week = day_of_week
        self._hour_of_day = hour_of_day
        self._trade_duration = timedelta(hours=trade_duration_hours)
        self._open_trade_time = None

        self.schedule.on(
            self.date_rules.every(day_of_week_enum),
            self.time_rules.at(hour_of_day, 0),
            self.weekly_trade
        )

    def weekly_trade(self) -> None:
        if not self.portfolio.invested:
            price = self.securities[self._xauusd].price
            tick_size = self.securities[self._xauusd].symbol_properties.minimum_price_variation

            if self._order_above:
                stop_price = price + (self._order_ticks * tick_size)
                self.stop_market_order(self._xauusd, 10, stop_price)
            else:
                limit_price = price - (self._order_ticks * tick_size)
                self.limit_order(self._xauusd, 10, limit_price)

            self._open_trade_time = self.time

    def on_data(self, slice: Slice) -> None:
        if self.portfolio.invested and self._open_trade_time is not None:
            if self.time >= self._open_trade_time + self._trade_duration:
                self.liquidate(self._xauusd)
                self._open_trade_time = None

    def on_order_event(self, order_event: OrderEvent) -> None:
        if order_event.status == OrderStatus.FILLED:
            pass

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
        return mapping.get(day_of_week, DayOfWeek.WEDNESDAY)