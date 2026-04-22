# ⚡ SOLUCIÓN RÁPIDA - Bot V5 Funcional

## 🎯 El Problema

Tu bot crasheaba porque faltaba `order_book.py` - el archivo más importante del bot v5.

El bot v5 es **COMPLETAMENTE DIFERENTE** del código anterior. Tiene:
- Order Book Analysis
- BB Squeeze detection  
- CVD (Cumulative Volume Delta)
- Multi-timeframe
- TP parcial
- Telegram alerts

## ✅ La Solución (3 Pasos)

### 1️⃣ Descargar Archivos

He creado **7 archivos corregidos** (arriba ↑):

**Archivos NUEVOS que necesitas:**
1. `order_book.py` - ⭐ EL MÁS IMPORTANTE (faltaba esto)
2. `signal_engine_v5.py` - Motor de señales actualizado
3. `trade_manager_v5.py` - Gestor con TP parcial
4. `market_scanner_v5.py` - Scanner actualizado
5. `bingx_client_v5.py` - Cliente con Telegram
6. `ENV_VARIABLES_V5.txt` - Variables correctas
7. `README_BOT_V5.md` - Guía completa

**Archivos que YA tienes (no cambies):**
- `bot.py` - Ya lo tienes (el documento que compartiste)
- `reward_scheme.py` - Usa el original
- `rl_trainer.py` - Usa el original
- `Dockerfile` - Usa el original
- `railway.toml` - Usa el original
- `requirements.txt` - Usa el original

---

### 2️⃣ Renombrar Archivos

En tu proyecto local:

```bash
# Renombrar los archivos v5 (quitar el _v5)
mv signal_engine_v5.py signal_engine.py
mv trade_manager_v5.py trade_manager.py  
mv market_scanner_v5.py market_scanner.py
mv bingx_client_v5.py bingx_client.py

# order_book.py ya tiene el nombre correcto
# bot.py ya lo tienes con el nombre correcto
```

---

### 3️⃣ Variables de Entorno en Railway

**SOLO necesitas estas 3 (mínimo):**

```
BINGX_API_KEY = tu_api_key
BINGX_API_SECRET = tu_secret
DRY_RUN = true
```

**Telegram (opcional):**
```
TELEGRAM_BOT_TOKEN = (obtén de @BotFather)
TELEGRAM_CHAT_ID = (obtén de /getUpdates)
```

**Deploy:**
```bash
git add .
git commit -m "Add missing order_book.py"
git push
```

---

## 🎯 Cómo Saber que Funciona

### En Railway Logs:

**ANTES (crasheaba):**
```
❌ 'OrderBookSnapshot' object has no attribute 'bid_delta_pct'
❌ 'TradeManager' object has no attribute 'get_performance'
```

**DESPUÉS (funcionando):**
```
✅ Bot v5 iniciado — DRY_RUN
✅ Health server activo en 0.0.0.0:8080
━━ Ciclo #1 ━━
   30 símbolos calientes
```

### Dashboard (http://tu-app:8080):

Verás:
- 📊 Señales con scores 0-100
- 🌊 Order Book analysis (muros, CVD, imbalance)
- 📈 Performance stats
- ✅ Trades simulados (en DRY_RUN)

### Telegram (si configuraste):

Recibirás:
```
⚡ Bot v5 iniciado — DRY_RUN
Scan: 60s
```

Y cuando detecte señales:
```
🟢 ALTO BTC-USDT — 72%
$ $45123 | 24h: +0.45%
BB:0.8% Z:-0.28 CVD:80% MTF:2/3
Señales:
  🎯 BB Squeeze 0.8%
  🔥 Vol acelerado 14.2x
⏰ 19:55 UTC
```

---

## 🔧 Ajuste de Señales

### Muchas Señales (baja calidad):

Si ves 50+ señales por hora:
```
MIN_SIGNAL_SCORE=60      # Sube a 60
```

### Pocas Señales:

Si NO detecta nada:
```
MIN_SIGNAL_SCORE=30      # Baja a 30-35
```

**Balance recomendado:**
```
MIN_SIGNAL_SCORE=40      # 3-10 señales por hora
```

---

## ⚠️ RECORDATORIO FINAL

Este bot v5:
- ✅ Es más complejo que el básico
- ✅ Analiza order book en tiempo real
- ✅ Tiene scoring system avanzado
- ❌ NO garantiza ganancias
- ❌ NO es una máquina de hacer dinero

**Siempre:**
1. Empieza con DRY_RUN=true
2. Monitorea durante semanas
3. Ajusta MIN_SIGNAL_SCORE según necesites
4. NO actives trading real sin probar extensamente

---

## 📚 Documentación Completa

Lee estos archivos que creé:

1. **README_BOT_V5.md** - Guía completa con todo explicado
2. **ENV_VARIABLES_V5.txt** - Todas las variables disponibles

---

## 🆘 Si Algo Falla

### Bot sigue crasheando:

1. Verifica que descargaste **order_book.py**
2. Verifica que renombraste los archivos (quitar _v5)
3. Revisa los logs de Railway para el error específico

### No detecta señales:

1. Baja MIN_SIGNAL_SCORE a 30
2. Espera 2-3 ciclos (2-5 minutos)
3. Verifica en dashboard que símbolos están siendo escaneados

### Telegram no funciona:

1. Verifica BOT_TOKEN y CHAT_ID
2. Envía mensaje al bot primero
3. Usa /getUpdates para obtener el chat_id

---

## ✅ Checklist Final

Antes de push:

- [ ] Descargaste los 7 archivos nuevos
- [ ] Renombraste archivos (quitaste _v5)
- [ ] Añadiste BINGX_API_KEY y SECRET
- [ ] Configuraste DRY_RUN=true
- [ ] (Opcional) Configuraste Telegram
- [ ] Hiciste git push

**Después del deploy:**

- [ ] Logs muestran "✅ Bot v5 iniciado"
- [ ] Dashboard carga en :8080
- [ ] (Si Telegram) Recibiste mensaje de inicio

---

**Tiempo estimado:** 15 minutos si sigues los pasos exactamente.

**¿Problemas?** Lee README_BOT_V5.md (tiene troubleshooting detallado).
