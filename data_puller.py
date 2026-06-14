"""
data_puller.py — Kite API data extraction for Nifty Council pipeline.
Pulls all required market data and formats it as a Section 0 extraction report.
"""

import os
import json
import time
import datetime
import requests
import pandas as pd
import numpy as np
import pyotp
from kiteconnect import KiteConnect

OI_CACHE_FILE = os.path.join(os.path.dirname(__file__), "oi_cache.json")

# ── Instrument tokens ──────────────────────────────────────────────────────────
NIFTY_TOKEN = 256265
VIX_TOKEN   = 264969
NIFTY_SYMBOL = "NIFTY 50"

# ── Login ──────────────────────────────────────────────────────────────────────

def _totp_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def login_kite() -> KiteConnect:
    """
    Auto-login to Kite using TOTP.
    Returns an authenticated KiteConnect instance.
    Raises RuntimeError on failure so the caller can send an error notification.
    """
    api_key    = os.environ["KITE_API_KEY"]
    api_secret = os.environ["KITE_API_SECRET"]
    user_id    = os.environ["KITE_USER_ID"]
    password   = os.environ["KITE_PASSWORD"]
    totp_secret = os.environ["KITE_TOTP_SECRET"]

    kite = KiteConnect(api_key=api_key)

    session = requests.Session()

    # Step 1 — initiate login
    resp = session.post(
        "https://kite.zerodha.com/api/login",
        data={"user_id": user_id, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Kite login step 1 failed: {data}")

    request_id = data["data"]["request_id"]

    # Step 2 — submit TOTP
    totp = _totp_code(totp_secret)
    resp2 = session.post(
        "https://kite.zerodha.com/api/twofa",
        data={"user_id": user_id, "request_id": request_id, "twofa_value": totp},
        timeout=15,
    )
    resp2.raise_for_status()
    data2 = resp2.json()
    if data2.get("status") != "success":
        raise RuntimeError(f"Kite TOTP step failed: {data2}")

    # Step 3 — follow Kite Connect login URL to get request_token
    login_url = kite.login_url()

    # Follow redirects and check each URL for request_token or sess_id
    resp3 = session.get(login_url, allow_redirects=True, timeout=15)
    final_url = resp3.url

    request_token = None

    # Check final URL for request_token
    if "request_token=" in final_url:
        request_token = final_url.split("request_token=")[1].split("&")[0]
    # Kite sometimes returns sess_id instead of request_token
    elif "sess_id=" in final_url:
        request_token = final_url.split("sess_id=")[1].split("&")[0]
    else:
        # Walk the redirect history
        for r in resp3.history:
            loc = r.headers.get("Location", "")
            if "request_token=" in loc:
                request_token = loc.split("request_token=")[1].split("&")[0]
                break
            if "sess_id=" in loc:
                request_token = loc.split("sess_id=")[1].split("&")[0]
                break

    if not request_token:
        raise RuntimeError(f"Could not extract request_token from redirect: {final_url}")

    # Step 4 — generate session
    sess_data = kite.generate_session(request_token, api_secret=api_secret)
    kite.set_access_token(sess_data["access_token"])

    return kite


# ── OHLC helpers ───────────────────────────────────────────────────────────────

def _fetch_candles(kite: KiteConnect, token: int, interval: str,
                   from_date: datetime.date, to_date: datetime.date) -> pd.DataFrame:
    data = kite.historical_data(token, from_date, to_date, interval)
    df = pd.DataFrame(data)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df


def _rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=window - 1, min_periods=window).mean()
    avg_loss = loss.ewm(com=window - 1, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast   = series.ewm(span=fast, adjust=False).mean()
    ema_slow   = series.ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line
    return macd_line, signal_line, histogram


def _structure(df: pd.DataFrame) -> str:
    """Very lightweight structure label from recent highs/lows."""
    if len(df) < 10:
        return "Insufficient data"
    closes = df["close"]
    mid    = len(closes) // 2
    first_half  = closes.iloc[:mid]
    second_half = closes.iloc[mid:]
    if second_half.mean() > first_half.mean() * 1.005:
        return "Uptrend / Bullish"
    elif second_half.mean() < first_half.mean() * 0.995:
        return "Downtrend / Bearish"
    else:
        return "Consolidation / Transition"


# ── DTE calculation ────────────────────────────────────────────────────────────

def _next_tuesday(today: datetime.date) -> datetime.date:
    """Return the date of the next Tuesday (weekly expiry). If today is Tuesday, return today."""
    days_ahead = (1 - today.weekday()) % 7   # Tuesday = weekday 1
    if days_ahead == 0:
        return today
    return today + datetime.timedelta(days=days_ahead)


def get_dte(today: datetime.date = None) -> tuple[int, datetime.date, str]:
    if today is None:
        today = datetime.date.today()
    expiry = _next_tuesday(today)
    dte = (expiry - today).days
    if dte <= 3:
        classification = "Near Expiry"
    elif dte <= 10:
        classification = "Mid Cycle"
    else:
        classification = "Far Expiry"
    return dte, expiry, classification


# ── Chart data ─────────────────────────────────────────────────────────────────

def get_ohlc_indicators(kite: KiteConnect) -> dict:
    today     = datetime.date.today()
    from_120d = today - datetime.timedelta(days=170)  # extra buffer for indicators
    from_30d  = today - datetime.timedelta(days=45)

    result = {}

    # Daily candles
    try:
        daily = _fetch_candles(kite, NIFTY_TOKEN, "day", from_120d, today)
        if not daily.empty:
            daily["rsi"]  = _rsi(daily["close"])
            ml, sl, hist  = _macd(daily["close"])
            daily["macd_line"]   = ml
            daily["macd_signal"] = sl
            daily["macd_hist"]   = hist

            last = daily.iloc[-1]
            prev = daily.iloc[-2] if len(daily) > 1 else last

            result["daily_rsi"]      = round(last["rsi"], 2)
            result["daily_rsi_sig"]  = round(daily["rsi"].ewm(span=9).mean().iloc[-1], 2)
            result["daily_rsi_dir"]  = "Rising" if last["rsi"] > prev["rsi"] else "Falling"
            result["daily_macd_line"]   = round(last["macd_line"], 2)
            result["daily_macd_signal"] = round(last["macd_signal"], 2)
            result["daily_macd_status"] = (
                "Bullish" if last["macd_line"] > last["macd_signal"] else "Bearish"
            )
            result["daily_close"]    = round(last["close"], 2)
            result["daily_structure"] = _structure(daily.tail(60))
        else:
            result.update({k: "Not Available" for k in [
                "daily_rsi","daily_rsi_sig","daily_rsi_dir",
                "daily_macd_line","daily_macd_signal","daily_macd_status",
                "daily_close","daily_structure"
            ]})
    except Exception as e:
        result["daily_error"] = str(e)
        result.update({k: "Not Available" for k in [
            "daily_rsi","daily_rsi_sig","daily_rsi_dir",
            "daily_macd_line","daily_macd_signal","daily_macd_status",
            "daily_close","daily_structure"
        ]})

    # Weekly candles (resampled)
    try:
        daily_raw = _fetch_candles(kite, NIFTY_TOKEN, "day", from_120d, today)
        if not daily_raw.empty:
            weekly = daily_raw.resample("W-TUE").agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum"
            }).dropna()
            if len(weekly) >= 5:
                weekly["rsi"] = _rsi(weekly["close"])
                wml, wsl, _  = _macd(weekly["close"])
                weekly["macd_line"]   = wml
                weekly["macd_signal"] = wsl

                wlast = weekly.iloc[-1]
                wprev = weekly.iloc[-2]
                result["weekly_rsi"]     = round(wlast["rsi"], 2)
                result["weekly_rsi_dir"] = "Rising" if wlast["rsi"] > wprev["rsi"] else "Falling"
                result["weekly_macd_line"]   = round(wlast["macd_line"], 2)
                result["weekly_macd_signal"] = round(wlast["macd_signal"], 2)
                result["weekly_macd_status"] = (
                    "Bullish" if wlast["macd_line"] > wlast["macd_signal"] else "Bearish"
                )
                result["weekly_structure"] = _structure(weekly.tail(20))
            else:
                result.update({k: "Not Available" for k in [
                    "weekly_rsi","weekly_rsi_dir","weekly_macd_line",
                    "weekly_macd_signal","weekly_macd_status","weekly_structure"
                ]})
        else:
            result.update({k: "Not Available" for k in [
                "weekly_rsi","weekly_rsi_dir","weekly_macd_line",
                "weekly_macd_signal","weekly_macd_status","weekly_structure"
            ]})
    except Exception as e:
        result["weekly_error"] = str(e)
        result.update({k: "Not Available" for k in [
            "weekly_rsi","weekly_rsi_dir","weekly_macd_line",
            "weekly_macd_signal","weekly_macd_status","weekly_structure"
        ]})

    # 4H candles (resample from 60-min)
    try:
        intraday = _fetch_candles(kite, NIFTY_TOKEN, "60minute", from_30d, today)
        if not intraday.empty:
            h4 = intraday.resample("4H").agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum"
            }).dropna()
            if len(h4) >= 20:
                h4["rsi"] = _rsi(h4["close"])
                hml, hsl, _ = _macd(h4["close"])
                h4["macd_line"]   = hml
                h4["macd_signal"] = hsl

                hlast = h4.iloc[-1]
                hprev = h4.iloc[-2]
                result["h4_rsi"]     = round(hlast["rsi"], 2)
                result["h4_rsi_dir"] = "Rising" if hlast["rsi"] > hprev["rsi"] else "Falling"
                result["h4_macd_line"]   = round(hlast["macd_line"], 2)
                result["h4_macd_signal"] = round(hlast["macd_signal"], 2)
                result["h4_macd_status"] = (
                    "Bullish" if hlast["macd_line"] > hlast["macd_signal"] else "Bearish"
                )
                result["h4_structure"] = _structure(h4.tail(30))
            else:
                result.update({k: "Not Available" for k in [
                    "h4_rsi","h4_rsi_dir","h4_macd_line",
                    "h4_macd_signal","h4_macd_status","h4_structure"
                ]})
        else:
            result.update({k: "Not Available" for k in [
                "h4_rsi","h4_rsi_dir","h4_macd_line",
                "h4_macd_signal","h4_macd_status","h4_structure"
            ]})
    except Exception as e:
        result["h4_error"] = str(e)
        result.update({k: "Not Available" for k in [
            "h4_rsi","h4_rsi_dir","h4_macd_line",
            "h4_macd_signal","h4_macd_status","h4_structure"
        ]})

    return result


# ── VIX ────────────────────────────────────────────────────────────────────────

def get_vix(kite: KiteConnect) -> dict:
    try:
        today    = datetime.date.today()
        from_5d  = today - datetime.timedelta(days=7)
        vix_data = _fetch_candles(kite, VIX_TOKEN, "day", from_5d, today)
        if vix_data.empty or len(vix_data) < 2:
            return {"vix": "Not Available", "vix_trend": "Not Available",
                    "vix_pct_change": "Not Available"}
        last  = vix_data.iloc[-1]
        prev  = vix_data.iloc[-2]
        vix_val = round(last["close"], 2)
        pct     = round((last["close"] - prev["close"]) / prev["close"] * 100, 2)
        trend   = "Rising" if pct > 0.5 else ("Falling" if pct < -0.5 else "Stable")
        return {"vix": vix_val, "vix_trend": trend, "vix_pct_change": pct}
    except Exception as e:
        return {"vix": "Not Available", "vix_trend": "Not Available",
                "vix_pct_change": "Not Available", "vix_error": str(e)}


# ── OI cache (persist between runs via git commit in workflow) ─────────────────

def _load_oi_cache() -> dict:
    """Load previous session's OI from cache file. Returns {} if not found."""
    try:
        if os.path.exists(OI_CACHE_FILE):
            with open(OI_CACHE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_oi_cache(expiry_str: str, strike_data: dict[float, dict]) -> None:
    """Save today's OI keyed by expiry so next run can compute true OI change."""
    cache = {
        "saved_date": datetime.date.today().isoformat(),
        "expiry":     expiry_str,
        "strikes":    {
            str(k): {"call_oi": v["call_oi"], "put_oi": v["put_oi"]}
            for k, v in strike_data.items()
        },
    }
    try:
        with open(OI_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"Warning: could not save OI cache: {e}")


def _load_futures_oi_cache() -> dict:
    """Return yesterday's futures OI from cache."""
    cache = _load_oi_cache()
    return cache.get("futures", {})


def _save_futures_oi_cache(symbol: str, oi: int) -> None:
    """Add futures OI to cache alongside options OI."""
    try:
        cache = {}
        if os.path.exists(OI_CACHE_FILE):
            with open(OI_CACHE_FILE, "r") as f:
                cache = json.load(f)
        cache.setdefault("futures", {})[symbol] = oi
        with open(OI_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"Warning: could not save futures OI cache: {e}")


# ── Option chain ───────────────────────────────────────────────────────────────

def get_option_chain(kite: KiteConnect, current_price: float = None) -> dict:
    try:
        today  = datetime.date.today()
        _, expiry, _ = get_dte(today)
        expiry_str = expiry.isoformat()

        instruments = kite.instruments("NFO")

        # Limit to current-week and next-week expiries only
        all_expiries = sorted(set(
            i["expiry"] for i in instruments
            if i.get("name") == "NIFTY" and i.get("instrument_type") in ("CE", "PE")
            and i.get("expiry")
        ))
        relevant_expiries = [e for e in all_expiries if e >= today][:2]

        nifty_opts = [
            i for i in instruments
            if (i.get("name") == "NIFTY" and
                i.get("instrument_type") in ("CE", "PE") and
                i.get("expiry") == expiry)
        ]
        if not nifty_opts:
            return {"option_chain_error": "No Nifty options found for expiry"}

        # Load yesterday's OI cache
        prev_cache = _load_oi_cache()
        prev_strikes = {}
        if (prev_cache.get("expiry") == expiry_str and
                prev_cache.get("saved_date") != today.isoformat()):
            prev_strikes = prev_cache.get("strikes", {})

        tokens = [i["instrument_token"] for i in nifty_opts]
        quotes = {}
        for chunk_start in range(0, len(tokens), 400):
            chunk = tokens[chunk_start: chunk_start + 400]
            q = kite.quote(chunk)
            quotes.update(q)
            time.sleep(0.3)

        # Build strike table
        strike_data: dict[float, dict] = {}
        for inst in nifty_opts:
            token  = inst["instrument_token"]
            key    = f"NFO:{inst['tradingsymbol']}"
            q      = quotes.get(key) or quotes.get(str(token), {})
            strike = inst["strike"]
            itype  = inst["instrument_type"]
            oi     = q.get("oi", 0) or 0
            ltp    = q.get("last_price", 0) or 0

            if strike not in strike_data:
                strike_data[strike] = {
                    "call_oi": 0, "put_oi": 0,
                    "call_oi_prev": 0, "put_oi_prev": 0,
                    "call_ltp": 0, "put_ltp": 0,
                }
            prev = prev_strikes.get(str(strike), {})
            if itype == "CE":
                strike_data[strike]["call_oi"]      = oi
                strike_data[strike]["call_oi_prev"]  = prev.get("call_oi", oi)
                strike_data[strike]["call_ltp"]      = ltp
            else:
                strike_data[strike]["put_oi"]        = oi
                strike_data[strike]["put_oi_prev"]   = prev.get("put_oi", oi)
                strike_data[strike]["put_ltp"]       = ltp

        # Save today's OI for tomorrow's run
        _save_oi_cache(expiry_str, strike_data)

        df = pd.DataFrame.from_dict(strike_data, orient="index").sort_index()
        df.index.name = "strike"

        total_call_oi = df["call_oi"].sum()
        total_put_oi  = df["put_oi"].sum()
        pcr_aggregate = round(total_put_oi / total_call_oi, 3) if total_call_oi else 0

        # ATM strike
        if current_price:
            atm = min(df.index, key=lambda x: abs(x - current_price))
        else:
            atm = df.index[len(df) // 2]

        # Snapshot PCR — OI within ±5 strikes of ATM
        atm_idx   = list(df.index).index(atm)
        near_range = df.iloc[max(0, atm_idx - 5): atm_idx + 6]
        snap_call = near_range["call_oi"].sum()
        snap_put  = near_range["put_oi"].sum()
        pcr_snapshot = round(snap_put / snap_call, 3) if snap_call else 0

        top3_call_oi = (
            df["call_oi"].nlargest(3).reset_index()
            .apply(lambda r: f"{int(r['strike'])} ({int(r['call_oi']):,})", axis=1)
            .tolist()
        )
        top3_put_oi = (
            df["put_oi"].nlargest(3).reset_index()
            .apply(lambda r: f"{int(r['strike'])} ({int(r['put_oi']):,})", axis=1)
            .tolist()
        )

        # OI change classification
        df["call_oi_chg"] = df["call_oi"] - df["call_oi_prev"]
        df["put_oi_chg"]  = df["put_oi"]  - df["put_oi_prev"]
        net_call_chg = df["call_oi_chg"].sum()
        net_put_chg  = df["put_oi_chg"].sum()

        if net_put_chg > 0 and net_call_chg < 0:
            oi_classification = "Strongly Bullish (put writing + call unwinding)"
        elif net_put_chg > 0 and net_call_chg >= 0:
            oi_classification = "Moderately Bullish (put writing)"
        elif net_call_chg > 0 and net_put_chg < 0:
            oi_classification = "Strongly Bearish (call writing + put unwinding)"
        elif net_call_chg > 0 and net_put_chg >= 0:
            oi_classification = "Moderately Bearish (call writing)"
        else:
            oi_classification = "Mixed / Neutral"

        # Top OI change strikes
        top_put_writing  = df.nlargest(3, "put_oi_chg")[["put_oi_chg"]].reset_index()
        top_call_writing = df.nlargest(3, "call_oi_chg")[["call_oi_chg"]].reset_index()

        return {
            "pcr_aggregate":     pcr_aggregate,
            "pcr_snapshot":      pcr_snapshot,
            "top3_call_oi":      ", ".join(top3_call_oi),
            "top3_put_oi":       ", ".join(top3_put_oi),
            "oi_classification": oi_classification,
            "net_call_oi_chg":   int(net_call_chg),
            "net_put_oi_chg":    int(net_put_chg),
            "total_call_oi":     int(total_call_oi),
            "total_put_oi":      int(total_put_oi),
            "atm_strike":        atm,
            "expiry":            expiry,
        }
    except Exception as e:
        return {"option_chain_error": str(e)}


# ── Futures OI classification ──────────────────────────────────────────────────

def get_futures_oi(kite: KiteConnect) -> dict:
    try:
        today = datetime.date.today()
        instruments = kite.instruments("NFO")
        fut_instruments = [
            i for i in instruments
            if i.get("name") == "NIFTY" and i.get("instrument_type") == "FUT"
        ]
        if not fut_instruments:
            return {"futures_oi": "Not Available", "futures_classification": "Not Available"}

        # Pick nearest expiry future
        fut_instruments.sort(key=lambda x: x["expiry"])
        near_fut = fut_instruments[0]
        token    = near_fut["instrument_token"]
        symbol   = f"NFO:{near_fut['tradingsymbol']}"

        q = kite.quote([symbol])
        q_data = q.get(symbol) or q.get(str(token), {})
        oi       = q_data.get("oi", 0) or 0
        ltp      = q_data.get("last_price", 0) or 0
        prev_cls = q_data.get("ohlc", {}).get("close", ltp) or ltp

        # Load yesterday's futures OI from cache (saved by previous run)
        fut_cache = _load_oi_cache().get("futures", {})
        oi_prev   = fut_cache.get(near_fut["tradingsymbol"], oi)

        # Save today's futures OI for tomorrow
        _save_futures_oi_cache(near_fut["tradingsymbol"], oi)

        price_up = ltp > prev_cls
        oi_up    = oi > oi_prev

        if price_up and oi_up:
            classification = "Long Buildup"
        elif price_up and not oi_up:
            classification = "Short Covering"
        elif not price_up and oi_up:
            classification = "Short Buildup"
        else:
            classification = "Long Unwinding"

        return {
            "futures_oi":             int(oi),
            "futures_oi_prev":        int(oi_prev),
            "futures_ltp":            round(ltp, 2),
            "futures_classification": classification,
            "futures_symbol":         near_fut["tradingsymbol"],
        }
    except Exception as e:
        return {"futures_oi": "Not Available", "futures_classification": "Not Available",
                "futures_error": str(e)}


# ── Advance / Decline (NSE scrape) ─────────────────────────────────────────────

def get_advance_decline() -> dict:
    """
    Scrape NSE website for Nifty 50 Advance/Decline.
    Falls back to Not Available gracefully — does not block session.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com/",
        }
        session = requests.Session()
        # Seed session cookie
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        time.sleep(1)

        resp = session.get(
            "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        advances  = data.get("advances",  "N/A")
        declines  = data.get("declines",  "N/A")
        unchanged = data.get("unchanged", "N/A")

        if advances == "N/A" or declines == "N/A":
            return {"breadth": "Not Available", "advances": "N/A", "declines": "N/A"}

        adv = int(advances)
        dec = int(declines)
        ratio = round(adv / dec, 2) if dec else 0

        if ratio >= 3:
            classification = f"Strongly Bullish {adv}/{dec} Nifty50"
        elif ratio >= 1.5:
            classification = f"Moderately Bullish {adv}/{dec} Nifty50"
        elif ratio >= 0.7:
            classification = f"Neutral {adv}/{dec} Nifty50"
        elif ratio >= 0.33:
            classification = f"Moderately Bearish {adv}/{dec} Nifty50"
        else:
            classification = f"Strongly Bearish {adv}/{dec} Nifty50"

        return {
            "breadth":      classification,
            "advances":     adv,
            "declines":     dec,
            "unchanged":    unchanged,
            "adv_dec_ratio": ratio,
        }
    except Exception as e:
        return {"breadth": "Not Available", "advances": "N/A", "declines": "N/A",
                "breadth_error": str(e)}


# ── Master pull function ───────────────────────────────────────────────────────

def pull_all_data(kite: KiteConnect) -> dict:
    """
    Pull all required data and return a single dict.
    Partial failures are recorded but do not stop execution.
    """
    today = datetime.date.today()
    dte, expiry, dte_class = get_dte(today)

    data = {
        "date":            today.strftime("%d-%b-%Y"),
        "dte":             dte,
        "expiry":          expiry.strftime("%d-%b-%Y"),
        "dte_class":       dte_class,
    }

    print("Pulling OHLC and indicators...")
    ohlc = get_ohlc_indicators(kite)
    data.update(ohlc)

    print("Pulling VIX...")
    vix = get_vix(kite)
    data.update(vix)

    current_price = data.get("daily_close")
    if current_price == "Not Available":
        current_price = None

    print("Pulling option chain...")
    chain = get_option_chain(kite, current_price=current_price)
    data.update(chain)

    print("Pulling futures OI...")
    fut = get_futures_oi(kite)
    data.update(fut)

    print("Pulling advance/decline...")
    breadth = get_advance_decline()
    data.update(breadth)

    return data


# ── Format as Section 0 extraction report ─────────────────────────────────────

def format_extraction_report(d: dict) -> str:
    """
    Convert raw data dict to the Council Section 0 Data Extraction Report format.
    This is sent as the user message to Claude.
    """

    def v(key, default="Not Available"):
        val = d.get(key, default)
        return str(val) if val is not None else default

    # RSI/MACD formatted strings
    daily_rsi_str = (
        f"{v('daily_rsi')} — {v('daily_rsi_dir')}, above signal {v('daily_rsi_sig')}"
        if d.get("daily_rsi") != "Not Available"
        else "Not Available"
    )
    daily_macd_str = (
        f"Line {v('daily_macd_line')} / Signal {v('daily_macd_signal')} — {v('daily_macd_status')}"
        if d.get("daily_macd_line") != "Not Available"
        else "Not Available"
    )
    weekly_rsi_str = (
        f"{v('weekly_rsi')} — {v('weekly_rsi_dir')}"
        if d.get("weekly_rsi") != "Not Available"
        else "Not Available"
    )
    weekly_macd_str = (
        f"Line {v('weekly_macd_line')} / Signal {v('weekly_macd_signal')} — {v('weekly_macd_status')}"
        if d.get("weekly_macd_line") != "Not Available"
        else "Not Available"
    )
    h4_rsi_str = (
        f"{v('h4_rsi')} — {v('h4_rsi_dir')}"
        if d.get("h4_rsi") != "Not Available"
        else "Not Available"
    )
    h4_macd_str = (
        f"Line {v('h4_macd_line')} / Signal {v('h4_macd_signal')} — {v('h4_macd_status')}"
        if d.get("h4_macd_line") != "Not Available"
        else "Not Available"
    )
    vix_str = (
        f"{v('vix')} ({v('vix_pct_change')}% on day)"
        if d.get("vix") != "Not Available"
        else "Not Available"
    )

    # Determine confidence mode
    missing = []
    if d.get("weekly_rsi") == "Not Available":   missing.append("Weekly Chart")
    if d.get("h4_rsi") == "Not Available":        missing.append("4H Chart")
    if d.get("vix") == "Not Available":           missing.append("India VIX")
    if d.get("pcr_aggregate") is None:            missing.append("Option Chain")
    if d.get("breadth") == "Not Available":       missing.append("Market Breadth")
    if d.get("futures_classification") == "Not Available": missing.append("Futures OI")
    # Always missing in automated mode:
    missing += ["Macro Notes", "Event Calendar", "FII/DII Flows", "Sector Heatmap"]

    tier1_missing = [m for m in missing if m in (
        "4H Chart", "India VIX", "Option Chain", "Weekly Chart"
    )]
    if tier1_missing:
        confidence_mode = "Reduced"
    elif len(missing) > 4:
        confidence_mode = "Moderate"
    else:
        confidence_mode = "Moderate"   # automated pipeline always Moderate (no Macro/Event)

    report = f"""================================================================
SECTION 0 — DATA EXTRACTION REPORT
================================================================
Automated extraction: {d.get('date', 'Unknown')}
Mode: Standard Mode

| Metric                   | Value                                      |
|--------------------------|--------------------------------------------|
| Daily RSI                | {daily_rsi_str}  |
| Daily MACD               | {daily_macd_str} |
| Daily Structure          | {v('daily_structure')}                     |
| Weekly RSI               | {weekly_rsi_str}                           |
| Weekly MACD              | {weekly_macd_str}                          |
| Weekly Structure         | {v('weekly_structure')}                    |
| 4H RSI                   | {h4_rsi_str}                              |
| 4H MACD                  | {h4_macd_str}                             |
| 4H Structure             | {v('h4_structure')}                        |
| VIX                      | {vix_str}                                  |
| VIX Trend                | {v('vix_trend')}                           |
| Aggregate PCR            | {v('pcr_aggregate')}                       |
| Snapshot PCR             | {v('pcr_snapshot')}                        |
| Major Call OI Strikes    | {v('top3_call_oi')}                        |
| Major Put OI Strikes     | {v('top3_put_oi')}                         |
| Net Call OI Change       | {v('net_call_oi_chg')} contracts           |
| Net Put OI Change        | {v('net_put_oi_chg')} contracts            |
| OI Change Classification | {v('oi_classification')}                   |
| Futures OI               | {v('futures_classification')} ({v('futures_symbol')}) |
| Market Breadth           | {v('breadth')}                             |
| Event Risk               | Unknown — no calendar provided             |
| Current Expiry           | {v('expiry')} (Tuesday Weekly)             |
| Days to Expiry (DTE)     | {v('dte')}                                 |
| DTE Classification       | {v('dte_class')}                           |

Extraction Confidence: {confidence_mode}
Missing: {', '.join(missing) if missing else 'None'}
"""
    return report


# ── Standalone test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Logging in to Kite...")
    kite = login_kite()
    print("Login successful.")
    data = pull_all_data(kite)
    report = format_extraction_report(data)
    print(report)
