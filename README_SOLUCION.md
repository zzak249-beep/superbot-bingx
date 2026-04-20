# ⚡ SOLUCIÓN RÁPIDA - 3 PASOS

## El problema
Tu bot falló en Railway con error: `COPY src/ /src/`  
**Causa:** El Dockerfile espera archivos en carpeta `src/` pero están en la raíz.

---

## ✅ SOLUCIÓN (5 minutos)

### 📝 Paso 1: Reemplaza tu Dockerfile

Sube el archivo **`Dockerfile`** que te he generado a tu repositorio (reemplaza el actual).

Este archivo ya está corregido para:
- ✅ Copiar archivos desde la raíz (donde realmente están)
- ✅ Ejecutar `bot_v2.py` (versión con todos los filtros avanzados)
- ✅ Crear carpeta de logs automáticamente

### 🔄 Paso 2: Push a GitHub

```bash
git add Dockerfile
git commit -m "fix: corregir error COPY src/"
git push
```

Railway detectará el cambio y hará rebuild **automáticamente**.

### ⚙️ Paso 3: Configurar variables en Railway

Ve a **Railway → Variables** y añade COMO MÍNIMO:

```
BINGX_API_KEY=tu_api_key
BINGX_SECRET_KEY=tu_secret_key
DEMO_MODE=true
```

**¿Dónde conseguir las claves?**  
BingX → Perfil → API Management → Create New Key → ✅ Futures Enabled

---

## 🎯 Verifica que funciona

1. Railway → Deployments → último debe estar en **verde** ✅
2. Railway → Logs → deberías ver:

```
🤖 BingX Signal Projection Bot v2
   Modo: ⚠️  DEMO
```

Si ves eso = **¡ARREGLADO!** 🎉

---

## 📦 Archivos que te he generado

| Archivo | Descripción |
|---------|-------------|
| `Dockerfile` | ✅ **Corregido** - úsalo para reemplazar el tuyo |
| `RAILWAY_FIX.md` | Guía detallada con troubleshooting |
| `.env.example` | Template con TODAS las variables del bot v2 |
| `.gitignore` | Para no subir archivos sensibles |

---

## 💰 Bot v2 - Qué incluye

Tu bot ahora ejecutará `bot_v2.py` que tiene:

- ✅ **Funding Rate + OI Filter** - Evita entrar contra el sentimiento del mercado
- ✅ **Multi-Timeframe (1h + 4h)** - Solo opera si las tendencias están alineadas
- ✅ **Correlation Filter** - No abre posiciones correlacionadas (ej: DOGE + SHIB)
- ✅ **Smart Limit Orders** - Entra con límites en retrocesos (mejor precio)
- ✅ **Dynamic Leverage** - Reduce apalancamiento automáticamente en alta volatilidad
- ✅ **Telegram Notifications** - Alertas en cada operación (si configuras TELEGRAM_TOKEN)

---

## ⚠️ IMPORTANTE

1. **Empieza SIEMPRE con `DEMO_MODE=true`**
2. Revisa los logs durante 1-2 días
3. Solo cuando estés seguro → `DEMO_MODE=false`

---

## 🆘 ¿Problemas después de arreglar?

**"Module not found"**  
→ Verifica que `requirements.txt` está en la raíz del repo

**"API Key not configured"**  
→ Añade las variables en Railway → Variables

**Bot no arranca**  
→ Comparte los logs de Railway para ayudarte

---

## 📊 Variables recomendadas (mejoran resultados)

Además de las 3 obligatorias, añade estas para mejor performance:

```env
LEVERAGE=5
MAX_OPEN_POSITIONS=5
MAX_RISK_PER_TRADE=1.5
MIN_SCORE=60.0
MTF_MIN_CONFLUENCE=25.0
FUNDING_BLOCK=0.0005
MAX_CORRELATION=0.80
```

Ver todas las opciones en `.env.example`

---

**¿Listo?** 🚀

1. Sube el nuevo `Dockerfile`
2. Configura las 3 variables mínimas
3. ¡Espera el rebuild!
