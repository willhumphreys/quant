# Quantitative Trading Algorithm

![Tests](https://github.com/username/quant/actions/workflows/tests.yml/badge.svg)

This repository contains a quantitative trading algorithm that executes trades on a weekly schedule.

## Algorithm Overview

The `WeeklyTradeExecution` class implements a trading algorithm that:

1. Trades XAUUSD (Gold) on a specific day of the week at a specific hour
2. Places either a stop market order above the current price or a limit order below the current price
3. Closes positions after a specified duration

## Testing

The repository includes a comprehensive test suite for the trading algorithm.

### Test Structure

- `tests/test_weekly_trade_execution.py`: Unit tests for the `WeeklyTradeExecution` class
  - Tests for the `_convert_int_to_day_of_week` method
  - Tests for the `initialize` method
  - Tests for the `weekly_trade` method (both with order above and below current price)
  - Tests for the `on_data` method (both with and without liquidation)
  - Tests for the `on_order_event` method

### Running Tests

To run the tests, use the provided test runner script:

```bash
python run_tests.py
```

This will discover and run all tests in the `tests` directory.

### Testing Approach

The tests use Python's `unittest` framework and the `unittest.mock` module to mock the QuantConnect framework dependencies. This allows us to test the algorithm's logic without needing the actual QuantConnect environment.

Key mocking strategies:
- Mock classes for QuantConnect enums (DayOfWeek, OrderStatus, Resolution)
- Mock objects for QCAlgorithm methods and properties
- Mock objects for securities and order events

## Continuous Integration

This project uses GitHub Actions for continuous integration. The workflow automatically runs all tests on multiple Python versions (3.8, 3.9, 3.10) whenever code is pushed to the main branch or a pull request is created.

To see the status of the latest tests, check the badge at the top of this README or visit the Actions tab in the GitHub repository.

## Requirements

See `requirements.txt` for the list of dependencies.
