# ⚡ SOLUCIÓN RÁPIDA - 3 PASOS

## El problema original
Tu bot falló en Railway con error: `COPY src/ /src/`  
**Causa:** El Dockerfile espera archivos en carpeta `src/` pero están en la raíz.

## ⚠️ NUEVO: Error "can't open file main.py"
Si después del fix ves este error, es porque Railway busca `main.py` por defecto.  
**Solución:** Sube también el archivo `main.py` que te he generado.

---

## ✅ SOLUCIÓN COMPLETA (5 minutos)

### 📝 Paso 1: Sube estos archivos a tu repo

**ARCHIVOS OBLIGATORIOS:**
- ✅ `Dockerfile` (corregido)
- ✅ `main.py` (nuevo - punto de entrada para Railway)

**ARCHIVOS OPCIONALES (recomendados):**
- `Procfile` (alternativa al main.py)
- `.env.example` (template de configuración)
- `.gitignore` (seguridad)

### 🔄 Paso 2: Push a GitHub

```bash
# Añade los archivos nuevos
git add Dockerfile main.py Procfile
git commit -m "fix: corregir Railway - agregar main.py"
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

**Error: "can't open file '/app/main.py'"**  
→ Sube el archivo `main.py` que te generé (es el punto de entrada)

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
