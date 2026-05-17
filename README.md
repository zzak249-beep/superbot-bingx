# 🎯 Sniper Bot V35: Golden Equilibrium

Bot de trading automatizado para BingX Perpetual Futures.  
Implementa exactamente la estrategia V35 de TradingView (Pine Script), con motor de aprendizaje adaptativo y notificaciones en Telegram.

---

## 📁 Estructura

```
sniper-bot-v35/
├── main.py              # Orquestador principal
├── config.py            # Variables de entorno centralizadas
├── bingx_client.py      # Cliente BingX REST API (firmado HMAC-SHA256)
├── strategy.py          # Estrategia V35: EMA 7/17/21 + Pivot + Vol + ADX
├── scanner.py           # Top 20 monedas del día por volumen/momentum
├── telegram_notifier.py # Notificaciones HTML enriquecidas
├── risk_manager.py      # Tamaño de posición y control de exposición
├── learning_engine.py   # Motor adaptativo: registra, analiza, ajusta
├── requirements.txt
├── railway.toml
├── .env.example
└── data/                # Auto-creado: trades.json, bot.log
```

---

## ⚙️ Configuración

### 1. Clonar y preparar `.env`

```bash
git clone https://github.com/TU_USUARIO/sniper-bot-v35.git
cd sniper-bot-v35
cp .env.example .env
nano .env   # rellena tus claves
```

### 2. Variables de entorno obligatorias

| Variable | Descripción | Ejemplo |
|---|---|---|
| `BINGX_API_KEY` | Clave API de BingX | `abc123...` |
| `BINGX_SECRET_KEY` | Secret de BingX | `xyz789...` |
| `TELEGRAM_TOKEN` | Token del bot de Telegram | `1234567:ABC...` |
| `TELEGRAM_CHAT_ID` | Tu Chat ID de Telegram | `987654321` |

### 3. Variables opcionales (ya tienen valores por defecto correctos)

| Variable | Default | Descripción |
|---|---|---|
| `VOL_MULT` | `1.5` | Multiplicador de volumen (sync con Pine) |
| `ADX_MIN` | `20` | Umbral ADX mínimo (sync con Pine) |
| `LEVERAGE` | `5` | Apalancamiento |
| `CAPITAL_PCT` | `2` | % de balance por operación |
| `MAX_TRADES` | `3` | Máximo de trades simultáneos |
| `DRY_RUN` | `false` | `true` para simular sin operar real |

---

## 🔑 Cómo obtener las claves

### BingX API
1. Entra a BingX → Tu perfil → **API Management**
2. Crear nueva API Key
3. Permisos necesarios: **Trade** + **Read**
4. Copia `API Key` y `Secret Key` al `.env`

### Telegram Bot
1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot`
2. Copia el **token** al `.env`
3. Envía cualquier mensaje a tu bot, luego visita:  
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Copia el `chat.id` al `.env`

---

## 🚀 Despliegue en Railway

### Opción A: Desde GitHub (recomendado)

1. Sube el código a GitHub
2. Ve a [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
3. Selecciona tu repositorio
4. Ve a **Variables** y añade todas las del `.env`
5. Railway detecta `railway.toml` y despliega automáticamente

### Opción B: Railway CLI

```bash
npm install -g @railway/cli
railway login
railway init
railway up
railway variables set BINGX_API_KEY=... BINGX_SECRET_KEY=... TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=...
```

---

## 🧪 Prueba local

```bash
# Instalar dependencias
pip install -r requirements.txt

# Probar en modo seco (sin operar)
DRY_RUN=true python main.py
```

---

## 📊 Estrategia V35 — Cómo funciona

Implementación exacta del Pine Script `//@version=6`:

| Componente | Valor | Descripción |
|---|---|---|
| EMA Fast | 7 | Señal de momentum |
| EMA Mid | 17 | Confirmación cruce |
| EMA Slow | 21 | Take Profit institucional |
| Pivot Len | 5 | Profundidad del ZigZag |
| Vol Mult | 1.5x | Volumen institucional |
| ADX Min | 20 | Tendencia mínima |
| SL | valley − ATR×0.5 | Stop bajo estructura |
| TP | EMA 21 | Objetivo institucional |
| Time-Stop | 15 velas = 45 min | Evita quedarse atascado |

**Señal LONG:** crossover(EMA7, EMA17) AND low < valley AND vol > 1.5x AND ADX > 20  
**Señal SHORT:** crossunder(EMA7, EMA17) AND high > peak AND vol > 1.5x AND ADX > 20

---

## 🧠 Motor de aprendizaje

El bot aprende automáticamente de sus resultados:

- **Registra** cada trade con todos sus indicadores en `data/trades.json`
- **Analiza** cada 5 trades: winrate, ADX medio en pérdidas, patrones
- **Ajusta** automáticamente:
  - Si WR < 40% → sube umbral ADX +2 y fuerza mínima +5%
  - Si WR > 65% → relaja filtros levemente
  - Si pérdidas correlacionan con ADX bajo → sube umbral
- **Bloquea** símbolos con WR < 30% tras 5+ trades
- **Notifica** en Telegram cada ajuste realizado

---

## 📱 Notificaciones Telegram

| Evento | Trigger |
|---|---|
| 🚀 Startup | Al iniciar el bot |
| ⚡ Trade abierto | Cada nueva posición |
| ✅/❌ Trade cerrado | TP, SL o Time-Stop |
| 🔍 Escaneo | Cada hora (top 20 pares) |
| 🏆 Reporte diario | 00:01 UTC |
| 🧠 Ajuste IA | Cada cambio de parámetros |
| 🚫 Blacklist | Símbolo bloqueado |
| ⚠️ Error | Cualquier excepción grave |

---

## ⚠️ Disclaimer

Este bot opera con **dinero real**. Usa `DRY_RUN=true` para probar.  
El trading de futuros con apalancamiento implica **riesgo de pérdida total**.  
Este software se proporciona sin garantías. Úsalo bajo tu propio riesgo.
