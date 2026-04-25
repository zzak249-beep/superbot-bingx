# 🤖 Conflux 4 Bot v2 — Edición Rentable

Bot de trading en futuros perpetuos de **BingX** basado en el indicador **Conflux 4**.  
La v2 añade todas las capas que convierten buenas señales en **operaciones rentables**.

---

## ¿Qué cambia respecto a la v1?

| Módulo | v1 | v2 |
|---|---|---|
| Señales | VWMA+EMA+RSI+ST+ADX | igual + MTF + funding filter + vol filter |
| Tamaño posición | fijo (USDT configurado) | **Kelly dinámico** ajustado por calidad |
| Stop loss | solo nivel fijo | **trailing ST + breakeven automático** |
| Salidas | solo SL o TP total | **25% parciales en TP1/2/3/4** |
| Riesgo diario | ninguno | **circuit breaker** (para el bot al límite) |
| Drawdown | ninguno | **para automáticamente** al límite configurado |
| Correlaciones | ninguna | **bloquea pares correlacionados** mismo lado |
| Horario | 24/7 sin filtro | **evita horas de baja liquidez** |
| Dashboard | solo señales | **P&L, winrate, equity curve** en Telegram |
| Persistencia | sin memoria | **equity.json + trades.json** (sobrevive reinicios) |

---

## Arquitectura

```
conflux4-v2/
├── src/
│   ├── main.py              # Orquestador principal
│   ├── conflux4.py          # Motor señales (VWMA+EMA+RSI+ST+ADX+MTF+funding)
│   ├── risk_manager.py      # ⭐ Kelly sizing, circuit breakers, correlaciones
│   ├── trade_manager.py     # Trailing stop, salidas parciales, BE move
│   ├── bingx_client.py      # API BingX (klines MTF, funding, órdenes)
│   ├── telegram_notifier.py # Señales enriquecidas, trade updates, dashboard
│   └── config.py            # Presets y configuración
├── data/
│   ├── equity.json          # Balance, P&L, winrate (persistente)
│   └── trades.json          # Trades activos (persistente entre reinicios)
├── logs/
├── Dockerfile
├── railway.toml
├── .env.example
└── requirements.txt
```

---

## Los 5 módulos clave para la rentabilidad

### 1. Multi-Timeframe (MTF)
Una señal en 15m solo se activa si 1h y 4h también están alineados.  
Elimina señales "contra el trend mayor" — que son las que más pierden.

### 2. Kelly Criterion (tamaño dinámico)
```
Kelly = (Winrate × (RR + 1) - 1) / RR
Posición = Half-Kelly × ajuste de calidad (0-10)
```
En rachas ganadoras → posiciones más grandes.  
En rachas perdedoras → posiciones más pequeñas automáticamente.

### 3. Circuit Breakers
- Si pierdes >3% del capital en un día → **el bot para ese día**
- Si el drawdown desde el peak supera el 15% → **el bot para**
- Evita que un mal día destruya semanas de trabajo.

### 4. Gestión de Trade Activo
- **TP1**: Cierra 25%, mueve stop a breakeven → trade sin riesgo
- **TP2/3**: Cierra 25% adicional cada uno
- **TP4**: Cierra el 25% restante
- **Trailing**: El stop sigue al Supertrend mientras el trade avanza

### 5. Score de Calidad (0-10)
Cada señal recibe una nota. La posición es proporcional a la nota.  
Señales de 4/10 arriesgan la mitad que señales de 8/10.

---

## Configuración paso a paso

### 1. Railway (recomendado para 24/7)

1. Fork del repo en GitHub
2. Railway → New Project → Deploy from GitHub
3. Variables de entorno (Settings → Variables):

```
TELEGRAM_TOKEN        = tu_token_de_botfather
TELEGRAM_CHAT_ID      = -100xxxxxxxxxx
BINGX_API_KEY         = (opcional, para auto-trade)
BINGX_SECRET          = (opcional)
BINGX_TESTNET         = true   ← empieza aquí
AUTO_TRADE            = false  ← empieza aquí
SYMBOLS               = BTC-USDT,ETH-USDT,SOL-USDT
INTERVAL              = 15m
PRESET                = Daytrader
STARTING_BALANCE      = 1000
MAX_DAILY_LOSS_PCT    = 3.0
MAX_DRAWDOWN_PCT      = 15.0
SCAN_SECONDS          = 60
```

### 2. Local (desarrollo/testing)

```bash
git clone https://github.com/tuusuario/conflux4-v2.git
cd conflux4-v2
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edita con tus credenciales
python src/main.py
```

---

## Proceso recomendado antes de activar AUTO_TRADE=true

```
Semana 1-2:  AUTO_TRADE=false, BINGX_TESTNET=true
             → Observa señales, evalúa calidad manualmente

Semana 3-4:  AUTO_TRADE=true, BINGX_TESTNET=true
             → Modo paper, las órdenes no son reales

Mes 2:       AUTO_TRADE=true, BINGX_TESTNET=false, capital pequeño
             → Empieza con STARTING_BALANCE=100 y TRADE_USDT pequeño

Mes 3+:      Escala gradualmente según los resultados reales
```

---

## Ejemplo de flujo de señal completo

```
[Scan] BTC-USDT 15m
  Señal BULL detectada
  → MTF check: 1h BULL ✅, 4h BULL ✅
  → Funding: +0.01% < 0.05% ✅
  → Volumen percentil: 65 ≥ 30 ✅
  → Calidad: 8/10
  → Risk Manager: Kelly=1.8%, posición=180 USDT
  → APROBADO

  Telegram:
    🟢 CONFLUX 4 — BULL SIGNAL
    Entrada: 67,245  |  Stop: 66,890 (-0.53%)
    TP1 (25%): 67,422  TP2 (25%): 67,600
    TP3 (25%): 67,954  TP4 (25%): 68,309
    Posición: 180 USDT  Riesgo: 1.8%
    Calidad: ⭐⭐⭐⭐☆ (8/10)

  [Precio llega a TP1]
    🎯 TP1 — salida 25% @ 67,422
    🔒 Stop movido a BE @ 67,245
    → Trade sin riesgo, 3 TPs restantes

  [Precio llega a TP2]
    🎯 TP2 — salida 25% @ 67,600

  [Supertrend flip en reversal]
    ⚡ Trailing stop activado — stop sube a 67,800
```

---

## Variables completas de entorno

| Variable | Default | Descripción |
|---|---|---|
| `TELEGRAM_TOKEN` | — | Obligatorio |
| `TELEGRAM_CHAT_ID` | — | Obligatorio |
| `BINGX_API_KEY` | — | Opcional (solo auto-trade) |
| `BINGX_SECRET` | — | Opcional |
| `BINGX_TESTNET` | `true` | Paper trading BingX |
| `AUTO_TRADE` | `false` | Ejecutar órdenes reales |
| `SYMBOLS` | `BTC-USDT,ETH-USDT` | Pares separados por coma |
| `INTERVAL` | `15m` | TF principal (MTF auto) |
| `PRESET` | `Daytrader` | Scalp/Daytrader/Swing |
| `STARTING_BALANCE` | `1000` | Capital inicial USDT |
| `MAX_DAILY_LOSS_PCT` | `3.0` | % pérdida diaria → para el bot |
| `MAX_DRAWDOWN_PCT` | `15.0` | % drawdown → para el bot |
| `SCAN_SECONDS` | `60` | Segundos entre escaneos |

---

## ⚠️ Disclaimer

> Software educativo. El trading con derivados implica riesgo elevado de pérdida del capital.  
> Empieza siempre en testnet y con capital mínimo. No es consejo financiero.
