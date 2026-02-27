# 🚀 SATY ELITE v11 — Real Money Bot

Bot de trading algorítmico para **BingX Perpetual Futures** con 12 trades simultáneos, 24/7, universo completo de pares USDT de bajo volumen.

---

## ⚡ Deploy en Railway (5 minutos)

### PASO 1 — Subir a GitHub

```bash
# En tu máquina local, crea el repo:
git init
git add .
git commit -m "SATY ELITE v11 — initial deploy"

# Crea un repo PRIVADO en github.com/new (¡PRIVADO! tiene tus claves)
git remote add origin https://github.com/TU_USUARIO/saty-elite-v11.git
git branch -M main
git push -u origin main
```

### PASO 2 — Crear proyecto en Railway

1. Ve a **[railway.app](https://railway.app)** → **New Project**
2. Selecciona **Deploy from GitHub repo**
3. Conecta tu cuenta GitHub y elige el repo `saty-elite-v11`
4. Railway detectará automáticamente el `Procfile` y usará el worker

### PASO 3 — Variables de entorno en Railway

En tu proyecto Railway → **Variables** → añade estas **obligatorias**:

| Variable | Valor | Descripción |
|---|---|---|
| `BINGX_API_KEY` | `tu_api_key` | API Key de BingX |
| `BINGX_API_SECRET` | `tu_api_secret` | API Secret de BingX |
| `TELEGRAM_BOT_TOKEN` | `123456:ABC...` | Token del bot de Telegram |
| `TELEGRAM_CHAT_ID` | `-100123456789` | Chat ID donde recibir alertas |

Variables **opcionales** (ya tienen defaults):

| Variable | Default | Descripción |
|---|---|---|
| `FIXED_USDT` | `8` | USDT por trade |
| `MAX_OPEN_TRADES` | `12` | Máximo trades simultáneos |
| `MIN_SCORE` | `4` | Score mínimo de 12 para entrar |
| `MAX_DRAWDOWN` | `15` | % drawdown para circuit breaker |
| `DAILY_LOSS_LIMIT` | `8` | % pérdida diaria máxima |
| `MIN_VOLUME_USDT` | `100000` | Volumen mínimo 24h (100K = incluye altcoins) |
| `TOP_N_SYMBOLS` | `300` | Pares a escanear |
| `POLL_SECONDS` | `60` | Segundos entre ciclos |
| `BTC_FILTER` | `true` | Filtro macro BTC |
| `COOLDOWN_MIN` | `20` | Minutos pausa tras cierre |
| `MAX_SPREAD_PCT` | `1.0` | Spread máximo aceptado % |
| `BLACKLIST` | `` | Pares excluidos (ej: `BTC/USDT:USDT,ETH/USDT:USDT`) |
| `TIMEFRAME` | `5m` | Timeframe principal |
| `HTF1` | `15m` | Timeframe medio |
| `HTF2` | `1h` | Timeframe macro |

### PASO 4 — Verificar que funciona

1. En Railway → **Deployments** → ver los logs en tiempo real
2. Deberías ver: `SATY ELITE v11 — REAL MONEY · 12 TRADES · 24/7`
3. En Telegram recibirás el mensaje de arranque con balance y universo

---

## 🔑 Cómo crear las claves de BingX

1. Accede a **BingX** → Perfil → **API Management**
2. Crear nueva API Key con permisos:
   - ✅ **Read** (leer balance y posiciones)
   - ✅ **Trade** (abrir/cerrar órdenes)
   - ❌ **Withdraw** (NO activar nunca)
3. Añade la IP de Railway en la whitelist (o deja vacío para cualquier IP)

---

## 🤖 Cómo crear el bot de Telegram

```
1. Abre Telegram → busca @BotFather
2. Escribe /newbot → sigue instrucciones → copia el TOKEN
3. Para obtener tu Chat ID:
   - Busca @userinfobot en Telegram
   - Escríbele cualquier mensaje
   - Te devolverá tu Chat ID
4. Si quieres alertas en un grupo: añade el bot al grupo y usa el Chat ID del grupo (empieza por -100...)
```

---

## 📊 Estrategia — Cómo funciona

### Universo de pares
- Escanea hasta **300 pares USDT perpetuos** de BingX
- Volumen mínimo **100K USDT/24h** → incluye altcoins pequeños y nuevos
- Detecta pares nuevos listados (< 30 días) y los prioriza

### Score de confluencia (0-12 puntos)
El bot evalúa 12 condiciones para LONG y 12 para SHORT:
1. Precio > EMA48 + EMA8 > EMA21 (tendencia)
2. Oscilador cruzando cero al alza
3. HTF 15m: bias alcista
4. HTF 1h: macro alcista
5. ADX > 16 + DI+ > DI- (momentum)
6. RSI en zona 42-78
7. Volumen buy > sell + spike (sin squeeze)
8. Vela alcista > EMA21
9. MACD bullish o cruce al alza
10. Stochastic RSI bullish
11. RSI extremo (rebote sobreventa 10-25)
12. Engulfing alcista o divergencia RSI

### Gestión del trade
- **Entrada**: orden market
- **TP1** (50% posición): 1.2× ATR → activa Break-Even en SL
- **TP2** (50% restante): 3.0× ATR
- **SL**: swing_low/high – 0.2×ATR (o 1.0×ATR mínimo)
- **Trailing stop dinámico** post-TP1: 3 fases (normal/tight/locked)
- **Agotamiento**: cierre si 3/6 señales de reversión en ganancia
- **Flip**: cierre si señal contraria fuerte (score+2)

### Protecciones
- Circuit breaker: para si drawdown ≥ 15%
- Límite diario: para si pérdida diaria ≥ 8%
- Cooldown 20 min por par tras cierre
- 1 posición por moneda base (no duplica BTC long + BTC short)
- Risk reducido al 50% tras 3 pérdidas consecutivas

---

## 📁 Estructura del proyecto

```
saty-elite-v11/
├── bot.py           ← Bot principal
├── requirements.txt ← Dependencias Python
├── railway.toml     ← Config Railway
├── Procfile         ← Comando de inicio
├── runtime.txt      ← Versión Python
└── README.md        ← Esta guía
```

---

## ⚠️ Advertencias importantes

- **DINERO REAL**: este bot opera con fondos reales. Empieza con poco capital.
- **Repo PRIVADO**: nunca subas tus claves API a un repo público.
- **Sin garantías**: el trading algorítmico conlleva riesgo de pérdida total del capital.
- **Monitoriza**: revisa los logs de Railway y las alertas de Telegram regularmente.
- Railway plan **Hobby ($5/mes)** es suficiente para el bot. El plan Free tiene limitaciones de horas.

---

## 🔄 Actualizaciones

Para actualizar el bot simplemente haz push al repo:

```bash
git add .
git commit -m "update"
git push
```

Railway redesplegará automáticamente.
