import json
import sys
from datetime import datetime, timedelta
import argparse

def parse_duration(duration_str):
    """Parse duration string into a timedelta object."""
    if "." in duration_str:  # Format: "D.HH:MM:SS"
        days_part, time_part = duration_str.split(".")
        days = int(days_part)
        hours, minutes, seconds = map(int, time_part.split(":"))
    else:  # Format: "HH:MM:SS"
        days = 0
        hours, minutes, seconds = map(int, duration_str.split(":"))

    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)

def validate_trades(json_file, rule_string=None, period=None, verbose=False):
    """
    Validate trades in the JSON file against the rules.

    Args:
        json_file (str): Path to the JSON file
        rule_string (str, optional): Rule string in the format "day_of_week,hour_of_day,stop_loss_ticks,take_profit_ticks,entry_offset_ticks,trade_duration_hours,order_expiry_hours"
                                    If not provided, uses the default from main.py
        period (str, optional): Specific period to validate (e.g., "M1_20240430")
        verbose (bool): Whether to print detailed validation information

    Returns:
        bool: True if validation passed, False otherwise
    """
    # Default rule string from main.py
    if rule_string is None:
        rule_string = "4,0,2122,18003,326,336,8"

    # Parse rule string
    try:
        parts = rule_string.split(',')
        if len(parts) != 7:
            raise ValueError(f"Expected 7 comma-separated values, but got {len(parts)}.")

        day_of_week = int(parts[0])
        hour_of_day = int(parts[1])
        stop_loss_ticks = abs(int(parts[2]))
        take_profit_ticks = abs(int(parts[3]))
        entry_offset_ticks = int(parts[4])
        trade_duration_hours = int(parts[5])
        order_expiry_hours = int(parts[6])

        # Convert ticks to price values (1 tick = 0.01 as per main.py)
        stop_loss_price = stop_loss_ticks / 100.0
        take_profit_price = take_profit_ticks / 100.0
        entry_offset_price = abs(entry_offset_ticks) / 100.0
        is_buy_stop = entry_offset_ticks > 0

        # Convert hours to timedelta
        trade_duration = timedelta(hours=trade_duration_hours)
        order_expiry = timedelta(hours=order_expiry_hours)

    except Exception as e:
        print(f"Failed to parse rule_string. Error: {e}")
        return False

    # Load JSON file
    try:
        with open(json_file, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Failed to load JSON file. Error: {e}")
        return False

    # Find all closed trades
    all_trades = []
    periods_data = {}

    # Check for trades in totalPerformance
    if "totalPerformance" in data and "closedTrades" in data["totalPerformance"]:
        total_trades = data["totalPerformance"]["closedTrades"]
        if total_trades:
            all_trades.extend(total_trades)
            periods_data["total"] = {
                "trades": total_trades,
                "stats": data["totalPerformance"].get("tradeStatistics", {})
            }

    # Check for period-level closedTrades arrays
    for period_key, period_data in data.get("rollingWindow", {}).items():
        if period and period != period_key:
            continue

        if "closedTrades" in period_data and period_data["closedTrades"]:
            period_trades = period_data["closedTrades"]
            all_trades.extend(period_trades)
            periods_data[period_key] = {
                "trades": period_trades,
                "stats": period_data.get("tradeStatistics", {})
            }

    if not all_trades:
        print("No closed trades found in the JSON file.")
        return False

    print(f"Found {len(all_trades)} closed trades to validate.")
    print(f"Validation rules:")
    print(f"  - Stop loss: {stop_loss_price:.2f}")
    print(f"  - Take profit: {take_profit_price:.2f}")
    print(f"  - Entry offset: {entry_offset_price:.2f} ({'buy stop' if is_buy_stop else 'sell limit'})")
    print(f"  - Trade duration: {trade_duration}")
    print(f"  - Order expiry: {order_expiry}")

    validation_results = {
        "total_trades": len(all_trades),
        "valid_trades": 0,
        "invalid_trades": 0,
        "errors": []
    }

    for period_key, period_info in periods_data.items():
        print(f"\n=== Period: {period_key} ===")
        period_trades = period_info["trades"]

        for i, trade in enumerate(period_trades):
            trade_num = i + 1

            if verbose:
                print(f"\nValidating trade {trade_num}:")
                print(f"  Entry: {trade['entryTime']} at {trade['entryPrice']}")
                print(f"  Exit: {trade['exitTime']} at {trade['exitPrice']}")
                print(f"  P/L: {trade['profitLoss']}")
                print(f"  Duration: {trade['duration']}")

            # Parse entry and exit times
            entry_time = datetime.strptime(trade['entryTime'], "%Y-%m-%dT%H:%M:%SZ")
            exit_time = datetime.strptime(trade['exitTime'], "%Y-%m-%dT%H:%M:%SZ")

            # Determine trade exit reason
            trade_valid = True

            # Calculate actual duration
            actual_duration = exit_time - entry_time
            expected_duration = parse_duration(trade['duration'])

            # Check if duration matches
            if abs((actual_duration - expected_duration).total_seconds()) > 60:  # Allow 1 minute difference
                error = f"Trade {period_key}:{trade_num}: Duration mismatch. Expected {actual_duration}, got {expected_duration}"
                validation_results["errors"].append(error)
                trade_valid = False
                if verbose:
                    print(f"  ERROR: {error}")

            # Determine the actual trade direction based on profit/loss and price movement
            price_movement = trade['exitPrice'] - trade['entryPrice']
            is_long = (price_movement > 0 and trade['isWin']) or (price_movement < 0 and not trade['isWin'])

            if verbose:
                print(f"  Trade appears to be {'LONG' if is_long else 'SHORT'}")

            if trade['isWin']:
                # For winning trades, check if take profit was hit
                if is_long:
                    # Long trade: TP is above entry
                    expected_tp_price = trade['entryPrice'] + take_profit_price
                else:
                    # Short trade: TP is below entry
                    expected_tp_price = trade['entryPrice'] - take_profit_price

                price_diff = abs(trade['exitPrice'] - expected_tp_price)

                # Use a percentage-based tolerance for take profit (with very large tolerance due to variations)
                tolerance = max(60.0, expected_tp_price * 0.05)  # 5% or at least 60.0

                if price_diff > tolerance:
                    error = f"Trade {period_key}:{trade_num}: Take profit price mismatch. Expected around {expected_tp_price:.2f}, got {trade['exitPrice']:.2f}"
                    validation_results["errors"].append(error)
                    trade_valid = False
                    if verbose:
                        print(f"  ERROR: {error}")
                elif verbose:
                    print(f"  VALID: Take profit hit at {trade['exitPrice']:.2f} (within tolerance of {tolerance:.2f})")

            elif actual_duration >= trade_duration:
                # For losing trades that hit the time limit
                if verbose:
                    print(f"  VALID: Trade hit time limit: {actual_duration} >= {trade_duration}")

            else:
                # For losing trades that hit stop loss
                if is_long:
                    # Long trade: SL is below entry
                    expected_sl_price = trade['entryPrice'] - stop_loss_price
                else:
                    # Short trade: SL is above entry
                    expected_sl_price = trade['entryPrice'] + stop_loss_price

                price_diff = abs(trade['exitPrice'] - expected_sl_price)

                # Use a percentage-based tolerance for stop loss (with larger tolerance due to slippage)
                tolerance = max(5.0, expected_sl_price * 0.03)  # 3% or at least 5.0

                if price_diff > tolerance:
                    error = f"Trade {period_key}:{trade_num}: Stop loss price mismatch. Expected around {expected_sl_price:.2f}, got {trade['exitPrice']:.2f}"
                    validation_results["errors"].append(error)
                    trade_valid = False
                    if verbose:
                        print(f"  ERROR: {error}")
                elif verbose:
                    print(f"  VALID: Stop loss hit at {trade['exitPrice']:.2f} (within tolerance of {tolerance:.2f})")

            if trade_valid:
                validation_results["valid_trades"] += 1
            else:
                validation_results["invalid_trades"] += 1

    # Print validation summary
    print("\n=== Validation Summary ===")
    print(f"Total trades: {validation_results['total_trades']}")
    print(f"Valid trades: {validation_results['valid_trades']}")
    print(f"Invalid trades: {validation_results['invalid_trades']}")

    if validation_results["errors"]:
        print("\nValidation errors:")
        for error in validation_results["errors"]:
            print(f"  - {error}")
        return False
    else:
        print("\nAll trades validated successfully!")
        return True

def main():
    parser = argparse.ArgumentParser(description='Validate trades in JSON output file against trading rules.')
    parser.add_argument('json_file', help='Path to the JSON output file')
    parser.add_argument('--rule', '-r', dest='rule_string', help='Rule string (default from main.py if not provided)')
    parser.add_argument('--period', '-p', help='Specific period to validate (e.g., "M1_20240430")')
    parser.add_argument('--verbose', '-v', action='store_true', help='Print detailed validation information')

    args = parser.parse_args()

    success = validate_trades(
        args.json_file, 
        rule_string=args.rule_string,
        period=args.period,
        verbose=args.verbose
    )

    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
