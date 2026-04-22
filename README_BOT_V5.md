# 🚀 BingX Trading Bot V5 - Guía Completa

## ⚡ Características del Bot V5

Este es un bot **MÁS AVANZADO** que incluye:

### Análisis Técnico Avanzado:
- ✅ **Order Book Analysis** - Detecta muros, CVD, absorption signals
- ✅ **Bollinger Bands Squeeze** - Identifica compresión → explosión
- ✅ **Multi-Timeframe (MTF)** - Confirma señales en timeframe superior
- ✅ **Sistema de Scoring 0-100** - Combina múltiples indicadores

### Trading Inteligente:
- ✅ **Take Profit Parcial** - TP1 (40%) → TP2 (35%) → Trailing (25%)
- ✅ **Circuit Breakers** - Pausa automática tras pérdidas consecutivas
- ✅ **Performance Tracking** - Win rate, profit factor, stats por señal

### Notificaciones:
- ✅ **Telegram Alerts** - Notificaciones ricas como el bot de referencia
- ✅ **Dashboard HTML** - Estado en tiempo real con métricas avanzadas

---

## 📁 Archivos del Bot V5

Necesitas estos archivos en tu proyecto de Railway:

### Archivos Principales:
1. **bot.py** - El que te compartí en el documento (bot v5)
2. **order_book.py** - Análisis de order book (nuevo)
3. **signal_engine_v5.py** - Motor de señales con scoring
4. **trade_manager_v5.py** - Gestor de trades con TP parcial
5. **market_scanner_v5.py** - Scanner actualizado
6. **bingx_client_v5.py** - Cliente con Telegram

### Archivos que NO cambian:
7. **reward_scheme.py** - (usa el original)
8. **rl_trainer.py** - (usa el original, opcional)
9. **Dockerfile** - (usa el original)
10. **railway.toml** - (usa el original)
11. **requirements.txt** - (usa el original)

---

## 🔧 Setup Paso a Paso

### 1️⃣ Preparar Archivos

**En tu proyecto local:**

```bash
# Renombrar archivos
mv signal_engine_v5.py signal_engine.py
mv trade_manager_v5.py trade_manager.py
mv market_scanner_v5.py market_scanner.py
mv bingx_client_v5.py bingx_client.py

# El bot.py ya está con el nombre correcto
# order_book.py ya está con el nombre correcto
```

**Estructura final:**
```
tu-proyecto/
├── bot.py                 (v5 - nuevo)
├── order_book.py          (nuevo)
├── signal_engine.py       (v5 - actualizado)
├── trade_manager.py       (v5 - actualizado)
├── market_scanner.py      (v5 - actualizado)
├── bingx_client.py        (v5 - actualizado)
├── reward_scheme.py       (original - sin cambios)
├── rl_trainer.py          (original - sin cambios)
├── Dockerfile             (original - sin cambios)
├── railway.toml           (original - sin cambios)
└── requirements.txt       (original - sin cambios)
```

---

### 2️⃣ Variables de Entorno en Railway

**MÍNIMO requerido (3 variables):**

```
BINGX_API_KEY = tu_api_key_real
BINGX_API_SECRET = tu_api_secret_real
DRY_RUN = true
```

**Telegram (opcional pero recomendado):**

Para recibir alertas en tu móvil:

1. Abre Telegram
2. Busca @BotFather
3. Envía `/newbot`
4. Sigue las instrucciones
5. Copia el **token** que te da
6. Envía un mensaje a tu bot (cualquier cosa)
7. Ve a: `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
8. Copia el **chat_id** (número que aparece)

```
TELEGRAM_BOT_TOKEN = 123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID = 123456789
```

**Configuración Trading (opcional):**

```
# Señales
MIN_SIGNAL_SCORE=40      # 40=muchas, 60=calidad, 70=solo las mejores

# Risk
LEVERAGE=5
RISK_PCT=0.01
MAX_OPEN_TRADES=3
MIN_RR=2.0

# TP/SL
TP1_RATIO=2.5
TP1_PCT=40
TP2_RATIO=4.0
TP2_PCT=35
SL_MULT=1.0

# Circuit Breakers
DAILY_LOSS_CAP_PCT=5.0
MAX_LOSING_STREAK=3
```

---

### 3️⃣ Deploy a Railway

```bash
git add .
git commit -m "Update to bot v5 with order book analysis"
git push
```

Railway automáticamente:
1. Detecta cambios
2. Construye el container
3. Despliega el bot

---

### 4️⃣ Verificar que Funciona

**En Railway → Deploy Logs, busca:**

```
✅ Bot v5 iniciado — DRY_RUN
✅ Health server activo en 0.0.0.0:8080
━━ Ciclo #1 ━━
   XX símbolos calientes
```

**Dashboard:**
- Abre: `https://tu-app.railway.app:8080`
- Deberías ver:
  - Señales activas con scores
  - Order Book analysis
  - Performance stats

**Telegram (si configuraste):**
- Recibirás mensaje: "⚡ Bot v5 iniciado — DRY_RUN"
- Cuando detecte señales, recibirás alertas como:

```
🟢 ALTO BTC-USDT — 72%
$ $45123.45 | 24h: +0.45%
BB:0.8% Z:-0.28 CVD:80% OI:? MTF:2/3
Señales:
  🎯 BB Squeeze 0.8%
  🔥 Vol acelerado 14.2x
  🌊 CVD 80% bull
  ✅ MTF 2/3
⏰ 19:55 UTC
```

---

## 📊 Diferencias vs. Bot Básico

### Bot Básico (v1):
- EMA crossover simple
- RSI basic
- Señales binarias (sí/no)
- TP/SL fijos

### Bot V5 (avanzado):
- ✅ Order Book depth analysis
- ✅ BB Squeeze detection
- ✅ Multi-timeframe confirmation
- ✅ Scoring system 0-100
- ✅ TP parcial (TP1, TP2, trailing)
- ✅ Circuit breakers
- ✅ Performance tracking
- ✅ Telegram alerts rico

---

## ⚙️ Ajustar Parámetros

### Más Señales vs. Mejor Calidad

**Quieres MÁS señales:**
```
MIN_SIGNAL_SCORE=30      # Más señales, menor calidad
MA_FAST=5                # Crossovers más frecuentes
```

**Quieres MEJOR calidad:**
```
MIN_SIGNAL_SCORE=60      # Menos señales, mejor calidad
MIN_RR=3.0               # Solo R:R > 3:1
```

### Más Agresivo vs. Más Conservador

**Más agresivo:**
```
RISK_PCT=0.02            # 2% por trade (más riesgo)
LEVERAGE=10              # 10x leverage
MAX_OPEN_TRADES=5        # Hasta 5 trades simultáneos
```

**Más conservador:**
```
RISK_PCT=0.005           # 0.5% por trade (menos riesgo)
LEVERAGE=3               # 3x leverage
MAX_OPEN_TRADES=2        # Máximo 2 trades
```

---

## 🛡️ Circuit Breakers Explicados

El bot se PAUSA automáticamente si:

### 1. Racha Perdedora:
```
MAX_LOSING_STREAK=3
```
- Si pierdes 3 trades consecutivos → Pausa 4 horas
- Evita pérdidas en espiral
- Se reactiva automáticamente

### 2. Pérdida Diaria:
```
DAILY_LOSS_CAP_PCT=5.0
```
- Si pierdes 5% del balance en un día → Pausa hasta mañana
- Protección contra días catastróficos

**En el dashboard verás:**
```
⛔ CIRCUIT BREAKER: 3 pérdidas consecutivas
```

---

## 📱 Telegram Alerts Explicados

### Formato de las Alertas:

```
🟢 ALTO STORJ-USDT — 71%
$ $0.101300 | 24h: -0.30%
BB:0.8% Z:-0.28 CVD:80% OI:+0.0% MTF:2/3
Señales:
  🎯 BB Squeeze 0.8%
  🔥 Vol acelerado 14.2x
  🌊 CVD 80% bull
  ✅ MTF 2/3
⏰ 19:55 UTC
```

**Qué significa:**
- **🟢 ALTO** - Score 70+ (🟡 MEDIO 55-70, 🟠 BAJO <55)
- **STORJ-USDT** - Símbolo
- **71%** - Score total
- **BB:0.8%** - Ancho de Bollinger Bands (squeeze)
- **Z:-0.28** - Z-score del CVD
- **CVD:80%** - Cumulative Volume Delta (presión compradora)
- **MTF:2/3** - Multi-timeframe (2 de 3 timeframes confirman)

---

## ⚠️ ADVERTENCIAS CRÍTICAS

### 1. Más Complejo ≠ Más Rentable

Este bot tiene más indicadores, pero:
- ❌ NO garantiza más ganancias
- ❌ Puede generar señales conflictivas
- ❌ Más difícil de entender y ajustar

### 2. Order Book Data = Más API Calls

- El bot consulta order book constantemente
- Consume más rate limits de BingX
- Puede ser más lento que el bot básico

### 3. False Positives en Order Book

- Muros pueden ser spoofing (órdenes falsas)
- Ballenas pueden mover el order book artificialmente
- CVD puede dar señales falsas en mercados manipulados

### 4. Sobreajuste (Overfitting)

Con tantos parámetros:
- Fácil optimizar para el pasado
- Difícil saber si funcionará en el futuro
- Riesgo de sobreoptimización

---

## 🎯 Uso Recomendado

### Paso 1: DRY_RUN Indefinido

Mantén **DRY_RUN=true** y monitorea:

**Durante 2-4 semanas, pregúntate:**
- ¿Las señales tienen sentido?
- ¿El timing es bueno?
- ¿Los TP1/TP2 se cumplen?
- ¿El circuit breaker actúa correctamente?

### Paso 2: Ajustar MIN_SIGNAL_SCORE

**Si ves:**
- Muchas señales malas → Sube a 60
- Pocas señales → Baja a 35-40
- Señales conflictivas → Revisa los logs

### Paso 3: Backtesting Manual

**En el dashboard:**
- Revisa "Performance por Tipo de Señal"
- ¿Qué tipos de señal funcionan mejor?
- Ajusta parámetros según lo que funciona

### Paso 4: (Opcional) Trading Real

**SOLO si después de 1+ mes:**
- Win rate > 55% consistente
- Profit factor > 1.5
- Circuit breakers funcionando
- Entiendes todas las señales

**Empieza con:**
- Balance: $50-100 máximo
- RISK_PCT: 0.005 (0.5%)
- MAX_OPEN_TRADES: 2

---

## 🔍 Troubleshooting

### "Order Book: Calculando..."

**Causa:** Order book analyzer está cargando
**Solución:** Espera 1-2 ciclos, se llenará automáticamente

### Muchas Señales con Score Bajo

**Causa:** MIN_SIGNAL_SCORE muy bajo
**Solución:** Sube a 50-60

### Pocas/Ninguna Señal

**Causa:** MIN_SIGNAL_SCORE muy alto o mercado lateral
**Solución:** 
- Baja MIN_SIGNAL_SCORE a 35-40
- Espera mercado más volátil
- Reduce MIN_RR

### Circuit Breaker Activo Constantemente

**Causa:** Estrategia perdiendo dinero
**Solución:**
- Vuelve a DRY_RUN=true
- Revisa configuración
- Ajusta MIN_SIGNAL_SCORE

### Telegram No Envía Alertas

**Causa:** Token o Chat ID incorrectos
**Solución:**
1. Verifica TELEGRAM_BOT_TOKEN
2. Verifica TELEGRAM_CHAT_ID (debe ser solo números)
3. Envía mensaje al bot primero
4. Usa `/getUpdates` para obtener chat_id

---

## 📝 Logs Importantes

### Señal Detectada:
```
✅ SEÑAL LONG en BTC-USDT  
   score=72.0  mean=0.45%  median=0.38%  rr=3.2
```

### Trade Abierto:
```
Abriendo LONG BTC-USDT  qty=0.001  
entry=45000  TP1=45112  TP2=45180  SL=44775
✅ Trade abierto BTC-USDT #123456
```

### TP Parcial:
```
Closing 0.0004 of BTC-USDT (TP1)
```

### Circuit Breaker:
```
⚠️ Circuit breaker: 3 pérdidas. Paused hasta 2026-04-23 02:00
```

---

## 💡 Conclusión

El Bot V5 es **MUCHO más complejo** que el básico, pero:

### ✅ Pros:
- Análisis más profundo del mercado
- Múltiples confirmaciones antes de señal
- Circuit breakers para protección
- Performance tracking detallado

### ❌ Contras:
- Más difícil de configurar
- Más fácil de sobreajustar
- Más lento (más API calls)
- NO garantiza mejores resultados

**Recomendación Final:**
1. Usa SIEMPRE en DRY_RUN primero
2. Monitorea durante semanas
3. Ajusta MIN_SIGNAL_SCORE según tus necesidades
4. NO actives trading real sin comprender cada señal
5. Recuerda: más indicadores ≠ más ganancias

---

**¿Listo?** Sigue los pasos de Setup y verifica que todo funciona en DRY_RUN.
