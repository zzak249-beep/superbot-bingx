# Sniper Bot V49 Definitivo

Bot algorítmico que opera en **BingX Perpetual Futures** con la lógica del V49:
Motor Markov 200 velas + ADX Slope Adaptativo + STC + POC Institucional + Triple Barrera.

---

## Estructura

```
sniper_v49/
├── main.py          # Loop principal
├── indicators.py    # Motor de indicadores (V49 portado a Python)
├── exchange.py      # Cliente BingX via CCXT
├── telegram_bot.py  # Notificaciones Telegram
├── config.py        # Configuración central
├── requirements.txt
├── Procfile         # Railway worker
├── railway.toml     # Config Railway
└── .env.example     # Variables de entorno
```

---

## Setup local

```bash
git clone https://github.com/TU_USUARIO/sniper-bot-v49
cd sniper-bot-v49
pip install -r requirements.txt
cp .env.example .env
# Edita .env con tus claves
python main.py
```

---

## Variables de entorno (.env)

| Variable | Descripción |
|---|---|
| `BINGX_API_KEY` | API Key de BingX |
| `BINGX_API_SECRET` | API Secret de BingX |
| `TELEGRAM_BOT_TOKEN` | Token del bot de Telegram |
| `TELEGRAM_CHAT_ID` | Tu chat ID de Telegram |
| `SYMBOL` | Par a operar (ej: `BTC/USDT`) |
| `TIMEFRAME` | Temporalidad (`5m`, `15m`, `1h`) |
| `RISK_PCT` | % del balance por operación (ej: `2.0`) |
| `LEVERAGE` | Apalancamiento (`1` = sin apalancamiento) |
| `MODE` | `paper` (simulado) o `live` (dinero real) |

---

## Despliegue en Railway

1. Sube el proyecto a GitHub
2. Entra en [railway.app](https://railway.app) → **New Project → Deploy from GitHub**
3. Selecciona el repositorio
4. Ve a **Variables** y añade todas las del `.env`
5. Railway detecta el `Procfile` y arranca `python main.py` automáticamente
6. El bot se reinicia solo si falla (configurado en `railway.toml`)

---

## Obtener API Keys BingX

1. Entra en [bingx.com](https://bingx.com) → Cuenta → API Management
2. Crea una clave con permisos: **Read + Trade** (sin retiradas)
3. Whitelist la IP de Railway (o deja abierta solo para trade)

## Obtener Telegram Bot Token

1. Habla con [@BotFather](https://t.me/BotFather) en Telegram
2. `/newbot` → sigue las instrucciones → copia el token
3. Para obtener tu `CHAT_ID`: habla con [@userinfobot](https://t.me/userinfobot)

---

## Lógica de señales V49

### LONG entra cuando:
- `low` rompe por debajo del último valle (sweep de liquidez)
- `close` está por debajo del VWAP
- Magic Slope > Slope Adaptativo (ADX)
- RVOL > 1.5x (volumen institucional)
- Prob. Markov Bull > umbral
- STC < 75 (no sobrecomprado)

### SHORT entra cuando:
- `high` rompe por encima del último pico (sweep de liquidez)
- `close` está por encima del VWAP
- Magic Slope < -Slope Adaptativo (ADX)
- RVOL > 1.5x
- Prob. Markov Bear > umbral
- STC > 25 (no sobrevendido)

### Triple barrera de salida:
- **TP**: precio_entrada ± ATR14 × 2.0
- **SL**: precio_entrada ∓ ATR14 × 1.2
- **Tiempo**: cierre forzado tras 20 velas sin resolver

---

## Advertencia

Operar con dinero real conlleva riesgo de pérdida. Testea siempre en modo `paper` primero.
El bot no garantiza resultados. Úsalo bajo tu propia responsabilidad.
