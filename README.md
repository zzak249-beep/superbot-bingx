# Crowding bot — solo señales

Bot de señales del posicionamiento amontonado en perpetuos de BingX.

**NO OPERA. NO PIDE CLAVES DE API.** Solo endpoints públicos, así que no
puede tocar la cuenta ni por error.

## Qué hace

Detecta apalancamiento amontonado (basis extremo + open interest subiendo
+ precio en un extremo) y espera la primera vela EN CONTRA de la multitud.
Cada señal abre una operación **virtual** con stop y objetivo, la sigue
hasta el desenlace y anota el resultado en R con el coste descontado.

El informe diario dice la muestra acumulada **y qué se puede concluir con
ella**:

| ventaja real | operaciones necesarias |
|---|---|
| 0.50 R/op | 31 |
| 0.30 R/op | 87 |
| 0.20 R/op | 196 |
| 0.10 R/op | 784 |

## Despliegue en Railway

1. Proyecto nuevo desde este repo.
2. **Monta un Volume en `/data`.** Sin él, cada redespliegue borra la
   historia acumulada y el bot vuelve a calentar tres días desde cero.
3. Variables de entorno (ver abajo).

## Calentamiento

BingX no sirve histórico de open interest, así que el bot acumula el suyo.
Durante ~3 días dirá `calentando (X/200)` y no emitirá nada. Es correcto.

## Variables

```
TIMEFRAME=15m
SCAN_SEC=300
MIN_VOL_24H=2000000
MAX_SYMBOLS=300
HIST_LEN=300
MIN_HIST=200
OI_LOOK=24
Z_BASIS=2.0
Z_OI=1.0
EXT_PCT=80
ATR_LEN=14
SL_ATR=1.5
TP_R=2.0
MAX_BARS=16
MIN_ATR_PCT=1.0
COST_PCT=0.25
MAX_COST_R=0.20
STATE=/data/crowding_state.json
CSV=/data/crowding_ops.csv
TG_TOKEN=
TG_CHAT=
TG_SIGNALS=false
TG_CLOSES=false
REPORT_HOUR=7
```

## Telegram

`TG_SIGNALS` y `TG_CLOSES` vienen **apagados**. Con ~300 símbolos salen
unos 67 mensajes al día, y un chat con 67 mensajes diarios se deja de leer
en una semana. Por defecto llega **un mensaje al día**: el informe.

Todo queda igualmente en el CSV, que es de donde sale la respuesta a los
15 días.

Pon `TG_SIGNALS=true` los primeros días si quieres ver que emite bien, y
apágalo después.

## Sobre las claves de BingX

**No las pongas.** Todo lo que este bot necesita (klines, open interest,
premium index, tickers) es público. Unas claves no le darían ni un dato
más: solo añadirían un secreto con permiso de trading a un servicio que no
ejecuta nada.

## Régimen (confirm.py)

El módulo `confirm.py` calcula el ratio de varianzas robusto y etiqueta el
símbolo como tendencial, reversivo o indeterminado. **Aquí solo se apunta,
nunca decide.**

Motivo: el crowding opera CONTRA la multitud, o sea que es una estrategia
de reversión. El veto de `confirm.py` está pensado para ruptura y le
quitaría justo sus mejores entradas. Se registra en la columna
`conf_regimen` para que a los 15 días el informe conteste si el crowding
rinde mejor en régimen reversivo — en vez de darlo por hecho.

Variables: `CONFIRM_ENABLED`, `CONFIRM_Q`, `CONFIRM_WIN`,
`CONFIRM_LAMBDA`, `CONFIRM_Z`, `CONFIRM_MIN_VELAS`. `CONFIRM_BLOQUEAR`
está fijado a False en el código y no es configurable a propósito.

## Salida

- `/data/crowding_ops.csv` — una fila por operación virtual cerrada,
  con `conf_z` y `conf_regimen` para cruzar resultados por régimen
- `/data/crowding_state.json` — historia de basis y OI, virtuales abiertas
- Telegram — cada señal, cada cierre, e informe diario
