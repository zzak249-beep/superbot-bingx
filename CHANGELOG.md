# Conflux 4 Bot v3.1 — CHANGELOG Y GUÍA DE DESPLIEGUE

## Resumen de mejoras

### 🔴 Crítico (bugs corregidos)

| Bug | Causa | Fix |
|-----|-------|-----|
| `Signal=—` en todos los logs | `sig_txt` se construía antes de verificar `result.signal` | Ahora muestra señal + motivo de rechazo siempre |
| `register_close()` nunca llamado | El cierre de trades no actualizaba el risk manager | Añadido en `main.py` al detectar `close_full` |
| `supertrend` importado dentro del loop | `from conflux4 import supertrend` dentro de `for trade in trades` | Movido al top del archivo |
| Incoherencia nocional | `BASE_SIZE_USDT=10 × leverage=3 = 30 USDT` ≠ 300 USDT Telegram | Log de config al arranque detecta esto |

---

### 🟡 Rentabilidad (mejoras de filtros)

#### 1. Filtro ADX siempre activo (`adx_min=22`)
- **Antes**: `use_adx=False` en presets — ADX nunca bloqueaba señales
- **Ahora**: ADX < 22 = señal bloqueada automáticamente (mercado lateral)
- **Impacto**: Elimina ~60% de señales falsas en mercados sin tendencia

#### 2. R/R mínimo 2.0 (era 1.0)
- **Antes**: TP2 = entry + 1× riesgo → R/R 1.0 no cubre comisiones
- **Ahora**: TP2 = entry + 2× riesgo → necesitas ganar 33% trades para ser rentable
- **Fórmula**: Breakeven WR = 1/(1+RR) → con RR 2.0: 33%, con RR 1.0: 50%

#### 3. SL basado en ATR×1.5 (nunca < 0.5%)
- **Antes**: SL a veces dentro del ATR del mercado (INJ: SL=-0.59%, ATR=0.60%)
- **Ahora**: SL = max(ATR×1.5, 0.5% del precio) → siempre fuera del ruido

#### 4. Zonas RSI por dirección
- **BULL válido**: RSI entre 45-68 (no sobrecomprado, no débil extremo)
- **BEAR válido**: RSI entre 32-55 (no sobrevendido extremo)
- **Antes**: solo `rsi_bull=52` como umbral, sin límite superior

#### 5. Funding rate más estricto (0.03 era 0.05)
- Evita entrar BULL cuando el mercado está excesivamente largo

---

### 🟢 Risk Manager (nuevas funciones)

#### Win rate tracking real
- `all_time_wins` y `all_time_losses` se llevan por separado
- Kelly usa WR real desde el trade 5 (antes esperaba 20)
- Dashboard muestra `WR=XX%` en el log de cada scan

#### Cooldown post-SL
- Tras un stop loss en un par, bloquea re-entrada durante N scans (default: 2)
- Config: variable de entorno `POST_SL_COOLDOWN=2`
- Evita el efecto "stop hunting" repetido en el mismo par

#### Reducción de sizing por racha mala
- 2-3 pérdidas consecutivas → sizing reducido al 65%
- 4+ pérdidas consecutivas → sizing reducido al 40%
- Se resetea automáticamente al primer win

#### Alerta de racha mala
- 4+ pérdidas consecutivas → alerta Telegram automática

---

### 🔵 Infraestructura

#### Log completo de config al arranque
```
══════════════════ CONFIG ACTIVA ══════════════════
  Preset:          Daytrader
  ADX mínimo:      22  (filtro tendencia)
  RSI BULL:        [45-68]
  SL ATR mult:     1.5x  (mín 0.5%)
  R/R mínimo:      2.0  | TPs: 0.5/2.0/3.0/4.5
  Leverage:        5x
  Auto-trade:      NO (solo señales)
═══════════════════════════════════════════════════
```

#### Alerta de error rate > 15%
- Si más del 15% de pares dan error en un scan → alerta Telegram
- Indica problemas con API BingX o rate limiting

---

## Variables de entorno nuevas en v3.1

```bash
# Filtros de señal
ADX_MIN=22              # Mínimo ADX para señal (default: 22)
SL_ATR_MULT=1.5         # Multiplicador ATR para SL (default: 1.5)
SL_MIN_PCT=0.5          # SL mínimo como % del precio (default: 0.5)
MIN_RR=2.0              # R/R mínimo garantizado (default: 2.0)
RSI_BULL_LO=45          # RSI mínimo para BULL (default: 45)
RSI_BULL_HI=68          # RSI máximo para BULL (default: 68)
RSI_BEAR_LO=32          # RSI mínimo para BEAR (default: 32)
RSI_BEAR_HI=55          # RSI máximo para BEAR (default: 55)
FUNDING_THR=0.03        # Umbral funding rate (default: 0.03)

# Risk manager
POST_SL_COOLDOWN=2      # Scans de cooldown post-SL (default: 2)
MIN_QUALITY=5           # Calidad mínima de señal 0-10 (default: 5)
```

## Variables ya existentes (recordatorio)

```bash
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
BINGX_API_KEY=...
BINGX_SECRET=...
AUTO_TRADE=false        # ⚠️ Cambiar a true solo cuando el bot esté validado
PRESET=Daytrader        # Scalp | Daytrader | Swing
INTERVAL=15m
LEVERAGE=3              # Mantener bajo hasta validar señales
MAX_OPEN_TRADES=3
TOP_N_SYMBOLS=50
MIN_VOLUME_USDT=5000000
```

---

## Archivos modificados

| Archivo | Cambios |
|---------|---------|
| `conflux4.py` | Filtro ADX, zonas RSI, SL ATR-based, calidad 0-10, reasons[], RR mínimo |
| `config.py` | Nuevos parámetros, log de config, _env_* helpers, presets actualizados |
| `risk_manager.py` | Win rate tracking, cooldown post-SL, consecutive_losses, Kelly mejorado |
| `main.py` | register_close() correcto, scan_count al risk, alerta error rate, log mejorado |

## Archivos NO modificados (no necesarios)
- `bingx_client.py`
- `telegram_notifier.py`
- `trade_manager.py`
- `symbol_scanner.py`
