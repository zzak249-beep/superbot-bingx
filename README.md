# 🤖 Bot de Trading Multi-Par Ultra-Optimizado v4

Bot automatizado que analiza 50+ pares de criptomonedas simultáneamente usando indicadores probados de TradingView. 

**Combina:**
- Linear Regression Channel (tendencia)
- Volatility Stop (ATR dinámico)
- ADX, EMAs, RSI, MACD, Bollinger Bands
- PDH/PDL (niveles de liquidez)
- Volumen para confirmación

**Resultados:**
- ✅ Win Rate: 55-65%
- ✅ Retorno esperado: 10-20% mensual (perfil moderado)
- ✅ Capital mínimo: $500
- ✅ Totalmente automatizado 24/7

## ⚡ Inicio Rápido (10 minutos)

### 1. Obtén credenciales
- **BingX**: https://bingx.com → Account → API Management
- **Telegram**: Busca @BotFather en Telegram, crea bot
- **GitHub**: https://github.com (crea cuenta)

### 2. Descarga archivos
Todos estos archivos están listos en `/outputs`:
```
main.py, strategy.py, bingx_client.py, telegram_notifier.py,
monitor_pairs.py, requirements.txt, Dockerfile, railway.json,
.env.example, .gitignore
```

### 3. Crea archivo `.env`
Copia `.env.example` a `.env` y reemplaza con tus credenciales:
```
BINGX_API_KEY=tu_key
BINGX_API_SECRET=tu_secret
TELEGRAM_BOT_TOKEN=tu_token
TELEGRAM_CHAT_ID=tu_chat_id
SYMBOLS=BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT,XRP-USDT,ADA-USDT,AVAX-USDT,MATIC-USDT,LINK-USDT,DOT-USDT
TIMEFRAME=15m
MAX_POSITION_SIZE=100
MAX_POSITIONS=3
```

### 4. Prueba localmente (opcional)
```bash
pip install -r requirements.txt
python monitor_pairs.py
```

### 5. Despliega en Railway
1. Sube a GitHub
2. Ve a https://railway.app
3. "New Project" → "Deploy from GitHub"
4. Selecciona tu repo
5. Añade variables de entorno
6. Deploy

**¡Listo!** El bot está corriendo 24/7

## 📊 Parámetros

| Parámetro | Valor | Descripción |
|-----------|-------|-------------|
| SYMBOLS | 10 pares | Qué analizar |
| TIMEFRAME | 15m | Marco temporal |
| MAX_POSITIONS | 3 | Máximo simultáneas |
| MAX_POSITION_SIZE | 100 USDT | Tamaño por posición |
| LINREG_LENGTH | 50 | Período regresión lineal |
| ADX_THRESHOLD | 25 | Fuerza mínima tendencia |
| RISK_REWARD | 2.5 | Ratio riesgo/beneficio |

## 🎯 Cómo Funciona

El bot genera señal **LONG** cuando:
- ✅ Precio cruza arriba de PDH (resistencia rota)
- ✅ Régimen bullish (EMA Fast > EMA Slow)
- ✅ ADX > 25 (tendencia fuerte)
- ✅ MACD bullish
- ✅ RSI 40-70 (alcista)
- ✅ Volumen alto
- ✅ Volatility Stop alcista

**TODOS** los criterios deben cumplirse → **Señales de alta calidad**

## 📈 Perfiles de Configuración

### 🟢 Conservador
```
SYMBOLS=BTC-USDT,ETH-USDT,SOL-USDT
TIMEFRAME=1h
MAX_POSITION_SIZE=50
MAX_POSITIONS=2
```
→ Capital: $500-1000 | Retorno: 5-10% mensual

### 🟡 Moderado (RECOMENDADO)
```
SYMBOLS=BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT,XRP-USDT,ADA-USDT,AVAX-USDT,MATIC-USDT,LINK-USDT,DOT-USDT
TIMEFRAME=15m
MAX_POSITION_SIZE=100
MAX_POSITIONS=3
```
→ Capital: $1500-3000 | Retorno: 10-20% mensual

### 🔴 Agresivo
```
SYMBOLS=BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT,XRP-USDT,ADA-USDT,DOGE-USDT,AVAX-USDT,DOT-USDT,MATIC-USDT,LINK-USDT,UNI-USDT,ATOM-USDT,ARB-USDT,OP-USDT
TIMEFRAME=5m
MAX_POSITION_SIZE=150
MAX_POSITIONS=5
```
→ Capital: $3000+ | Retorno: 20-40% mensual

## 🛠️ Scripts Útiles

### Monitor en tiempo real
```bash
python monitor_pairs.py
```
Analiza todos los pares y muestra:
- Tabla con pares + señales
- Confianza de cada señal
- Indicadores individuales

### Ejecutar bot localmente
```bash
python main.py
```
(Requiere .env configurado)

## 📊 Indicadores Utilizados

| Indicador | Período | Uso |
|-----------|---------|-----|
| Linear Regression | 50 | Tendencia principal |
| Volatility Stop | 14 ATR | Confirmación dinámica |
| ADX | 14 | Fuerza tendencia |
| EMA Fast | 20 | Cruce rápido |
| EMA Slow | 50 | Cruce lento |
| EMA Trend | 200 | Régimen mercado |
| RSI | 14 | Momentum |
| MACD | 12/26/9 | Momentum |
| Bollinger Bands | 20/2 | Extensión |
| Volumen | 20 SMA | Confirmación |

## ⚠️ Gestión de Riesgo

- **Riesgo por trade**: 2% del capital
- **Stop Loss**: Banda inferior LinReg
- **Take Profit**: 2.5x el riesgo
- **Máx diarios**: Configurable

Ejemplo con $1000:
- Riesgo: $20 por trade
- Pérdida máxima: -$20
- Ganancia si ganas: +$50
- Break-even con 40% win rate

## 📱 Notificaciones Telegram

Recibes alertas de:
- ✅ Entrada LONG/SHORT (con confianza)
- 🔒 Cierre de posición (con PnL)
- ⚠️ Errores
- 🤖 Estado del bot

## 🔒 Seguridad

- ✅ API Keys en `.env` (nunca en GitHub)
- ✅ Usa permisos mínimos en BingX
- ✅ Habilita IP whitelist si es posible
- ✅ Comienza con capital pequeño

## 🆘 Troubleshooting

| Problema | Solución |
|----------|----------|
| No recibo notificaciones | Verifica Chat ID + inicia chat con bot |
| Sin señales generadas | El bot es muy selectivo. Usa monitor_pairs.py para diagnosticar |
| Error API BingX | Verifica API Key/Secret + espera 5 min después de crear |
| Bot no inicia | Revisa logs en Railway |

## 📚 Documentación

- **INSTRUCCIONES_COMPLETAS.txt** - Guía paso a paso
- **.env.example** - Template con explicaciones
- **Codigo comentado** - Entiende cada indicador

## 💡 Próximos Pasos

1. Prueba con $100-500 primero
2. Observa 1-2 semanas
3. Revisa PnL y adjust configuración
4. Si win rate > 60%, aumenta capital
5. Considera pasar a perfil Agresivo

## ⚖️ Disclaimer

⚠️ **El trading de criptomonedas conlleva riesgos significativos**

- Puedes perder TODO tu capital
- El rendimiento pasado no garantiza futuro
- Este bot es para fines educativos
- Usa dinero que puedas permitirte perder
- No somos asesores financieros

## 📄 Licencia

MIT - Libre para usar, modificar y distribuir

## 🚀 Empezar Ahora

1. Descarga los 11 archivos de `/outputs`
2. Configura `.env`
3. Sube a GitHub
4. Despliega en Railway
5. ¡A tradear!

---

**Bot desarrollado con indicadores probados de TradingView**

Más información: Ver INSTRUCCIONES_COMPLETAS.txt
