# 🤖 BingX SuperBot

Automated crypto trading bot for BingX Perpetual Futures.  
Combines **VWAP Volatility Bands [BOSWaves]** + **Sniper Entry [KhanSaab V.02]** strategies.

---

## 🧠 Strategy Logic

### Signal Generation (dual confirmation required)
| Check | Source |
|---|---|
| EMA 9/21 crossover | Sniper trigger |
| T3-smoothed VWAP trend direction | BOSWaves |
| Bull/Bear score ≥ 5/7 conditions | Sniper dashboard |
| ADX > 25 (strong trend) | Both indicators |
| Price not overextended (within Band 4) | BOSWaves |
| Higher-timeframe RSI alignment | Multi-TF filter |

### Entry Conditions (ALL must be true)
**LONG**: EMA9 crosses above EMA21 + T3-VWAP rising + Bull score ≥ 71% + ADX > 25  
**SHORT**: EMA9 crosses below EMA21 + T3-VWAP falling + Bear score ≥ 71% + ADX > 25

### Risk Management
- **Risk per trade**: 1% of balance
- **Max open positions**: 5
- **Stop Loss**: entry ± (ATR × 1.5)
- **TP1**: 1× risk → close 50% (lock profit)
- **TP2**: 2× risk → close remaining
- **Daily loss limit**: 5% → kill switch
- **Leverage**: 5× isolated margin

### Commission Savings
| Order Type | Fee | Savings vs Market |
|---|---|---|
| LIMIT (maker) | 0.02% | **60% cheaper** |
| MARKET (taker) | 0.05% | baseline |

The bot **always uses LIMIT orders** at entry (0.03% offset from market price for fill probability).

---

## 🚀 Deploy to Railway

### 1. Prepare your BingX API Key
1. Go to [BingX API Management](https://bingx.com/en-us/account/api/)
2. Create new API key with **Futures Trading** permission
3. Whitelist your Railway IP (or use IP-unrestricted for simplicity)

### 2. Push to GitHub
```bash
git init
git add .
git commit -m "Initial bot"
git remote add origin https://github.com/YOUR_USER/bingx-superbot.git
git push -u origin main
```

### 3. Deploy on Railway
1. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
2. Select your repo
3. Add environment variables:

| Variable | Value |
|---|---|
| `BINGX_API_KEY` | your key |
| `BINGX_SECRET_KEY` | your secret |
| `DRY_RUN` | `true` ← **START WITH THIS** |
| `SCAN_PERIOD_SECONDS` | `900` |
| `LIMIT_ENTRY` | `true` |

4. Railway auto-deploys on every git push

---

## ⚠️ CRITICAL: Test Protocol

```
Step 1: DRY_RUN=true  →  Run for 3-7 days. Check logs. Verify signal quality.
Step 2: DRY_RUN=false →  Start with small balance ($50-100 USDT)
Step 3: Scale up       →  Only after validating live performance
```

**Never skip dry run. Real money can be lost.**

---

## 📁 File Structure

```
bingx-superbot/
├── main.py          # Entry point
├── bot.py           # Main orchestrator loop
├── strategy.py      # Signal logic (VWAP + Sniper replication)
├── scanner.py       # Market-wide symbol scanner
├── bingx_client.py  # BingX REST API wrapper
├── risk_manager.py  # Position sizing + kill switch
├── requirements.txt
├── railway.toml     # Railway deployment config
├── Procfile
└── .env.example
```

---

## 📊 Bot Loop

```
Every 15 minutes:
  1. Check daily reset
  2. Manage open positions (TP1/TP2 partials)
  3. If capacity available:
     a. Scan all USDT perpetuals (parallel, 8 threads)
     b. Filter: volume > $5M/day, ADX > 25
     c. Rank by adjusted score
     d. Open top signals with LIMIT orders
  4. Sleep until next cycle
```

---

## 🔧 Customization

Edit `risk_manager.py`:
- `RISK_PER_TRADE = 0.01`   → change to 0.005 for more conservative
- `MAX_POSITIONS = 5`       → max concurrent trades
- `LEVERAGE = 5`            → lower = safer
- `DAILY_LOSS_LIMIT = 0.05` → kill switch threshold

Edit `strategy.py`:
- `MIN_SCORE = 5`           → raise to 6-7 for fewer but higher quality signals
- `T3_LEN = 28`             → matches Pine Script exactly
- `SL_ATR_MULT = 1.5`       → wider stop = more breathing room

Edit `scanner.py`:
- `MIN_24H_VOLUME_USDT`     → raise for more liquid pairs only
- `LTF_INTERVAL = "15m"`    → change to "1h" for longer-term trades

---

## 📜 Disclaimer

This bot is for educational purposes. Cryptocurrency trading involves significant risk.  
Past performance does not guarantee future results. Trade only what you can afford to lose.
