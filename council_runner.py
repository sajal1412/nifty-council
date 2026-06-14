"""
council_runner.py — main orchestration script for the Nifty Council pipeline.

Flow:
  1. Login to Kite
  2. Pull all market data
  3. Format Section 0 extraction report
  4. Send to Claude API with compressed constitution
  5. Save full report
  6. Deliver via Telegram (summary alert + full report attachment)
"""

import sys
import datetime

from data_puller       import login_kite, pull_all_data, format_extraction_report
from claude_runner     import run_council_session, save_full_report
from telegram_delivery import send_report, send_error


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

    # Partial data is handled inside pull_all_data — if we get here, we have
    # at least enough to continue. Missing fields are noted in the report.

    # ── Step 3: Format extraction report ──────────────────────────────────────
    print("\nStep 3: Formatting Section 0 extraction report...")
    try:
        extraction_report = format_extraction_report(raw_data)
        print("  Report formatted.")
        print("\n--- EXTRACTION REPORT PREVIEW ---")
        print(extraction_report[:1500])
        print("--- END PREVIEW ---\n")
    except Exception as e:
        msg = f"COUNCIL ERROR: Report formatting failed {today}. Detail: {e}"
        print(f"ERROR: {msg}")
        send_error(msg)
        sys.exit(1)

    # ── Step 4: Claude API call ────────────────────────────────────────────────
    print("Step 4: Running Council analysis via Claude API...")
    try:
        full_report, telegram_summary = run_council_session(extraction_report)
        print("  Council session complete.")
        print(f"  Output length: {len(full_report)} characters.")
    except Exception as e:
        msg = f"COUNCIL ERROR: Analysis failed {today}. Claude API error. Manual session required. Detail: {e}"
        print(f"ERROR: {msg}")
        send_error(msg)
        sys.exit(1)

    # ── Step 5: Save full report ───────────────────────────────────────────────
    print("\nStep 5: Saving full report...")
    try:
        report_filepath = save_full_report(full_report)
    except Exception as e:
        print(f"Warning: could not save report file: {e}")
        # Non-fatal — continue to Telegram delivery
        import tempfile, os
        report_filepath = os.path.join(tempfile.gettempdir(), f"Council_{today}.txt")
        with open(report_filepath, "w", encoding="utf-8") as f:
            f.write(full_report)

    # ── Step 6: Telegram delivery ──────────────────────────────────────────────
    print("\nStep 6: Sending Telegram notification...")
    send_report(full_report, report_filepath)

    print(f"\n{'='*60}")
    print("Pipeline complete.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
