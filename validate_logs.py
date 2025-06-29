import re
from datetime import datetime, timedelta

def validate_qc_logs(log_file_path: str, rule_string: str) -> None:
    """
    Parses a QuantConnect log file and validates trade logic against a rule string.

    Args:
        log_file_path: The path to the downloaded log file.
        rule_string: The same rule string used in the QC algorithm.
    """
    # --- 1. Define Rules from the String ---
    try:
        parts = rule_string.split(',')
        if len(parts) != 7:
            raise ValueError(f"Expected 7 parts, got {len(parts)}")

        stop_loss_ticks = abs(int(parts[2]))
        take_profit_ticks = abs(int(parts[3]))
        entry_offset_ticks = int(parts[4])
        trade_duration_hours = int(parts[5])
        order_expiry_hours = int(parts[6])

        print("--- Validation Rules ---")
        print(f"Stop Loss: {stop_loss_ticks/100.0:.2f} dollars")
        print(f"Take Profit: {take_profit_ticks/100.0:.2f} dollars")
        print(f"Trade Duration: {trade_duration_hours} hours")
        print(f"Order Expiry: {order_expiry_hours} hours")
        print("-" * 26 + "\n")

    except Exception as e:
        print(f"Error parsing rule string: {e}")
        return

    # --- 2. Regular Expressions to Parse Log Lines ---
    # Note: QC logs use the local timezone of the backtest machine. We parse it and assume UTC.
    log_line_re = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(.*)")
    entry_re = re.compile(r"ENTRY EXECUTED: OrderID (\d+) filled at \$([\d\.]+)\. TP: ([\d\.]+), SL: ([\d\.]+)")
    exit_re = re.compile(r"EXIT EXECUTED: OrderID (\d+) \((StopLoss|TakeProfit)\) filled at \$([\d\.]+)")
    time_exit_re = re.compile(r"EXIT TRIGGERED \(TIME LIMIT\): Trade from Entry Order (\d+) has expired\.")
    cancel_re = re.compile(r"CANCELED: Entry Order (\d+) expired without being filled\.")
    submit_re = re.compile(r"Entry order (\d+) submitted. Expires if not filled by ([\d\- \:]+).")

    # --- 3. Data Structures to Reconstruct Trades ---
    trades = {} # {entry_order_id: {details}}
    pending_orders = {} # {order_id: {details}}


    # --- 4. Parse the Log File ---
    with open(log_file_path, 'r') as f:
        for line in f:
            match = log_line_re.match(line)
            if not match:
                continue

            log_time_str, message = match.groups()
            log_time = datetime.strptime(log_time_str, '%Y-%m-%d %H:%M:%S')

            # Capture order submission
            m = submit_re.search(message)
            if m:
                order_id, expiry_str = m.groups()
                pending_orders[int(order_id)] = {'submit_time': log_time}

            # Capture entry fill
            m = entry_re.search(message)
            if m:
                order_id, fill_price, tp_price, sl_price = m.groups()
                order_id = int(order_id)
                trades[order_id] = {
                    'entry_time': log_time,
                    'entry_price': float(fill_price),
                    'expected_tp': float(tp_price),
                    'expected_sl': float(sl_price),
                    'exit_time': None,
                    'exit_reason': None
                }

            # Capture SL/TP exit
            m = exit_re.search(message)
            if m:
                # Find which entry this exit belongs to
                for entry_id, trade in trades.items():
                    # This is a simplification; a real system would need a proper map
                    # For one-trade-at-a-time, this is sufficient.
                    if trade['exit_time'] is None:
                        trade['exit_time'] = log_time
                        trade['exit_reason'] = m.group(2)
                        break

            # Capture Time Limit exit
            m = time_exit_re.search(message)
            if m:
                entry_id = int(m.group(1))
                if entry_id in trades:
                    trades[entry_id]['exit_time'] = log_time
                    trades[entry_id]['exit_reason'] = 'TIME LIMIT'

            # Capture Order Cancellation
            m = cancel_re.search(message)
            if m:
                order_id = int(m.group(1))
                if order_id in pending_orders:
                    pending_orders[order_id]['cancel_time'] = log_time
                    pending_orders[order_id]['exit_reason'] = 'CANCELED'

    # --- 5. Validate and Print Report ---
    print("--- Validation Report ---")

    # Group trades by week
    trades_by_week = {}
    # Group pending orders by week
    pending_by_week = {}
    # Group time-expired trades by week
    expired_by_week = {}
    start_date = None
    end_date = None

    # Process executed trades
    for order_id, trade in trades.items():
        entry_time = trade['entry_time']

        # Track the earliest and latest dates
        if start_date is None or entry_time < start_date:
            start_date = entry_time
        if end_date is None or entry_time > end_date:
            end_date = entry_time

        # Get the week number (ISO week)
        year, week_num, _ = entry_time.isocalendar()
        week_key = f"{year}-W{week_num:02d}"

        if week_key not in trades_by_week:
            trades_by_week[week_key] = []

        trades_by_week[week_key].append(order_id)

        # If this trade expired due to time limit, add it to expired_by_week
        if trade.get('exit_reason') == 'TIME LIMIT':
            if week_key not in expired_by_week:
                expired_by_week[week_key] = []
            expired_by_week[week_key].append(order_id)

    # Process pending orders that were canceled
    for order_id, order in pending_orders.items():
        if order.get('exit_reason') == 'CANCELED':
            submit_time = order['submit_time']

            # Track the earliest and latest dates
            if start_date is None or submit_time < start_date:
                start_date = submit_time
            if end_date is None or submit_time > end_date:
                end_date = submit_time

            # Get the week number (ISO week)
            year, week_num, _ = submit_time.isocalendar()
            week_key = f"{year}-W{week_num:02d}"

            if week_key not in pending_by_week:
                pending_by_week[week_key] = []

            pending_by_week[week_key].append(order_id)

    # Process all submitted orders to identify those that were not filled
    for order_id, order in pending_orders.items():
        if order_id not in trades:  # Order was submitted but not filled
            submit_time = order['submit_time']

            # Track the earliest and latest dates
            if start_date is None or submit_time < start_date:
                start_date = submit_time
            if end_date is None or submit_time > end_date:
                end_date = submit_time

            # Get the week number (ISO week)
            year, week_num, _ = submit_time.isocalendar()
            week_key = f"{year}-W{week_num:02d}"

            if week_key not in pending_by_week:
                pending_by_week[week_key] = []

            # Only add if not already in the list
            if order_id not in pending_by_week[week_key]:
                pending_by_week[week_key].append(order_id)

    # Validate that a trade is placed every week
    if start_date and end_date:
        print("\n--- Weekly Trade Validation ---")

        # Generate all weeks in the date range
        current_date = start_date
        all_weeks = set()

        while current_date <= end_date:
            year, week_num, _ = current_date.isocalendar()
            week_key = f"{year}-W{week_num:02d}"
            all_weeks.add(week_key)
            current_date += timedelta(days=7)

        # Check if each week has at least one trade
        all_weeks_valid = True
        for week_key in sorted(all_weeks):
            if week_key in trades_by_week:
                trade_count = len(trades_by_week[week_key])
                # Check if any trades in this week expired due to time limit
                if week_key in expired_by_week:
                    expired_count = len(expired_by_week[week_key])
                    print(f"  [PASS] Week {week_key}: {trade_count} trade(s) placed, {expired_count} expired due to time limit")
                else:
                    print(f"  [PASS] Week {week_key}: {trade_count} trade(s) placed")
            elif week_key in pending_by_week:
                expired_count = len(pending_by_week[week_key])
                print(f"  [FAIL] Week {week_key}: {expired_count} trade(s) placed but expired without being filled")
                all_weeks_valid = False
            else:
                print(f"  [FAIL] Week {week_key}: No trades placed")
                all_weeks_valid = False

        if all_weeks_valid:
            print("\nWeekly Trade Validation: PASSED. A trade was placed every week.")
        else:
            print("\nWeekly Trade Validation: FAILED. Some weeks have no trades.")

    # Validate Filled Trades
    for order_id, trade in trades.items():
        print(f"\nValidating Trade from Entry Order ID: {order_id}")
        is_valid = True

        # Check SL/TP price calculation
        calc_tp = round(trade['entry_price'] + (take_profit_ticks / 100.0), 2)
        calc_sl = round(trade['entry_price'] - (stop_loss_ticks / 100.0), 2)

        if abs(calc_tp - trade['expected_tp']) > 0.01:
            print(f"  [FAIL] Take-Profit Price: Expected ~{calc_tp}, Logged {trade['expected_tp']}")
            is_valid = False
        else:
            print(f"  [PASS] Take-Profit Price: ~{calc_tp}")

        if abs(calc_sl - trade['expected_sl']) > 0.01:
            print(f"  [FAIL] Stop-Loss Price: Expected ~{calc_sl}, Logged {trade['expected_sl']}")
            is_valid = False
        else:
            print(f"  [PASS] Stop-Loss Price: ~{calc_sl}")

        # Check trade duration on time-based exits
        if trade['exit_reason'] == 'TIME LIMIT':
            duration = trade['exit_time'] - trade['entry_time']
            expected_duration = timedelta(hours=trade_duration_hours)
            # Allow a small tolerance (e.g., 1 minute)
            if abs(duration - expected_duration) > timedelta(minutes=1):
                print(f"  [FAIL] Trade Duration: Expected {expected_duration}, Actual {duration}")
                is_valid = False
            else:
                print(f"  [PASS] Trade Duration: {duration}")

        if is_valid:
            print(f"Result: PASSED. Exited via {trade['exit_reason']}.")

    # Validate Canceled Orders
    for order_id, order in pending_orders.items():
        if order.get('exit_reason') == 'CANCELED':
            print(f"\nValidating Canceled Order ID: {order_id}")
            duration = order['cancel_time'] - order['submit_time']
            expected_duration = timedelta(hours=order_expiry_hours)
            # Allow a small tolerance
            if abs(duration - expected_duration) > timedelta(minutes=1):
                print(f"  [FAIL] Order Expiry: Expected {expected_duration}, Actual {duration}")
            else:
                print(f"  [PASS] Order Expiry: {duration}")

if __name__ == '__main__':
    # --- CONFIGURATION ---
    LOG_FILE = 'Energetic Fluorescent Orange Butterfly_logs.txt'  # <-- IMPORTANT: SET THIS PATH
    RULE_STRING = "4,0,2122,18003,326,336,8"

    validate_qc_logs(LOG_FILE, RULE_STRING)
