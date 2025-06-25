import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

# Add the parent directory to the path so we can import the main module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock the AlgorithmImports module
class MockDayOfWeek:
    MONDAY = "Monday"
    TUESDAY = "Tuesday"
    WEDNESDAY = "Wednesday"
    THURSDAY = "Thursday"
    FRIDAY = "Friday"
    SATURDAY = "Saturday"
    SUNDAY = "Sunday"

class MockOrderStatus:
    FILLED = "Filled"

class MockResolution:
    MINUTE = "Minute"

# Create mock for the main module
sys.modules['AlgorithmImports'] = MagicMock()
sys.modules['AlgorithmImports'].DayOfWeek = MockDayOfWeek
sys.modules['AlgorithmImports'].OrderStatus = MockOrderStatus
sys.modules['AlgorithmImports'].Resolution = MockResolution
sys.modules['AlgorithmImports'].QCAlgorithm = MagicMock
sys.modules['AlgorithmImports'].Slice = MagicMock
sys.modules['AlgorithmImports'].OrderEvent = MagicMock

# Now import the main module
from main import WeeklyTradeExecution

class TestWeeklyTradeExecution(unittest.TestCase):
    """
    Improved test suite for the WeeklyTradeExecution algorithm.
    It uses test parameterization to reduce code duplication and constants for better readability.
    """

    # Define constants for test data to improve readability and maintainability
    TEST_SYMBOL = "XAUUSD"
    INITIAL_PRICE = 1000.0
    TICK_SIZE = 0.1
    ORDER_TICKS = 5
    TRADE_QUANTITY = 10
    TRADE_DURATION_HOURS = 336

    def setUp(self):
        """Set up the test environment before each test."""
        self.algorithm = WeeklyTradeExecution()

        # Mock the API methods and properties provided by the QCAlgorithm base class
        self.algorithm.set_start_date = MagicMock()
        self.algorithm.set_end_date = MagicMock()
        self.algorithm.set_cash = MagicMock()
        self.algorithm.add_cfd = MagicMock()
        self.algorithm.schedule = MagicMock()
        self.algorithm.date_rules = MagicMock()
        self.algorithm.time_rules = MagicMock()
        self.algorithm.portfolio = MagicMock()
        self.algorithm.securities = MagicMock()
        self.algorithm.stop_market_order = MagicMock()
        self.algorithm.limit_order = MagicMock()
        self.algorithm.liquidate = MagicMock()

        # Mock the return value for add_cfd to provide a mock symbol
        self.mock_symbol = MagicMock()
        self.algorithm.add_cfd.return_value.symbol = self.mock_symbol

        # Initialize the algorithm with default parameters.
        self.algorithm.initialize()

    def test_initialize(self):
        """Test the initialize method sets up the algorithm correctly."""
        self.algorithm.set_start_date.assert_called_once_with(2023, 1, 1)
        self.algorithm.set_end_date.assert_called_once_with(2023, 12, 31)
        self.algorithm.set_cash.assert_called_once_with(100000)
        self.algorithm.add_cfd.assert_called_once_with(self.TEST_SYMBOL, MockResolution.MINUTE)
        self.algorithm.schedule.on.assert_called_once()

        self.assertEqual(self.algorithm._order_ticks, 5)
        self.assertTrue(self.algorithm._order_above)
        self.assertEqual(self.algorithm._day_of_week, 3)
        self.assertEqual(self.algorithm._hour_of_day, 10)
        self.assertEqual(self.algorithm._trade_duration, timedelta(hours=self.TRADE_DURATION_HOURS))
        self.assertIsNone(self.algorithm._open_trade_time)

    def test_convert_int_to_day_of_week(self):
        """Test day of week conversion for all valid and default cases."""
        test_cases = {
            1: MockDayOfWeek.MONDAY,
            2: MockDayOfWeek.TUESDAY,
            3: MockDayOfWeek.WEDNESDAY,
            4: MockDayOfWeek.THURSDAY,
            5: MockDayOfWeek.FRIDAY,
            6: MockDayOfWeek.SATURDAY,
            7: MockDayOfWeek.SUNDAY,
            8: MockDayOfWeek.WEDNESDAY,  # Default case
            0: MockDayOfWeek.WEDNESDAY,  # Default case
        }
        for day_int, expected_day in test_cases.items():
            with self.subTest(day=day_int):
                actual_day = self.algorithm._convert_int_to_day_of_week(day_int)
                self.assertEqual(actual_day, expected_day)

    def test_weekly_trade_order_placement(self):
        """Test weekly_trade places the correct order type based on the _order_above flag."""
        # Common setup for both scenarios
        mock_security = MagicMock()
        mock_security.price = self.INITIAL_PRICE
        mock_security.symbol_properties.minimum_price_variation = self.TICK_SIZE
        self.algorithm.securities.__getitem__.return_value = mock_security
        current_time = datetime(2023, 1, 4, 10, 0)
        self.algorithm.time = current_time

        scenarios = [
            {'order_above': True, 'expected_price_offset': 1},
            {'order_above': False, 'expected_price_offset': -1},
        ]

        for scenario in scenarios:
            with self.subTest(order_above=scenario['order_above']):
                # Reset mocks and apply scenario-specific setup
                self.algorithm.stop_market_order.reset_mock()
                self.algorithm.limit_order.reset_mock()
                self.algorithm.portfolio.invested = False
                self.algorithm._order_above = scenario['order_above']
                self.algorithm._order_ticks = self.ORDER_TICKS

                # Execute the method under test
                self.algorithm.weekly_trade()

                # Verify the correct order method was called with the correct price
                expected_price = self.INITIAL_PRICE + (scenario['expected_price_offset'] * self.ORDER_TICKS * self.TICK_SIZE)
                if scenario['order_above']:
                    self.algorithm.stop_market_order.assert_called_once_with(self.mock_symbol, self.TRADE_QUANTITY, expected_price)
                    self.algorithm.limit_order.assert_not_called()
                else:
                    self.algorithm.limit_order.assert_called_once_with(self.mock_symbol, self.TRADE_QUANTITY, expected_price)
                    self.algorithm.stop_market_order.assert_not_called()

                self.assertEqual(self.algorithm._open_trade_time, current_time)

    def test_on_data_liquidation_logic(self):
        """Test on_data liquidates a position only after the trade duration has passed."""
        # Setup the initial state where a trade is open
        self.algorithm.portfolio.invested = True
        open_time = datetime(2023, 1, 4, 10, 0)
        self.algorithm._open_trade_time = open_time
        self.algorithm._trade_duration = timedelta(hours=self.TRADE_DURATION_HOURS)

        # Case 1: Time is not yet up, so liquidation should NOT occur
        self.algorithm.time = open_time + timedelta(hours=self.TRADE_DURATION_HOURS - 1)
        self.algorithm.on_data(MagicMock())
        self.algorithm.liquidate.assert_not_called()
        self.assertIsNotNone(self.algorithm._open_trade_time)

        # Case 2: Time is up, so liquidation SHOULD occur
        self.algorithm.time = open_time + timedelta(hours=self.TRADE_DURATION_HOURS)
        self.algorithm.on_data(MagicMock())
        self.algorithm.liquidate.assert_called_once_with(self.mock_symbol)
        self.assertIsNone(self.algorithm._open_trade_time)

    def test_on_order_event(self):
        """Test the on_order_event method for coverage."""
        mock_order_event = MagicMock()
        mock_order_event.status = MockOrderStatus.FILLED

        # This test confirms the method runs without error.
        self.algorithm.on_order_event(mock_order_event)

if __name__ == '__main__':
    unittest.main()