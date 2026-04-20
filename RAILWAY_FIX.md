# 🔧 Reparación del Deploy en Railway

## 📋 Problema identificado

El error `COPY src/ /src/` ocurre porque el Dockerfile original esperaba una carpeta `src/` pero los archivos Python están en la raíz del repositorio.

---

## ✅ Solución - Opción A: Reemplazar el Dockerfile (MÁS RÁPIDO)

### 1. Reemplaza tu `Dockerfile` con este:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source code (archivos están en raíz, no en src/)
COPY bot.py .
COPY bot_v2.py .
COPY bingx_client.py .
COPY config.py .
COPY risk_manager.py .
COPY scanner.py .
COPY signal_engine.py .
COPY funding_oi.py .
COPY mtf_analyzer.py .
COPY smart_order.py .
COPY filters.py .

# Create logs directory
RUN mkdir -p logs

# Default to v2 (advanced bot with all filters)
# To use v1, set environment variable: BOT_VERSION=v1
CMD ["python", "-u", "bot_v2.py"]
```

### 2. Haz commit y push:

```bash
git add Dockerfile
git commit -m "fix: corregir Dockerfile para estructura de archivos actual"
git push
```

### 3. Railway detectará el cambio y hará rebuild automáticamente ✅

---

## ✅ Solución - Opción B: Reorganizar la estructura (MÁS LIMPIA)

Si prefieres la estructura "correcta" con carpeta `src/`:

```bash
# Crear carpeta src y mover archivos
mkdir src
mv *.py src/
git add .
git commit -m "refactor: mover archivos Python a carpeta src/"
git push
```

Luego usa este `Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source code
COPY src/ ./src/
RUN mkdir -p logs

WORKDIR /app/src

CMD ["python", "-u", "bot_v2.py"]
```

---

## 🚀 Variables de entorno en Railway

Una vez que el build funcione, configura estas variables **OBLIGATORIAS** en Railway:

### Panel Railway → Tu servicio → Variables → Add Variable:

```env
BINGX_API_KEY=tu_api_key_de_bingx
BINGX_SECRET_KEY=tu_secret_key_de_bingx
DEMO_MODE=true
```

### Variables **RECOMENDADAS** (mejoran el bot):

```env
# Trading
LEVERAGE=5
MAX_OPEN_POSITIONS=5
MAX_RISK_PER_TRADE=1.5
MAX_DAILY_LOSS_PCT=5.0
MIN_SCORE=60.0

# Bot v2 Features (filtros avanzados)
FUNDING_BLOCK=0.0005
MTF_MIN_CONFLUENCE=25.0
MAX_CORRELATION=0.80

# Telegram (OPCIONAL pero muy útil)
TELEGRAM_TOKEN=tu_token_de_botfather
TELEGRAM_CHAT_ID=tu_chat_id
```

---

## 📊 Verificar que funciona

Después del deploy exitoso:

1. Ve a **Railway → Deployments** → último deployment debe mostrar "Success" ✅
2. Ve a **Logs** → deberías ver:

```
🤖 BingX Signal Projection Bot v2
   Modo: ⚠️  DEMO
   Filtros: Funding+OI | MTF | Correlación | Sesión
```

3. Si ves ese mensaje = **¡FUNCIONANDO!** 🎉

---

## 🆘 Errores comunes después de arreglar el Dockerfile

| Error en logs | Solución |
|--------------|----------|
| `BINGX_API_KEY no configurada` | Añadir variables de entorno en Railway |
| `Module 'httpx' not found` | El requirements.txt no se está usando → verificar build logs |
| `Permission denied` en `/logs` | Ya está solucionado en el Dockerfile con `RUN mkdir -p logs` |

---

## 🎯 Cuál versión del bot ejecutar

- **`bot_v2.py`** (RECOMENDADO): Incluye todos los filtros avanzados:
  - Funding rate + Open Interest
  - Multi-timeframe (1h + 4h)
  - Correlation filter
  - Smart limit orders
  - Telegram notifications
  
- **`bot.py`**: Versión básica sin filtros avanzados (solo para testing)

El Dockerfile actualizado usa `bot_v2.py` por defecto.

---

## 💡 Próximos pasos después de reparar

1. ✅ Verifica que el bot arranca en DEMO MODE
2. ✅ Revisa los logs durante 1-2 horas
3. ✅ Configura Telegram para recibir alertas
4. ⚠️  Cuando estés listo para real: `DEMO_MODE=false`
5. 🚀 ¡A tradear!

---

## 📝 Checklist final

- [ ] Dockerfile actualizado y pusheado
- [ ] Build en Railway exitoso (verde)
- [ ] Variables BINGX_API_KEY y BINGX_SECRET_KEY configuradas
- [ ] DEMO_MODE=true (para empezar)
- [ ] Logs muestran el mensaje de inicio del bot
- [ ] (Opcional) Telegram configurado

---

**¿Necesitas más ayuda?** 

- Logs de Railway: `railway logs`
- Ver variables: Railway → Settings → Variables
- Rebuild manual: Railway → Deployments → Redeploy
