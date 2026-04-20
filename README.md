# 🤖 BingX Signal Projection Bot

Bot de trading automático para BingX Perpetual Futures. Combina el **Signal Projection Explorer** de TradingView con filtros ADX, ATR y RSI, scanner de monedas explosivas y gestión de riesgo automática.

---

## ⚙️ Arquitectura

```
scanner.py          → Escanea todos los pares de BingX, rankea por volumen spike + momentum
signal_engine.py    → Signal Projection Explorer (Pine Script → Python) + ADX/ATR/RSI
risk_manager.py     → Sizing por ATR, trailing stop, límites diarios
bingx_client.py     → API REST de BingX con autenticación HMAC-SHA256
bot.py              → Loop principal: scan → señal → entrada → gestión → salida
config.py           → Toda la configuración vía variables de entorno
```

### Flujo del bot
```
Cada N segundos:
  1. Gestiona posiciones abiertas (trailing stop, SL/TP)
  2. Si pasaron 15 min → re-escanea mercado buscando monedas explosivas
  3. Para cada candidata:
       - Descarga 300 velas
       - Calcula SMA crossover + ADX + ATR + RSI + régimen
       - Si score >= MIN_SCORE → abre posición con SL/TP por ATR
```

---

## 🚀 Setup Local

```bash
# 1. Clonar
git clone https://github.com/tuusuario/bingx-bot
cd bingx-bot

# 2. Variables de entorno
cp .env.example .env
# Edita .env con tus claves de BingX

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Ejecutar (modo demo por defecto)
cd src
python bot.py
```

---

## 🚂 Deploy en Railway

### Opción 1 — Desde GitHub (recomendado)
1. Sube el repo a GitHub
2. Entra en [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Selecciona el repo → Railway detecta el `Dockerfile` automáticamente
4. Ve a **Variables** y añade todas las del `.env.example`
5. Cambia `DEMO_MODE=false` cuando estés listo para real

### Opción 2 — Railway CLI
```bash
npm install -g @railway/cli
railway login
railway up
```

### Variables de entorno en Railway
En el panel de Railway → tu servicio → Variables, añade:
```
BINGX_API_KEY         = tu_api_key
BINGX_SECRET_KEY      = tu_secret_key
DEMO_MODE             = true          # cambia a false para real
LEVERAGE              = 5
MAX_OPEN_POSITIONS    = 5
MAX_RISK_PER_TRADE    = 1.5
MAX_DAILY_LOSS_PCT    = 5.0
MIN_SCORE             = 55.0
TRADE_INTERVAL        = 15m
```

---

## 🔑 Obtener API Keys de BingX

1. Entra en [bingx.com](https://bingx.com) → Perfil → Gestión de API
2. Crea nueva clave → activa **Futuros de Perpetuos**
3. **NO actives retiradas** (no las necesita el bot)
4. Guarda API Key y Secret Key en tu `.env`

---

## 🛡️ Gestión de Riesgo

| Parámetro | Valor por defecto | Descripción |
|-----------|------------------|-------------|
| MAX_RISK_PER_TRADE | 1.5% | Máximo del balance arriesgado por trade |
| MAX_DAILY_LOSS_PCT | 5.0% | El bot para si pierde este % en el día |
| MAX_OPEN_POSITIONS | 5 | Posiciones simultáneas máximas |
| MIN_SCORE | 55/100 | Score mínimo de la señal para entrar |
| LEVERAGE | 5x | Apalancamiento (no subas de 10x) |

**Stop Loss**: calculado automáticamente como `entrada ± ATR × 1.2`  
**Take Profit**: calculado automáticamente como `entrada ± ATR × 2.5`  
**Trailing Stop**: se activa y sigue el precio con `ATR × 1.5`

---

## 📊 Score de Señal (0–100)

| Condición | Puntos |
|-----------|--------|
| ADX ≥ 30 (tendencia fuerte) | 30 pts |
| ADX ≥ 25 | 22 pts |
| ADX ≥ umbral | 15 pts |
| ATR > ATR_MA × 1.3 (expansión) | 20 pts |
| ATR > ATR_MA × 1.0 | 12 pts |
| RSI favorable (≥60 long, ≤40 short) | 20 pts |
| DMI alineado con dirección | 20 pts |
| Precio sobre/bajo EMA 200 | 10 pts |

---

## ⚠️ Advertencias

- **Empieza siempre con `DEMO_MODE=true`** para verificar que todo funciona
- El trading con apalancamiento conlleva riesgo de pérdida total
- Ajusta `MAX_RISK_PER_TRADE` a valores conservadores (1–2%)
- Monitoriza los logs regularmente en Railway
- Este bot no garantiza beneficios

---

## 📁 Estructura del proyecto

```
bingx-bot/
├── src/
│   ├── bot.py              # Loop principal
│   ├── bingx_client.py     # API BingX
│   ├── signal_engine.py    # Signal Projection Explorer
│   ├── scanner.py          # Scanner de monedas
│   ├── risk_manager.py     # Gestión de riesgo
│   └── config.py           # Configuración
├── logs/                   # Logs automáticos (Railway los muestra en panel)
├── .env.example            # Template de variables
├── requirements.txt
├── Dockerfile
├── railway.toml
└── README.md
```
