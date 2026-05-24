# QF×JP Bot v3 — Crypto Trading Bot

Bot de trading algorítmico para BingX Perpetual Futures basado en el indicador
**QF Machine × JP Fusion v3** con 12 capas de confirmación.

---

## 🚀 Deploy en Railway

### 1. GitHub
```bash
git init
git add .
git commit -m "QF×JP Bot v3"
git remote add origin https://github.com/TU_USUARIO/qf-jp-bot.git
git push -u origin main
```

### 2. Railway
1. Crear nuevo proyecto → Deploy from GitHub repo
2. Ir a **Variables** y añadir todas las de `.env.example`
3. Railway detecta `railway.toml` y arranca automáticamente

---

## ⚙️ Variables clave

| Variable | Descripción | Default |
|---|---|---|
| `BINGX_API_KEY` | API Key BingX | — |
| `BINGX_SECRET_KEY` | Secret Key BingX | — |
| `TELEGRAM_TOKEN` | Token del bot Telegram | — |
| `TELEGRAM_CHAT_ID` | ID del chat donde recibir alertas | — |
| `SYMBOL` | Par a operar | `BTC-USDT` |
| `LEVERAGE` | Apalancamiento | `10` |
| `RISK_PER_TRADE` | % capital por operación | `0.015` (1.5%) |
| `DAILY_LOSS_LIMIT` | Máximo pérdida diaria | `0.06` (6%) |
| `MIN_CONVICTION` | Score mínimo para operar | `5` |
| `MULTI_PAIR` | Activar scanner multi-par | `false` |

---

## 📊 Arquitectura de señales

```
long_STD   = Score>0.15 + Señal viva + Spread ok + HTF bull
           + Asimetría bull + Vendedores agotados + Funding ok

long_FUEL  = STD + (Ruptura TL  OR  Squeeze ↑  OR  FVG/OB+CVD)

long_SUP   = FUEL + (Dark Pool compra  OR  Divergencia CVD  OR  OB imbalance)
```

**Score convicción 0-10**: cada capa vale 1 punto. Solo opera con ≥5.

---

## 🛡️ Gestión de riesgo

- **Sizing dinámico**: riesgo base × multiplicador convicción × factor pérdidas
- **Trailing Stop**: se ajusta cada vela con ATR
- **Cooldown**: N velas de espera tras una pérdida
- **Límite diario**: para si pérdidas > 6% del capital
- **Anti-hedge**: cierra posición contraria antes de invertir
- **Persistencia**: `state.json` sobrevive reinicios de Railway

---

## 🔍 Ventaja sobre bots estándar

| Técnica | Beneficio |
|---|---|
| CVD Delta | Detecta acumulación/distribución oculta |
| Fair Value Gaps | Zonas de reacción institucional ICT |
| Order Blocks | Niveles donde entró el dinero grande |
| Squeeze Momentum | Detecta explosiones antes de que ocurran |
| Orderbook imbalance | Presión real bid/ask en tiempo real |
| Funding rate filter | Evita trades en mercados saturados |
| Score 0-10 | Solo entradas con alta confluencia |
| Sesiones crypto | Adapta agresividad a NY/London/Asia |

---

## 📁 Estructura

```
qf_jp_bot/
├── main.py              # Scheduler + loop principal
├── strategy.py          # Motor señales QF×JP v3
├── indicators.py        # Todos los indicadores técnicos
├── bingx_client.py      # API BingX (firma HMAC)
├── order_manager.py     # Ejecución y gestión posiciones
├── risk_manager.py      # Sizing, cooldown, límites
├── telegram_notifier.py # Alertas Telegram HTML
├── market_data.py       # Fetching OHLCV
├── health_server.py     # Healthcheck Railway
├── config.py            # Toda la configuración
├── requirements.txt
├── railway.toml
└── .env.example
```

---

## ⚠️ Aviso

Este bot opera con **dinero real**. Ajusta `RISK_PER_TRADE` y `LEVERAGE`
según tu tolerancia al riesgo. Empieza con capital pequeño para validar
el comportamiento en tu par específico.
