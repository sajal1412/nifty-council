"""
telegram_delivery.py — sends Council summary alert and full report to Telegram.
"""

import os
import re
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


# ── Summary parsing ────────────────────────────────────────────────────────────

def _parse_field(summary: str, field: str) -> str:
    """Extract a single field value from the TELEGRAM SUMMARY block."""
    pattern = rf"^{re.escape(field)}\s*[:\-]\s*(.+)$"
    match   = re.search(pattern, summary, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else "—"


def _bias_emoji(bias: str, trade: str) -> str:
    bias_lower  = bias.lower()
    trade_lower = trade.lower()
    if "no trade" in trade_lower:
        return "🟡"
    if "bullish" in bias_lower:
        return "🟢"
    if "bearish" in bias_lower:
        return "🔴"
    return "🟡"


def _dte_emoji(dte_field: str) -> str:
    if "near" in dte_field.lower():
        return "⚠️"
    return ""


# ── Message 1: formatted summary alert ────────────────────────────────────────

def _build_alert_message(summary: str, date_str: str) -> str:
    bias       = _parse_field(summary, "BIAS")
    consensus  = _parse_field(summary, "CONSENSUS")
    regime     = _parse_field(summary, "REGIME")
    vix        = _parse_field(summary, "VIX")
    dte        = _parse_field(summary, "DTE")
    trade      = _parse_field(summary, "TRADE")
    structure  = _parse_field(summary, "STRUCTURE")
    entry      = _parse_field(summary, "ENTRY CONDITION")
    size       = _parse_field(summary, "SIZE")
    support    = _parse_field(summary, "SUPPORT")
    resistance = _parse_field(summary, "RESISTANCE")
    invalid    = _parse_field(summary, "INVALIDATION")
    dissent    = _parse_field(summary, "DISSENT")

    main_emoji = _bias_emoji(bias, trade)
    dte_warn   = _dte_emoji(dte)

    # Telegram MarkdownV2 requires escaping certain characters
    # We use regular Markdown (parse_mode=Markdown) which is simpler
    is_no_trade = "no trade" in trade.lower()
    trade_line  = (
        f"🚫 *NO TRADE*" if is_no_trade
        else f"📋 *TRADE:* {trade}\n📐 *STRUCTURE:* {structure}\n⏰ *ENTRY:* {entry}\n💼 *SIZE:* {size}"
    )

    dissent_line = ""
    if dissent and dissent != "—" and dissent.lower() != "none":
        dissent_line = f"\n⚠️ *DISSENT:* {dissent}"

    msg = (
        f"{main_emoji} *NIFTY COUNCIL — {date_str}*\n"
        f"{'=' * 30}\n\n"
        f"📊 *BIAS:* {bias}\n"
        f"🤝 *CONSENSUS:* {consensus}\n"
        f"🌐 *REGIME:* {regime}\n\n"
        f"📈 *VIX:* {vix}\n"
        f"📅 *DTE:* {dte} {dte_warn}\n\n"
        f"{trade_line}\n\n"
        f"🛡️ *SUPPORT:* {support}\n"
        f"🧱 *RESISTANCE:* {resistance}\n"
        f"❌ *INVALIDATION:* {invalid}"
        f"{dissent_line}\n\n"
        f"_Full report attached below._"
    )

    # Telegram message limit is 4096 chars — truncate if needed
    if len(msg) > 4000:
        msg = msg[:3950] + "\n...[truncated]"

    return msg


# ── Message 2: full report as document ────────────────────────────────────────

def send_report(full_report: str, report_filepath: str) -> None:
    """
    Send Message 1 (formatted alert) and Message 2 (full .txt report).
    Falls back to writing to log on failure — does not raise.
    """
    date_str = datetime.date.today().strftime("%d-%b-%Y")
    chat     = _chat_id()

    # Parse summary from full report
    pattern = r"(?:={10,}\s*\n)?TELEGRAM SUMMARY\s*(?:\n={10,})?\s*\n([\s\S]+?)(?:={10,}\s*$|$)"
    match   = re.search(pattern, full_report, re.IGNORECASE)
    summary = match.group(1).strip() if match else full_report

    try:
        alert_msg = _build_alert_message(summary, date_str)
        _post("sendMessage", data={
            "chat_id":    chat,
            "text":       alert_msg,
            "parse_mode": "Markdown",
        })
        print("Telegram alert sent.")
    except Exception as e:
        _log_fallback(f"Failed to send Telegram alert: {e}\n\nFull report:\n{full_report}")
        return

    # Message 2 — attach full report as document
    try:
        date_file = datetime.date.today().strftime("%Y-%m-%d")
        with open(report_filepath, "rb") as f:
            _post("sendDocument", data={"chat_id": chat}, files={
                "document": (f"Council_{date_file}.txt", f, "text/plain"),
            })
        print("Telegram report document sent.")
    except Exception as e:
        _log_fallback(f"Failed to send Telegram document: {e}")


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
    """Write to fallback log when Telegram is unavailable."""
    log_path = os.path.join(os.path.dirname(__file__), "council_fallback.log")
    timestamp = datetime.datetime.now().isoformat()
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n{timestamp}\n{content}\n")
        print(f"Fallback log written to {log_path}")
    except Exception as e:
        print(f"CRITICAL: Could not write fallback log either: {e}")
        print(content)
