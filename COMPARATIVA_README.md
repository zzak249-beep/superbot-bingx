# 🤖 COMPARATIVA: SATY v14 vs SUPERTREND BOT v1

## ¿Qué es esto?
Dos bots corriendo en paralelo en Railway, cada uno con su propia estrategia,
para ver cuál es más rentable en condiciones reales de mercado.

| Bot | Estrategia | Complejidad | Señales |
|-----|-----------|-------------|---------|
| **SATY v14** | 3 módulos (BB+SMC+Squeeze) + Brain adaptativo | Alta | Pocas, muy filtradas |
| **Supertrend Bot v1** | 3×Supertrend + EMA200 + RSI + Vol | Simple | Más frecuentes |

---

## 🚀 CÓMO DESPLEGARLO EN RAILWAY

### Paso 1 — Subir los archivos a GitHub
Añade estos archivos a tu repo `superbot-bingx`:
```
supertrend_bot.py   ← la estrategia nueva
```

### Paso 2 — Crear un SEGUNDO servicio en Railway
1. Ve a railway.app → tu proyecto `empathetic-ambition`
2. Haz clic en **"+ New Service"**
3. Selecciona **"GitHub Repo"** → el mismo repo `superbot-bingx`
4. En **Settings → Start Command** escribe:
   ```
   python supertrend_bot.py
   ```

### Paso 3 — Variables de entorno del segundo servicio
En el nuevo servicio → Settings → Variables, añade:

```env
# Mismas claves BingX (misma cuenta)
ST_BINGX_API_KEY=tu_api_key
ST_BINGX_API_SECRET=tu_api_secret

# Telegram — puedes usar el MISMO bot y chat
# Los mensajes dirán "ST BOT" para diferenciarlo de SATY
ST_TELEGRAM_BOT_TOKEN=tu_bot_token
ST_TELEGRAM_CHAT_ID=tu_chat_id

# Config del bot
ST_FIXED_USDT=8
ST_MAX_OPEN_TRADES=8
ST_LEVERAGE=10
ST_TIMEFRAME=5m
ST_HTF=1h
ST_POLL_SECONDS=60
ST_COOLDOWN_MIN=30
ST_MIN_VOLUME_USDT=50000
ST_TOP_N_SYMBOLS=150
ST_MAX_DRAWDOWN=15
ST_DAILY_LOSS_LIMIT=8
```

### Paso 4 — Verificar que ambos corren en paralelo
En Railway deberías ver 2 servicios activos:
- `worker` → SATY v14 (saty_v13.py o saty_v14.py)
- `worker-2` → Supertrend Bot v1

---

## 📊 CÓMO COMPARAR RESULTADOS

Ambos bots envían mensajes a Telegram con su nombre:
- **SATY ELITE v14** → mensajes normales de SATY
- **ST BOT** → mensajes del Supertrend Bot

Cada bot genera su propio CSV:
- `saty_v14_trades.csv`
- `supertrend_bot_trades.csv`

Métricas a comparar después de 2-4 semanas:
1. **Win Rate** (% de trades ganadores)
2. **Profit Factor** (ganancias / pérdidas)
3. **Total PnL** ($)
4. **Nº de señales** (cuántos trades hace cada uno)
5. **Max Drawdown** (peor racha de pérdidas)

---

## 🧠 DIFERENCIAS CLAVE DE ESTRATEGIA

### SATY v14 — Multi-módulo complejo
```
Señal válida requiere:
✅ ≥2 de 3 módulos alineados (BB Hunter + Confirmación PRO + SMC)
✅ Score ≥ 5 (ajustable por Brain)
✅ HTF confirmation (15m + 1h)
✅ Funding rate OK
✅ Fear & Greed OK
✅ BTC filter
→ Resultado: pocas señales, muy filtradas
```

### Supertrend Bot v1 — Simple y directo
```
Señal válida requiere:
✅ ≥2 de 3 Supertrends alineados
✅ Precio > o < EMA200
✅ RSI en zona válida (40-65 long / 35-60 short)
✅ Volumen > 1.5× media
✅ HTF Supertrend confirma
→ Resultado: más señales, lógica clara
```

---

## ⚠️ IMPORTANTE — Gestión de riesgo
- Ambos bots usan los mismos límites: 15% drawdown máximo, 8% límite diario
- Si los dos abren trades a la vez, el capital total en riesgo se duplica
- Considera bajar `ST_FIXED_USDT` a 5 mientras haces la comparativa
- Puedes usar la misma cuenta BingX (los trades son independientes)

---

## 🏆 VEREDICTO ESPERADO (según backtests históricos)

| Condición de mercado | Ganador esperado |
|---------------------|-----------------|
| Mercado en tendencia fuerte | 🏅 Supertrend Bot (sigue tendencias mejor) |
| Mercado lateral con volumen | 🏅 SATY v14 (SMC + BB detectan rebotes) |
| Mercado muy volátil | 🏅 SATY v14 (más filtros = menos falsas) |
| Mercado alcista sostenido | Empate |

¡Deja correr ambos al menos 3-4 semanas para tener datos significativos!
