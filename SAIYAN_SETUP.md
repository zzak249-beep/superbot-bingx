# SAIYAN OCC BOT — GUÍA COMPLETA DE INSTALACIÓN
## GitHub + Railway + Telegram

---

## ¿QUÉ ES ESTE BOT?

Implementación del indicador **SAIYAN OCC v6_1_23** (Pine Script) como bot real en BingX.

### Señal principal
```
closeSeries = ALMA(close, 2, offset=0.85, sigma=5) en TF 120m
openSeries  = ALMA(open,  2, offset=0.85, sigma=5) en TF 120m

LONG  → crossover (closeSeries > openSeries)
SHORT → crossunder(closeSeries < openSeries)
```

### Risk Management (replica exacta del Pine)
| Nivel | % precio | % del trade |
|-------|----------|-------------|
| TP1   | +1.0%    | 50%         |
| TP2   | +1.5%    | 30%         |
| TP3   | +2.0%    | 20%         |
| SL    | -0.5%    |             |

### Filtros adicionales
- **EMA144**: solo long si precio > EMA144; solo short si precio < EMA144
- **RSI28**: no entrar en sobrecompra (>65) para longs; no en sobreventa (<35) para shorts
- **Break-even**: SL se mueve a entrada tras alcanzar TP1
- **Señal de inversión**: cierra el trade si MA crossover se invierte

### TradingBrain (auto-aprendizaje)
- Registra cada trade con par, PnL, RSI, hora, razón de cierre
- Blacklist automática: 3 pérdidas seguidas en un par = bloqueo 6h
- Ajuste adaptativo de TP/SL cada 25 trades según win rate
- Estadísticas por TP1/TP2/TP3/SL para ver qué niveles funcionan mejor

---

## PASO 1 — CREAR BOT DE TELEGRAM

1. Abre Telegram → busca `@BotFather`
2. Escribe `/newbot`
3. Nombre: `Saiyan OCC Bot`
4. Username: `mi_saiyan_occ_bot` (debe terminar en `bot`)
5. Guarda el **TOKEN** que te da (ej: `7123456789:AAF...`)
6. Busca `@userinfobot` → escríbele cualquier cosa → guarda tu **Chat ID** (número)

---

## PASO 2 — CREAR REPOSITORIO EN GITHUB

1. Ve a **github.com** → botón verde **New**
2. Nombre: `saiyan-occ-bot`
3. Privado ✓ → **Create repository**
4. Click en **"uploading an existing file"**
5. Sube estos 2 archivos:
   - `saiyan_bot.py` (el bot)
   - `requirements_saiyan.txt` (renómbralo a `requirements.txt`)
6. **Commit changes**

---

## PASO 3 — DESPLEGAR EN RAILWAY

1. Ve a **railway.app** → **New Project** → **Deploy from GitHub repo**
2. Selecciona `saiyan-occ-bot`
3. Railway lo detectará automáticamente

### Configurar el comando de inicio
1. En tu proyecto Railway → pestaña **Settings**
2. Busca **"Start Command"** y escribe:
   ```
   python saiyan_bot.py
   ```

### Variables de entorno (Settings → Variables → Add Variable)

**OBLIGATORIAS:**
```
BINGX_API_KEY       = tu_api_key_de_bingx
BINGX_API_SECRET    = tu_api_secret_de_bingx
TELEGRAM_BOT_TOKEN  = 7123456789:AAF...
TELEGRAM_CHAT_ID    = -100123456789
```

**CONFIGURACIÓN DE ESTRATEGIA:**
```
TIMEFRAME           = 15m
HTF_MULT            = 8
MA_TYPE             = ALMA
MA_PERIOD           = 2
ALMA_OFFSET         = 0.85
ALMA_SIGMA          = 5
TP1_PCT             = 1.0
TP2_PCT             = 1.5
TP3_PCT             = 2.0
SL_PCT              = 0.5
TP1_QTY             = 50
TP2_QTY             = 30
TP3_QTY             = 20
```

**CONFIGURACIÓN DE POSICIÓN:**
```
FIXED_USDT          = 8
LEVERAGE            = 10
MAX_OPEN_TRADES     = 4
MIN_VOLUME_USDT     = 1000000
TOP_N_SYMBOLS       = 200
POLL_SECONDS        = 60
COOLDOWN_MIN        = 60
```

**FILTROS:**
```
EMA_FILTER          = true
RSI_FILTER          = true
USE_WT_FILTER       = false
EMA_LEN             = 144
RSI_OB              = 65
RSI_OS              = 35
```

**PROTECCIÓN:**
```
DAILY_LOSS_LIMIT    = 4.0
MAX_DRAWDOWN        = 15.0
CB_PAUSE_MIN        = 30
DEFENSIVE_MODE_ONLY = true
MAX_CONSEC_LOSS     = 3
MAX_SPREAD_PCT      = 0.8
```

4. Una vez configuradas las variables → Railway hace deploy automático

---

## PASO 4 — OBTENER CLAVES BINGX

1. Entra a **bingx.com** → tu cuenta → API Management
2. **Create API** → nombre: `saiyan-occ-bot`
3. Permisos: ✓ **Trade** | ✓ **Read**  (NO Withdraw)
4. Guarda API Key y Secret Key

---

## NOTIFICACIONES QUE RECIBIRÁS EN TELEGRAM

```
SAIYAN OCC BOT — INICIADO
===============================
Estrategia: ALMA crossover 15m×8=120m
TP1:1.0% TP2:1.5% TP3:2.0% SL:0.5%
Qty: 50%/30%/20%
...
```

```
🟢 LONG — BTCUSDT
================================
MA ALMA(2) crossover 120m
Entrada: 45000.00
TP1: 45450.00 (1.0%)
TP2: 45675.00 (1.5%)
TP3: 45900.00 (2.0%)
SL:  44775.00 (-0.5%)
RSI: 48.3 | EMA: BULL
```

```
WIN — BTCUSDT
LONG | TP2 EXCHANGE
45000.00 → 45675.00 (1.5%)
PnL final: $12.15
2W/0L WR:100%
```

```
BRAIN AJUSTE
WR últimos 25: 68% | PnL med: $8.45
TP1 1.0% → 1.05%
TP2 1.5% → 1.575%
```

---

## PREGUNTAS FRECUENTES

**¿El bot puede perder más de lo configurado?**
El SL está fijado en el exchange. Si el precio cae -0.5% el exchange cierra automáticamente. El modo defensivo reduce el tamaño al 50% si la pérdida diaria supera el límite.

**¿Qué timeframe usa realmente para la señal?**
TF base × multiplicador. Con TIMEFRAME=15m y HTF_MULT=8 → señal en 120m (2 horas). Puedes cambiarlo: `HTF_MULT=4` = 60m, `HTF_MULT=16` = 240m.

**¿Cómo cambiar el tipo de MA?**
Variable `MA_TYPE`: `ALMA` (default), `TEMA`, `HullMA`

**¿El bot para de operar si tiene pérdidas?**
NO. Con `DEFENSIVE_MODE_ONLY=true`:
- Pérdida diaria > límite → opera al 50% (no para)
- Circuit Breaker → pausa 30 min y reinicia (no para)

**¿Cómo ver el historial de trades?**
Railway → tu proyecto → Logs (puedes ver todo en tiempo real)
Los trades también se guardan en `/tmp/saiyan_occ_trades.csv`

**¿Qué hace el Brain cuando hay muchas pérdidas?**
1. Blacklist el par 6h si tiene 3 pérdidas seguidas
2. Reduce los TPs si win rate < 40%
3. Amplía los TPs si win rate > 65%
4. Notifica cada ajuste por Telegram

---

## CONFIGURACIONES RECOMENDADAS

### Conservadora (menos señales, más calidad)
```
TIMEFRAME=15m, HTF_MULT=16 → 240m señal
TP1_PCT=0.8, TP2_PCT=1.2, TP3_PCT=1.8
SL_PCT=0.5
LEVERAGE=8
```

### Agresiva (más señales, más riesgo)
```
TIMEFRAME=5m, HTF_MULT=8 → 40m señal
TP1_PCT=1.0, TP2_PCT=1.5, TP3_PCT=2.0
SL_PCT=0.4
LEVERAGE=15
```

### Original Pine (replica exacta)
```
TIMEFRAME=15m, HTF_MULT=8 → 120m señal
MA_TYPE=ALMA, MA_PERIOD=2
TP1_PCT=1.0, TP2_PCT=1.5, TP3_PCT=2.0, SL_PCT=0.5
TP1_QTY=50, TP2_QTY=30, TP3_QTY=20
LEVERAGE=10
```

---

*SAIYAN OCC Bot — Implementación Python del indicador SAIYAN OCC v6_1_23*
