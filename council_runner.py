"""
council_runner.py — main orchestration script for the Nifty Council pipeline.

Flow:
  1. Login to Kite
  2. Pull all market data
  3. Format Section 0 extraction report
  4. Send to Telegram (ready to copy-paste into Claude.ai)
"""

import sys
import datetime

from data_puller       import login_kite, pull_all_data, format_extraction_report
from telegram_delivery import send_extraction_report, send_error


def main() -> None:
    today = datetime.date.today().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"Nifty Council Pipeline — {today}")
    print(f"{'='*60}\n")

    # ── Step 1: Kite login ─────────────────────────────────────────────────────
    print("Step 1: Kite login...")
    try:
        kite = login_kite()
        print("  Login successful.")
    except Exception as e:
        msg = f"COUNCIL ERROR: Kite login failed {today}. Manual session required today. Detail: {e}"
        print(f"ERROR: {msg}")
        send_error(msg)
        sys.exit(1)

    # ── Step 2: Pull all market data ───────────────────────────────────────────
    print("\nStep 2: Pulling market data...")
    try:
        raw_data = pull_all_data(kite)
        print("  Data pull complete.")
    except Exception as e:
        msg = f"COUNCIL ERROR: Data pull failed {today}. No market data available. Detail: {e}"
        print(f"ERROR: {msg}")
        send_error(msg)
        sys.exit(1)

    # ── Step 3: Format extraction report ──────────────────────────────────────
    print("\nStep 3: Formatting Section 0 extraction report...")
    try:
        extraction_report = format_extraction_report(raw_data)
        print("  Report formatted.")
    except Exception as e:
        msg = f"COUNCIL ERROR: Report formatting failed {today}. Detail: {e}"
        print(f"ERROR: {msg}")
        send_error(msg)
        sys.exit(1)

    # ── Step 4: Send to Telegram ───────────────────────────────────────────────
    print("\nStep 4: Sending to Telegram...")
    send_extraction_report(extraction_report, raw_data)

    print(f"\n{'='*60}")
    print("Pipeline complete.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
