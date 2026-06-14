# Nifty Council — Automated F&O Analysis Pipeline

Pulls Nifty market data at 4:00 PM IST every weekday, runs the F&O Trading Council
framework analysis via Claude API, and delivers the result to your phone via Telegram.

No automated trade execution. Ever.

---

## What It Does

1. Logs into Zerodha Kite automatically using TOTP (no manual token refresh)
2. Pulls Daily, Weekly, 4H OHLC + RSI/MACD, India VIX, full option chain, Futures OI, Advance/Decline
3. Formats all data into the Council Section 0 extraction report
4. Sends to Claude API with the compressed Council Constitution as system prompt
5. Claude runs the full Council framework (Sections 0–13)
6. Sends you a formatted summary alert on Telegram
7. Attaches the full Council report as a `.txt` file

---

## Setup (One-Time)

### Step 1 — Enable Kite API on Zerodha

1. Go to [https://developers.kite.trade](https://developers.kite.trade) and log in with your Zerodha credentials.
2. Click **Create new app**.
3. Fill in:
   - App name: anything (e.g. "Council Pipeline")
   - App type: **Connect**
   - Redirect URL: `https://127.0.0.1` (dummy — not used for TOTP login)
4. After creating, note your **API Key** and **API Secret** from the app page.

### Step 2 — Get your Kite TOTP Secret

> This is the **underlying TOTP secret key**, NOT the 6-digit code that changes every 30 seconds.

1. Log in to your Zerodha Kite account at [https://kite.zerodha.com](https://kite.zerodha.com).
2. Go to **My Profile → Settings → Security & Login**.
3. Under **Two-factor authentication (TOTP)**, look for the option to set up or view your authenticator.
4. When setting up TOTP, Zerodha shows you a QR code and also a **text secret key** (usually 16 or 32 characters, e.g. `JBSWY3DPEHPK3PXP`).
5. **Save this secret key** — this is what goes into `KITE_TOTP_SECRET`.

> If you already have TOTP set up and cannot see the secret, you may need to disable and re-enable TOTP to retrieve it.

### Step 3 — Create a Telegram Bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts:
   - Give your bot a name (e.g. "Council Alert Bot")
   - Give your bot a username (must end in `bot`, e.g. `council_alert_bot`)
3. BotFather will send you a **Bot Token** — looks like `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`.
4. Save this as `TELEGRAM_BOT_TOKEN`.

### Step 4 — Get your Telegram Chat ID

1. Start a conversation with your new bot by searching for it in Telegram and pressing **Start**.
2. Send any message to the bot (e.g. "hello").
3. Open this URL in a browser, replacing `YOUR_BOT_TOKEN` with your actual token:
   ```
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```
4. In the JSON response, find `"chat"` → `"id"`. This number is your **Chat ID**.
5. Save this as `TELEGRAM_CHAT_ID`.

> If the response is empty, send another message to the bot and try again.

### Step 5 — Create a GitHub Private Repository

1. Go to [https://github.com/new](https://github.com/new).
2. Create a **private** repository named `nifty-council` (or any name you like).
3. Push all the files from this folder to the repository:
   ```bash
   cd nifty-council
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/YOUR_USERNAME/nifty-council.git
   git push -u origin main
   ```

### Step 6 — Add GitHub Secrets

1. In your GitHub repository, go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret** for each of the following:

| Secret Name        | Where to find it                                    |
|--------------------|-----------------------------------------------------|
| `KITE_API_KEY`     | Kite developer app page (Step 1)                    |
| `KITE_API_SECRET`  | Kite developer app page (Step 1)                    |
| `KITE_USER_ID`     | Your Zerodha login ID (e.g. `AB1234`)               |
| `KITE_PASSWORD`    | Your Zerodha login password                         |
| `KITE_TOTP_SECRET` | TOTP secret key from Zerodha (Step 2) — not the 6-digit code |
| `ANTHROPIC_API_KEY`| From [https://console.anthropic.com](https://console.anthropic.com) → API Keys |
| `TELEGRAM_BOT_TOKEN` | From @BotFather (Step 3)                         |
| `TELEGRAM_CHAT_ID` | From getUpdates call (Step 4)                       |

---

## Testing Manually

### Test 1 — Trigger from GitHub

1. Go to your repository on GitHub.
2. Click **Actions** → **Nifty Council Daily Analysis** → **Run workflow**.
3. Click the green **Run workflow** button.
4. Watch the run logs in real time.
5. A Telegram message should arrive within a few minutes.

### Test 2 — Trigger from GitHub Mobile App

1. Install the **GitHub** mobile app and log in.
2. Go to your repository → Actions → Nifty Council Daily Analysis.
3. Tap **Run workflow**.

### Test 3 — Run Locally (for debugging)

Set the environment variables on your machine:

```bash
# Windows PowerShell
$env:KITE_API_KEY       = "your_key"
$env:KITE_API_SECRET    = "your_secret"
$env:KITE_USER_ID       = "AB1234"
$env:KITE_PASSWORD      = "your_password"
$env:KITE_TOTP_SECRET   = "your_totp_secret"
$env:ANTHROPIC_API_KEY  = "sk-ant-..."
$env:TELEGRAM_BOT_TOKEN = "123456:..."
$env:TELEGRAM_CHAT_ID   = "987654321"

# Then run:
python council_runner.py
```

---

## How to Verify It Is Working

After the first successful run:

- [ ] Telegram alert arrives with bias, regime, VIX, DTE, trade recommendation or NO TRADE
- [ ] Telegram document attachment arrives as `Council_YYYY-MM-DD.txt`
- [ ] Full report has all 14 sections (0 through 13)
- [ ] TELEGRAM SUMMARY section is at the end
- [ ] RSI values look reasonable (compare to TradingView Nifty daily chart)
- [ ] VIX value matches Kite/NSE VIX
- [ ] PCR values look roughly correct vs Kite option chain display
- [ ] DTE is calculating correctly to next Tuesday
- [ ] `oi_cache.json` was committed back to the repository by the workflow
- [ ] After the second run (next day), OI Change Classification reflects actual changes

### Checking the GitHub Actions log

1. Go to Actions → most recent run.
2. Expand each step to see output.
3. Look for "Council session complete" and "Telegram alert sent."

---

## How OI Change Works

The pipeline saves today's option chain OI to `oi_cache.json` in the repository.
On the next run, it loads this file to compute the true OI change vs yesterday.

The GitHub Actions workflow commits `oi_cache.json` back after each run (`[skip ci]` tag prevents re-triggering).

First run: OI change will show 0 (no cache yet). From the second run onwards, OI change is real.

When a new expiry week starts, the cache automatically resets (different expiry date detected).

---

## Error Notifications

The pipeline never fails silently. If something goes wrong, you receive a Telegram message:

- `COUNCIL ERROR: Kite login failed` — Zerodha credentials issue or TOTP mismatch
- `COUNCIL ERROR: Data pull failed` — Kite API down or market data unavailable
- `COUNCIL ERROR: Analysis failed` — Claude API issue

If Telegram itself is unavailable, errors are written to `council_fallback.log` in the repository and to the GitHub Actions log.

---

## Schedule

Runs automatically at **4:00 PM IST (10:30 UTC) Monday to Friday**.

GitHub Actions cron: `30 10 * * 1-5`

Note: GitHub Actions schedule may occasionally run a few minutes late (±15 min) due to platform load.
If you need it exactly at 4 PM, use the manual trigger from the GitHub mobile app as a backup.

---

## Cost

| Component       | Cost                      |
|-----------------|---------------------------|
| Claude API      | ~Rs 10–12 per session     |
| 22 trading days | ~Rs 220–264               |
| Testing/retries | ~Rs 100–150               |
| Everything else | Free                      |
| **Monthly total** | **Rs 300–400**          |

---

## Validation Checklist (Before Going Live)

- [ ] TOTP auto-login works reliably on first trigger
- [ ] RSI values match TradingView Nifty daily within 0.5 points
- [ ] MACD values match TradingView within tolerance
- [ ] VIX value matches Kite/NSE display
- [ ] PCR matches Kite option chain display
- [ ] Top OI strikes match Kite option chain
- [ ] DTE calculates correctly for next Tuesday
- [ ] Claude output has all sections 0–13
- [ ] TELEGRAM SUMMARY present and correctly parsed
- [ ] Telegram alert arrives with correct formatting and emojis
- [ ] Full report arrives as `.txt` attachment
- [ ] Error messages arrive on Telegram when tested (you can temporarily break a secret to test)
- [ ] GitHub Actions runs on schedule without manual trigger
- [ ] `oi_cache.json` is being committed back after each run
- [ ] No credentials visible anywhere in code or logs

---

## Known Limitations

| Gap | Status |
|-----|--------|
| Event calendar (RBI, Fed, CPI) | Not auto-pulled. Agent 3 defaults to Unknown. Run a manual Council session before known binary events. |
| Global market context (US, Asia) | Not pulled. Agent 3 uses Unknown for Global Sentiment. |
| FII/DII flows | Not pulled. NSE scrape too unreliable to depend on. |
| Sector heatmap | Not available in automation. Agent 3 notes unavailable. |
| Previous day OI (first run) | OI change shows 0 on first ever run. Real from second run. |

---

## Files

```
nifty-council/
├── .github/
│   └── workflows/
│       └── council.yml          # GitHub Actions scheduler
├── council_runner.py            # Main orchestration
├── data_puller.py               # Kite API data extraction
├── constitution_compressed.py   # Compressed Council framework (system prompt)
├── claude_runner.py             # Claude API integration
├── telegram_delivery.py         # Telegram bot delivery
├── oi_cache.json                # Auto-generated — previous day OI cache
├── requirements.txt             # Python dependencies
└── README.md                    # This file
```
