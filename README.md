# ⚡ BingX Signal Bot v7 — Professional Edition

Bot algorítmico de trading de futuros perpetuos en BingX.
Detecta coins explosivas antes de que exploten usando 11 factores simultáneos.

---

## 🔧 Fix crítico vs v6

| Problema v6 | Solución v7 |
|---|---|
| `TRADE_MGR - Balance 0` | Balance leído de cuenta **FUTUROS** (no spot) |
| MIN_SIGNAL_SCORE=40 | Subido a **60** coherente con MIN_SCORE |
| Leverage fijo 5x en todo | **Dinámico**: 5x BTC, 3x mid-caps, 2x micro-caps |
| SCAN_INTERVAL duplicado | Un solo `SCAN_INTERVAL_SEC=30` |
| CVD proxy roto | Recalculado desde velas reales |
| Sin quality gates | Mínimo 2 señales convergentes + MIN_RR=2.5 |

---

## 🚀 Despliegue en Railway

### 1. Verificar cuenta BingX

**Este es el paso más importante.** El error "Balance 0" ocurre porque
los fondos están en la wallet SPOT, no en FUTUROS.

```
BingX → Assets → Transfer → De: Spot → A: Perpetual → Cantidad → Confirmar
```

### 2. API Key con permisos correctos

```
BingX → API Management → Crear nueva key
✅ Read     (obligatorio)
✅ Trade    (obligatorio — sin esto no puede abrir órdenes)
❌ Withdraw (NO necesario — no dar este permiso)
```

### 3. Variables en Railway

Sube estas variables (sin las comillas de texto):
```
BINGX_API_KEY         → tu key
BINGX_API_SECRET      → tu secret
TELEGRAM_TOKEN        → token de tu bot Telegram
TELEGRAM_CHAT_ID      → tu chat ID
DRY_RUN               → true (empieza SIEMPRE en dry run)
AUTO_TRADING_ENABLED  → true
ACCOUNT_EQUITY        → 100
```

El resto de variables tienen defaults sensatos en el código.

### 4. Deploy

```bash
# Railway detecta requirements.txt automáticamente
# Start command:
python main.py
```

---

## 📊 Cómo valida si está funcionando

Tras el deploy, en los logs de Railway debes ver:

```
✅ Balance futuros USDT: $XX.XX        ← confirma que lee futuros
✅ Conexión BingX verificada
Bot v7 iniciado — DRY_RUN
Scanner: 35/150 símbolos calientes
  #1 BTC-USDT score=88 vol=3.2x squeeze=True
  #2 ETH-USDT score=81 ...
3 señales ≥60pts detectadas
[DRY] OPEN BTC-USDT LONG entry=...    ← si ves esto, funciona
```

Si ves `Balance 0` → fondos no están en futuros o API sin permisos.

---

## 📈 Protocolo de validación antes de dinero real

### Fase 1 — Paper trading (2-4 semanas)
```
DRY_RUN=true
```
- Registra cada señal del Telegram en una hoja de cálculo
- Anota: símbolo, precio entrada, SL, TP1, TP2, resultado real
- Objetivo: **≥50 trades** con datos reales

### Fase 2 — Análisis
Calcula con tus datos reales:
- Win Rate objetivo: ≥ 55%
- Profit Factor objetivo: ≥ 1.5
- Max Drawdown: ≤ 15%

Si cumples los 3 → procede a Fase 3.

### Fase 3 — Capital mínimo real ($50)
```
DRY_RUN=false
ACCOUNT_EQUITY=50
MAX_POSITION_SIZE=5
MAX_OPEN_TRADES=2
```
Revisa diariamente. Escala solo si mantienes los ratios.

---

## 🧠 Cómo detecta coins explosivas

El scanner evalúa **11 factores** en paralelo para cada coin:

1. **Volumen spike** — 3x+ sobre media 20 velas = acumulación institucional
2. **BB Squeeze** — Bandas comprimidas al <3.5% = explosión inminente
3. **EMA alignment** — EMA9 > EMA21 > EMA55 = trend fuerte
4. **Momentum 4h** — Cambio de +3%+ en 4 horas = inicio de pump
5. **RSI position** — Entre 45-65 = zona óptima de entrada
6. **CVD analysis** — >65% volumen de compradores = presión alcista
7. **MTF confluence** — Señal confirmada en 15m + 1h + 4h = alta prob.
8. **Open interest** — OI creciendo = dinero nuevo entrando
9. **Funding rate** — Negativo = shorts pagando = sesgo alcista
10. **BTC decoupling** — Sube cuando BTC lateral = fuerza propia
11. **Candle patterns** — Engulfing, hammer, vela fuerte

Score ≥ 60 para considerar entrada. Score ≥ 75 = señal premium.

---

## ⚠️ Disclaimer

Este software es para uso educativo y experimental.
Trading de criptomonedas conlleva riesgo de pérdida total del capital.
No inviertas dinero que no puedas permitirte perder.
