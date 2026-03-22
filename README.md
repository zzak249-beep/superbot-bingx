# 🤖 Bot FUSION v1.0.0
## Trend Magic + RMI Trend Sniper + EMA

### Estrategia
Combina dos estrategias de TradingView:
1. **Trend Magic** (CCI + ATR) → detecta tendencia principal
2. **RMI Trend Sniper** (RSI + MFI) → detecta momentum
3. **EMA 9/21/50** → confirma dirección
4. **Bollinger Bands** → detecta sobreextensión
5. **Volumen** → confirma la señal

### Señales
- **LONG**: Trend Magic alcista + RMI cruza arriba de 66 + EMA alcista
- **SHORT**: Trend Magic bajista + RMI cruza abajo de 30 + EMA bajista

---

## 🚀 Deploy en Railway

### PASO 1 — Sube a GitHub
1. Crea repositorio nuevo en GitHub
2. Sube estos archivos: `main.py`, `requirements.txt`, `Procfile`

### PASO 2 — Conecta Railway
1. Ve a [railway.app](https://railway.app)
2. New Project → Deploy from GitHub
3. Selecciona tu repositorio

### PASO 3 — Variables de entorno
En Railway → Variables → Raw Editor, pega:

```
BINGX_API_KEY=tu_key
BINGX_API_SECRET=tu_secret
TELEGRAM_BOT_TOKEN=tu_token
TELEGRAM_CHAT_ID=tu_chat_id
AUTO_TRADING_ENABLED=true
MAX_POSITION_SIZE=7
MIN_TRADE_USDT=5
LEVERAGE=3
MAX_OPEN_TRADES=3
TAKE_PROFIT_PCT=2.0
STOP_LOSS_PCT=1.0
TRAILING_STOP_ENABLED=true
ENABLE_LONGS=true
ENABLE_SHORTS=true
MIN_SCORE=70
MIN_VOLUME_24H=500000
MAX_SYMBOLS_TO_ANALYZE=60
BTC_FILTER_PCT=2.0
CHECK_INTERVAL=120
USE_LIMIT_ORDERS=true
CCI_LENGTH=20
ATR_LENGTH=5
ATR_MULTIPLIER=1.0
RMI_LENGTH=14
RMI_POSITIVE=66
RMI_NEGATIVE=30
```

---

## ⚙️ Configuración BingX API
1. BingX → Perfil → Gestión de API
2. Permisos: ✅ Leer + ✅ Trading con Futuros Perpetuo
3. Sin restricciones de IP

## 📱 Configuración Telegram
1. Busca @BotFather en Telegram
2. /newbot → copia el token
3. Busca @userinfobot → obtén tu chat_id

---

## 📊 Parámetros recomendados con $60-100 USDT

| Parámetro | Valor | Por qué |
|---|---|---|
| MAX_POSITION_SIZE | 7 | ~10% del capital |
| LEVERAGE | 3 | Sin riesgo de liquidación |
| MAX_OPEN_TRADES | 3 | Máximo $21 comprometido |
| MIN_SCORE | 70 | Balance entre señales y calidad |
| TP_PCT | 2.0 | RR 2:1 con SL de 1% |

---

## 🔍 Cómo leer los logs
```
★ 📈 LONG BTC-USDT score:85    → señal detectada
TrendMagic_BULL(35) | RMI_BUY(72)(40) | EMA_BULL+(20)  → razones
ENTRADA LÍMITE OK 5.0 contratos @ $1.234 maker  → orden ejecutada
TP ✅ @ $1.259  SL ✅ @ $1.221  → protecciones activas
✅ TAKE PROFIT BTC-USDT PnL:+$0.42  → ganancia
```
