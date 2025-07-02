import json
from datetime import datetime, timedelta

# --- Configuration ---
# Define the trading rules based on the algorithm's rule_string:
# "4,0,2122,18003,326,336,8"
STOP_LOSS_TICKS = 2122
TAKE_PROFIT_TICKS = 18003
ENTRY_OFFSET_TICKS = 326
TRADE_DURATION_HOURS = 336
ORDER_EXPIRY_HOURS = 8

# Define a tolerance for floating point and time comparisons
PRICE_TOLERANCE = 0.01  # e.g., 1 cent
TIME_TOLERANCE_SECONDS = 60 # e.g., 1 minute for exit checks

def parse_qc_datetime(dt_string: str) -> datetime:
    """Parses QuantConnect's datetime string format."""
    # Handles both 'Z' and timezone offsets like '-04:00'
    if dt_string.endswith('Z'):
        return datetime.fromisoformat(dt_string.replace('Z', '+00:00'))
    return datetime.fromisoformat(dt_string)

def find_orders_for_trade(entry_order, all_orders):
    """
    Finds the associated SL, TP, and closing orders for a given entry order.
    This is inferred by looking at orders created shortly after the entry fill.
    """
    entry_fill_time = parse_qc_datetime(entry_order['lastFillTime'])
    sl_order, tp_order, closing_order = None, None, None

    for order_id, order in all_orders.items():
        if order['id'] == entry_order['id']:
            continue

        order_time = parse_qc_datetime(order['time'])

        # Check for orders created around the same time as the entry fill
        if abs((order_time - entry_fill_time).total_seconds()) < 5:
            tag = order.get('tag', '')
            if 'TakeProfit' in tag:
                tp_order = order
            elif 'StopLoss' in tag:
                sl_order = order

    return sl_order, tp_order


def get_exit_reason(trade, all_orders):
    """Determines the reason a trade was closed by finding the closing order."""
    exit_time = parse_qc_datetime(trade['exitTime'])

    for order_id, order in all_orders.items():
        if order['status'] == 'Filled' and order['lastFillTime']:
            fill_time = parse_qc_datetime(order['lastFillTime'])
            if abs((fill_time - exit_time).total_seconds()) < TIME_TOLERANCE_SECONDS:
                # Check if the order closes the position
                if float(order['quantity']) == -float(trade['quantity']):
                    return order.get('tag', 'Unknown')
    return 'Unknown'


def validate_backtest(file_path: str):
    """
    Main function to load, parse, and validate the backtest results.
    """
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error loading or parsing JSON file: {e}")
        return

    all_orders = data.get('orders', {})
    closed_trades = data.get('totalPerformance', {}).get('closedTrades', [])

    if not all_orders or not closed_trades:
        print("Could not find 'orders' or 'totalPerformance.closedTrades' in the JSON file.")
        return

    print("--- Backtest Validation Report ---")
    print(f"Validating results from: {file_path}")
    print("-" * 35)
    print(f"Stop Loss:         ${STOP_LOSS_TICKS / 100:.2f}")
    print(f"Take Profit:       ${TAKE_PROFIT_TICKS / 100:.2f}")
    print(f"Entry Offset:      ${ENTRY_OFFSET_TICKS / 100:.2f}")
    print(f"Trade Duration:    {TRADE_DURATION_HOURS} hours")
    print(f"Order Expiry:      {ORDER_EXPIRY_HOURS} hours")
    print("-" * 35, "\n")

    # --- 1. Validate Closed Trades ---
    print("--- Closed Trade Validation ---")
    trade_count = 0
    for trade in closed_trades:
        trade_count += 1
        print(f"\nValidating Trade #{trade_count} (Entry Time: {trade['entryTime']})")

        entry_price = float(trade['entryPrice'])
        exit_price = float(trade['exitPrice'])
        direction = 1 if float(trade['quantity']) > 0 else -1

        entry_order = None
        for order_id, order in all_orders.items():
            if order.get('status') == 'Filled' and 'Entry Order' in order.get('tag', ''):
                if order.get('lastFillTime'):
                    fill_time = parse_qc_datetime(order['lastFillTime'])
                    trade_entry_time = parse_qc_datetime(trade['entryTime'])
                    if abs((fill_time - trade_entry_time).total_seconds()) < 2:
                        entry_order = order
                        break

        if not entry_order:
            print("  [FAIL] Could not find matching entry order for this trade.")
            continue

        sl_order, tp_order = find_orders_for_trade(entry_order, all_orders)
        if not sl_order or not tp_order:
            print("  [FAIL] Could not find associated StopLoss or TakeProfit orders.")
            continue

        expected_sl_price = entry_price - direction * (STOP_LOSS_TICKS / 100.0)
        actual_sl_price = float(sl_order['stopPrice'])
        if abs(expected_sl_price - actual_sl_price) < PRICE_TOLERANCE:
            print(f"  [PASS] Stop-Loss price correctly set to ~${actual_sl_price:.2f}")
        else:
            print(f"  [FAIL] Stop-Loss: Expected ~${expected_sl_price:.2f}, but was set to ${actual_sl_price:.2f}")

        expected_tp_price = entry_price + direction * (TAKE_PROFIT_TICKS / 100.0)
        actual_tp_price = float(tp_order['limitPrice'])
        if abs(expected_tp_price - actual_tp_price) < PRICE_TOLERANCE:
            print(f"  [PASS] Take-Profit price correctly set to ~${actual_tp_price:.2f}")
        else:
            print(f"  [FAIL] Take-Profit: Expected ~${expected_tp_price:.2f}, but was set to ${actual_tp_price:.2f}")

        exit_reason = get_exit_reason(trade, all_orders)
        entry_time = parse_qc_datetime(trade['entryTime'])
        exit_time = parse_qc_datetime(trade['exitTime'])
        actual_duration_hours = (exit_time - entry_time).total_seconds() / 3600.0

        if "StopLoss" in exit_reason:
            print(f"  [INFO] Exited via StopLoss.")
        elif "TakeProfit" in exit_reason:
            print(f"  [INFO] Exited via TakeProfit.")
        elif "Time Limit Exit" in exit_reason:
            if abs(actual_duration_hours - TRADE_DURATION_HOURS) < 1.0:
                print(f"  [PASS] Exited via Time Limit after ~{actual_duration_hours:.2f} hours.")
            else:
                print(f"  [FAIL] Exited via Time Limit, but duration was {actual_duration_hours:.2f}h, expected {TRADE_DURATION_HOURS}h.")
        else:
            print(f"  [INFO] Exited due to an un-tagged reason: '{exit_reason}'")

    # --- 2. Validate Expired Orders ---
    print("\n--- Expired Order Validation ---")
    expired_count = 0
    other_canceled_count = 0
    for order_id, order in all_orders.items():
        if 'Entry Order' in order.get('tag', '') and order.get('status') == 'Canceled':
            created_time = parse_qc_datetime(order['time'])
            cancelled_time = parse_qc_datetime(order['lastUpdateTime'])
            duration_hours = (cancelled_time - created_time).total_seconds() / 3600.0

            if abs(duration_hours - ORDER_EXPIRY_HOURS) < 0.1:  # 6-minute tolerance
                print(f"  [PASS] Entry Order {order_id} correctly expired after ~{duration_hours:.2f} hours.")
                expired_count += 1
            else:
                other_canceled_count +=1

    if expired_count == 0:
        print("  [INFO] No orders found that were canceled due to expiry.")
    if other_canceled_count > 0:
        print(f"  [INFO] Found {other_canceled_count} canceled orders not related to expiry.")


    print("\n--- Validation Complete ---")


if __name__ == '__main__':
    # IMPORTANT: Replace this with the actual path to your JSON file from the 2023 backtest.
    json_file_path = 'Swimming Red Zebra.json'
    validate_backtest(json_file_path)