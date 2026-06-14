"""
telegram_delivery.py — sends Council data report to Telegram for copy-paste into Claude.ai
"""

import os
import datetime
import requests


def _bot_token() -> str:
    return os.environ["TELEGRAM_BOT_TOKEN"]


def _chat_id() -> str:
    return os.environ["TELEGRAM_CHAT_ID"]


def _post(method: str, **kwargs) -> requests.Response:
    url  = f"https://api.telegram.org/bot{_bot_token()}/{method}"
    resp = requests.post(url, timeout=30, **kwargs)
    resp.raise_for_status()
    return resp


# ── DTE styling ────────────────────────────────────────────────────────────────

def _dte_emoji(dte: int) -> str:
    if dte <= 3:
        return "⚠️ NEAR EXPIRY"
    elif dte <= 10:
        return "🟡 Mid Cycle"
    else:
        return "🟢 Far Expiry"


# ── Message 1: quick alert card ────────────────────────────────────────────────

def _build_alert_card(raw_data: dict, time_str: str) -> str:
    dte       = raw_data.get("dte", "?")
    dte_class = raw_data.get("dte_class", "")
    expiry    = raw_data.get("expiry", "?")
    vix       = raw_data.get("vix", "N/A")
    vix_trend = raw_data.get("vix_trend", "")
    pcr       = raw_data.get("pcr_aggregate", "N/A")
    daily_rsi = raw_data.get("daily_rsi", "N/A")
    breadth   = raw_data.get("breadth", "N/A")
    fut_class = raw_data.get("futures_classification", "N/A")
    oi_class  = raw_data.get("oi_classification", "N/A")
    date_str  = raw_data.get("date", datetime.date.today().strftime("%d-%b-%Y"))

    dte_label = _dte_emoji(int(dte) if str(dte).isdigit() else 99)

    # Dual chain line — shown when DTE <= 2 and next expiry data was pulled
    dual_line = ""
    next_pcr = raw_data.get("next_pcr_aggregate")
    if next_pcr is not None:
        next_exp = raw_data.get("next_expiry", "")
        next_exp_str = (
            next_exp.strftime("%d-%b-%Y") if hasattr(next_exp, "strftime") else str(next_exp)
        )
        dual_line = f"🔗 *DUAL CHAIN:* Next {next_exp_str} — PCR {next_pcr}\n"

    msg = (
        f"📊 *NIFTY COUNCIL DATA — {date_str} ({time_str} IST)*\n"
        f"{'─' * 32}\n\n"
        f"📅 *Expiry:* {expiry} | DTE: {dte} — {dte_label}\n"
        f"📈 *VIX:* {vix} — {vix_trend}\n"
        f"⚖️ *PCR (curr):* {pcr}\n"
        f"{dual_line}"
        f"📉 *Daily RSI:* {daily_rsi}\n"
        f"🧭 *Breadth:* {breadth}\n"
        f"📦 *Futures OI:* {fut_class}\n"
        f"🔄 *OI Change:* {oi_class}\n\n"
        f"_Full extraction report follows — copy and paste into Claude.ai_\n"
        f"_Trigger phrase: *Hello Council — Standard Mode*_"
    )
    return msg


# ── Message 2: full extraction report as copyable text file ───────────────────

def send_extraction_report(extraction_report: str, raw_data: dict) -> None:
    """
    Send two Telegram messages:
    1. Quick alert card with key numbers at a glance
    2. Full Section 0 extraction report as a .txt file (easy to copy on mobile)
    """
    chat = _chat_id()

    # Determine session time label based on current UTC hour
    utc_hour = datetime.datetime.utcnow().hour
    if utc_hour < 4:
        time_label = "8:30 AM"
    elif utc_hour < 7:
        time_label = "10:00 AM"
    else:
        time_label = "1:30 PM"

    # Message 1 — quick alert card
    try:
        alert = _build_alert_card(raw_data, time_label)
        _post("sendMessage", data={
            "chat_id":    chat,
            "text":       alert,
            "parse_mode": "Markdown",
        })
        print("Telegram alert card sent.")
    except Exception as e:
        _log_fallback(f"Failed to send alert card: {e}")

    # Message 2 — full extraction report as .txt attachment (easiest to copy on mobile)
    try:
        date_str  = datetime.date.today().strftime("%Y-%m-%d")
        filename  = f"CouncilData_{date_str}_{time_label.replace(':', '').replace(' ', '')}.txt"

        # Append the trigger instruction at the top so it's ready to paste
        report_with_header = (
            f"Hello Council — Standard Mode\n\n"
            f"{extraction_report}\n\n"
            f"---\n"
            f"INSTRUCTION TO COUNCIL:\n"
            f"Section 0 Data Extraction Report above is complete.\n"
            f"Please run Standard Mode beginning from Section 1 (Input Validation)\n"
            f"through all sections up to and including Section 13 (Journal Entry).\n"
            f"End your response with the TELEGRAM SUMMARY section in the exact format\n"
            f"specified in the Constitution."
        )

        _post("sendDocument",
            data={"chat_id": chat},
            files={"document": (filename, report_with_header.encode("utf-8"), "text/plain")},
        )
        print("Telegram extraction report sent.")
    except Exception as e:
        # Fallback: send as plain text message if document fails
        try:
            chunks = [extraction_report[i:i+4000] for i in range(0, len(extraction_report), 4000)]
            for chunk in chunks:
                _post("sendMessage", data={"chat_id": chat, "text": chunk})
            print("Telegram extraction report sent as text (document fallback).")
        except Exception as e2:
            _log_fallback(f"Failed to send extraction report: {e} / {e2}\n\n{extraction_report}")


def send_error(message: str) -> None:
    """Send a plain error notification to Telegram. Fallback to log if Telegram fails."""
    try:
        _post("sendMessage", data={
            "chat_id": _chat_id(),
            "text":    f"🔴 {message}",
        })
        print(f"Telegram error notification sent: {message}")
    except Exception as e:
        _log_fallback(f"Could not send Telegram error (original: {message}): {e}")


# ── Log fallback ───────────────────────────────────────────────────────────────

def _log_fallback(content: str) -> None:
    log_path  = os.path.join(os.path.dirname(__file__), "council_fallback.log")
    timestamp = datetime.datetime.now().isoformat()
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n{timestamp}\n{content}\n")
        print(f"Fallback log written to {log_path}")
    except Exception as e:
        print(f"CRITICAL: Could not write fallback log: {e}")
        print(content)
