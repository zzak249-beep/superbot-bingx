# Estudio P12

Mide el hilo de @p12_hunter con 1 año de velas de 5m y 20 símbolos, usando la misma lógica que `p12_hunter_v4_senales.pine`. Responde a una sola pregunta: **¿el P12 sirve para algo, o todo lo que muestra es azar?**

## En local (lo más fácil)

```
pip install -r requirements.txt
python study.py
```

La primera vez tarda unos minutos, porque descarga ~105.000 velas por símbolo. Después solo baja lo nuevo.

```
python study.py --symbols BTC,ETH,TAO,AAVE --days 540
python study.py --source bingx --symbols AMP,TRUST     # monedas que no están en Binance
python study.py --no-variants                          # más rápido, sin el barrido de filtros
```

## En Railway (ejecución única)

`railway.json` ya pone `restartPolicyType: NEVER`, así que se ejecuta una vez y para.

**Región Europa.** Binance bloquea las IP de EE. UU. y la región por defecto de Railway es EE. UU.: pon la región en Europa o usa `SOURCE=bingx`.

Variables (raw editor):

```
SYMBOLS=BTC,ETH,SOL,XRP,DOGE,BNB,ADA,AVAX,LINK,SUI,TAO,LTC,AAVE,NEAR,TRUMP,ENA,WIF,ARB,ZEC,LDO
DAYS=365
SOURCE=binance
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

El informe sale en los logs y el veredicto llega por Telegram.

## Salida

- `out/informe.md`: el informe completo.
- `out/dias.csv`: un registro por día y símbolo.
- `out/operaciones.csv`: cada operación simulada.
- `out/variantes.csv`: el barrido de filtros.

## Cómo se evita engañarse

1. **Base condicionada.** "Acepta high → el mínimo ya está hecho" se cumple hasta en un paseo aleatorio. Si el precio está arriba, el mínimo queda lejos. Por eso se compara con días con el precio en el mismo sitio que no aceptaron: así se aísla lo que aporta el tiempo.
2. **Control con ruido.** Todo se repite sobre series barajadas con la misma volatilidad por hora que el símbolo, pero sin sesiones. Lo que acierte igual ahí es geometría del rango, no información.
3. **Horas del extremo contra la ley del arcoseno**, no contra la duración de cada bloque. En un paseo aleatorio, el máximo y el mínimo caen más a menudo al principio y al final de la ventana.
4. **Operaciones:** t ≥ 3, entrenamiento/prueba 70/30 y desglose por mes y por símbolo. Se mira el agregado, no los mejores símbolos.
5. **16 variantes probadas.** Solo cuenta como candidata la que gana en entrenamiento y en prueba con t ≥ 3.

`python test_p12.py` comprueba tres cosas:
- **Día construido a mano:** da el resultado exacto calculado a mano.
- **Paseo aleatorio:** no encuentra nada.
- **Efecto real plantado:** lo detecta (t ≈ 3.9).

## Diferencias con TradingView

- Si en la misma vela se tocan el stop y el objetivo, cuenta el stop (conservador).
- Solo se simula la entrada "Confirmación 5m".
- La comisión es del 0.06% por lado y el funding del 0.01% por liquidación cruzada, igual que en el Pine v4.1.
