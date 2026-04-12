# 🤖 SuperBot v5 — Trading Bot BingX Perpetual Futures

Bot de trading algorítmico para BingX Perpetual Futures con metodología cuantitativa inspirada en Renaissance Technologies (Jim Simons).

## Características

- **Multi-Timeframe**: análisis 15m + 1h + 4h simultáneo
- **Kelly Criterion**: sizing dinámico (25% Kelly fraccionario)
- **Circuit Breaker**: para automáticamente si la pérdida diaria supera el 6%
- **Trailing Stop**: gestión automática de posiciones ganadoras
- **Filtros Institucionales**: funding rate, sesión, CVD, liquidez
- **Kronos AI**: filtro de señales con modelo de series temporales (opcional)
- **Notificaciones Telegram**: alertas en tiempo real

## Estructura del proyecto

```
superbot/
├── main.py              ← Entry point
├── bot.py               ← Orquestador principal
├── strategy.py          ← Señales técnicas (T3, DLO, ZLSMA, Turtle)
├── signals.py           ← Señales multi-timeframe (EMA, RSI, VWAP)
├── scanner.py           ← Escaner de mercado
├── filters.py           ← Filtros institucionales
├── risk_manager.py      ← Gestión de riesgo y sizing
├── bingx_client.py      ← Cliente API BingX
├── data_fetcher.py      ← Descarga de datos OHLCV
├── kronos_filter.py     ← Filtro AI (opcional)
├── notifier.py          ← Alertas Telegram
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── railway.toml
└── .env.example
```

## Instalación local

```bash
git clone https://github.com/TU_USUARIO/superbot.git
cd superbot
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows
pip install -r requirements.txt
cp .env.example .env
# Editar .env con tus API keys
python main.py
```

## Despliegue en Railway (recomendado)

1. Fork este repositorio
2. Entra en [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Selecciona el repo
4. En **Variables**, añade todas las variables de `.env.example` con tus valores reales
5. Railway construye la imagen Docker y despliega automáticamente

## Despliegue en VPS con Docker

```bash
git clone https://github.com/TU_USUARIO/superbot.git /opt/superbot
cd /opt/superbot
cp .env.example .env
nano .env          # rellenar con tus keys reales
docker compose up -d --build
docker compose logs -f
```

## Variables de entorno

| Variable | Descripción | Default |
|---|---|---|
| `BINGX_API_KEY` | API Key de BingX | — |
| `BINGX_API_SECRET` | Secret Key de BingX | — |
| `DRY_RUN` | Paper trading (sin órdenes reales) | `true` |
| `RISK_PER_TRADE` | % del balance por trade | `0.02` |
| `MAX_OPEN_TRADES` | Posiciones simultáneas máx | `4` |
| `LEVERAGE` | Apalancamiento | `10` |
| `DAILY_LOSS_LIMIT` | Circuit breaker (pérdida diaria) | `0.06` |
| `SCAN_PERIOD_SECONDS` | Frecuencia del scanner | `900` |
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram (opcional) | — |
| `TELEGRAM_CHAT_ID` | Chat ID Telegram (opcional) | — |

## Checklist antes de activar LIVE

- [ ] Bot corriendo 24h+ en `DRY_RUN=true` sin errores
- [ ] Log muestra `Balance futuros: $XXX USDT` (no $0)
- [ ] Señales Tier A/B aparecen con score > 60
- [ ] En BingX: fondos en **Perpetual Futures** (no en Spot)
- [ ] API Key con permisos **Futures Trading** activados
- [ ] Empezar con balance pequeño ($100-500)
- [ ] Cambiar `DRY_RUN=false` solo entonces

## Arquitectura de señales

```
Scanner (30 símbolos por volumen)
    ↓
Señal 15m (EMA 9/21/55 + RSI + VWAP)
    + Confirmación 1h
    + Tendencia 4h
    → Score 0-100 | Tier A/B/C
    ↓
Filtros institucionales
    (funding rate, sesión, CVD, liquidez)
    ↓
Kelly Criterion sizing
    (25% fracción, clamped 0.5%-3%)
    ↓
MARKET order + SL automático + Trailing stop
```

## Niveles de salida

| TP | ATR mult | Acción |
|---|---|---|
| TP1 | 2.0× | Cierra 40% de la posición |
| TP2 | 3.5× | Cierra el resto |
| Trailing | 2.5% | Activo desde TP1 en el 60% restante |

SL fijo: 1.5× ATR desde la entrada. Mueve a breakeven cuando el precio llega al 70% del camino a TP1.

## ⚠️ Disclaimer

Este software es solo para fines educativos. El trading de futuros con apalancamiento conlleva riesgo de pérdida total del capital. Opera solo con dinero que puedas permitirte perder.
