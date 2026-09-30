# Bot de tendencia diario (BingX)

Estrategia con la evidencia más sólida para cripto de las que se revisaron:
- **Qué hace:** tendencia por moneda con 9 canales Donchian (5 a 360 días), solo largos, tamaño ajustado a la volatilidad (25% anual), sobre la cesta de las N monedas más líquidas.
- **Fuente:** Zarattini, Pagani y Barbon (2025), SSRN 5209907. Sharpe 1.58 en 2015-2025, ya descontadas las comisiones.
- **Réplicas independientes:** Sharpe 1.27 con pesos reales, 0.14 con pesos barajados.

**Antes de meter dinero, el examen.** Una réplica encontró que casi todo el mérito viene del escalado por volatilidad, no de la señal de tendencia, y hay resultados planos en 2025-26. Por eso `backtest.py` no se limita a medir si gana. Contesta:

1. ¿Gana **después de comisión, deslizamiento y funding**?
2. ¿La tendencia aporta sobre **lo mismo pero siempre comprado** con el mismo escalado?
3. ¿Supera al **ruido** (retornos barajados, sin tendencias reales)?
4. ¿Aguanta en el **30% final** del historial?

Veredicto: 🟢 4/4 → demo y después real pequeño · 🟡 2-3 → solo demo · 🔴 → no operar.

## Railway: dos pasos, un solo servicio

`railway.json` arranca `python main.py`. La variable `TASK` decide qué hace el servicio.

### Paso 1 · Examen (una vez, 20-30 min)

Pega esto en Variables (raw editor):

```
TASK=examen
SOURCE=auto
N_COINS=10
PYTHONUNBUFFERED=1
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

El informe `examen.md` y `variantes.csv` te llegan por Telegram.

### Paso 2 · Bot

Pon la región en **Europa** (Binance bloquea EE. UU.; el bot opera en BingX, pero completa el historial con Binance). Añade un **volumen montado en `/data`** para el estado.

```
TASK=bot
MODE=SIGNAL
ALLOC_USDT=100
N_COINS=10
VOL_TARGET=0.25
ALLOW_SHORT=false
LEVERAGE=3
MIN_ORDER_USDT=5
MAX_DRAWDOWN_PCT=30
DISASTER_STOP=true
RUN_AT_UTC=00:10
RUN_NOW=true
PYTHONUNBUFFERED=1
BINGX_API_KEY=
BINGX_API_SECRET=
LIVE_CONFIRM=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

**Modos:**
- `MODE=SIGNAL`: cartera en papel. No necesita claves y no toca BingX.
- `MODE=DEMO`: órdenes reales en la cuenta demo VST de BingX, con las mismas claves.
- `MODE=LIVE`: dinero real. Exige también `LIVE_CONFIRM=SI`.

## Qué hace cada día a las 00:10 UTC

1. Coge las 40 monedas de BingX con más volumen en 24 h y descarga ~800 días de velas diarias.
2. Elige la cesta: las N más líquidas con al menos 365 días de historial. Se rehace el día 1 de cada mes.
3. Calcula la señal (0-100% de los 9 canales en posición) y el tamaño por volatilidad. Es **el mismo código** que mide el examen.
4. Solo opera lo que se sale de la banda del 2%. Rota unas pocas veces al mes.
5. Pone un stop de emergencia en el exchange por posición: 4 volatilidades diarias o el 15%, lo que sea mayor, y solo sube. Es para un desplome entre dos ejecuciones. El sistema sale al cierre diario.
6. Te manda por Telegram la cesta, las señales y las órdenes.

## Seguridad (lecciones de la flota, todas probadas en `test_bot.py`)

- **Cuenta compartida:** solo toca las posiciones que abrió él. Si una moneda de la cesta ya tiene posición manual o de otro bot, la salta y avisa.
- **Firma:** la cadena firmada es exactamente la enviada. GET en la URL, POST en el cuerpo, `recvWindow` siempre.
- **Modo de posición:** detecta Hedge o One-Way. En Hedge nunca manda `reduceOnly`.
- **Cierres:** cierra con la cantidad **exacta** del exchange.
- **Reinicios:** cada orden lleva un `clientOrderID` determinista y el estado se guarda tras cada orden. Un reinicio no duplica órdenes ni "olvida" posiciones.
- **Margen:** aislado por símbolo antes de la primera orden.
- **Caída máxima:** con un 30% de caída deja de abrir y solo reduce.
- **Variables:** se limpian las comillas. `LIVE` necesita dos cerrojos.

## Capital pequeño

Con volatilidad objetivo del 25%, la exposición típica ronda el 30-60% del capital. Con `ALLOC_USDT=100` y `N_COINS=10`, cada moneda recibe ~3-6 USDT, justo en el mínimo de orden de BingX. Por debajo de `MIN_ORDER_USDT` no opera.

Con poco capital hay dos opciones:
- **`N_COINS=5`:** cada posición es el doble de grande y hay menos diversificación.
- **Subir `ALLOC_USDT`.**

`VOL_TARGET` más alto también sube el tamaño, pero sube el riesgo en la misma proporción.

## Pruebas

```
python test_engine.py   # sin lookahead · el examen rechaza el ruido · detecta tendencias plantadas
python test_bot.py      # firma HTTP · señal · demo Hedge y One-Way · cuenta compartida · reinicio · interruptor
```
