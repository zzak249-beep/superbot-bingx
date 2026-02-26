# SUPERBOT BINGX v5 — Deploy en Railway

## Archivos incluidos
- `main.py` — el bot completo
- `requirements.txt` — dependencias (ninguna externa)
- `Procfile` — comando de inicio
- `railway.toml` — configuracion Railway

---

## Paso a paso para subir a Railway

### 1. Crear cuenta en Railway
Ve a https://railway.app y regístrate con GitHub

### 2. Instalar GitHub Desktop (si no tienes)
Ve a https://desktop.github.com y descárgalo

### 3. Crear repositorio en GitHub
1. Abre GitHub Desktop
2. File → New Repository
3. Nombre: `superbot-bingx`
4. Click en "Publish repository"
5. Arrastra los 4 archivos de esta carpeta al repositorio

### 4. Crear proyecto en Railway
1. Entra a https://railway.app
2. Click en "New Project"
3. Selecciona "Deploy from GitHub repo"
4. Elige tu repositorio `superbot-bingx`

### 5. Configurar Variables de Entorno
En Railway, ve a tu proyecto → Variables y añade estas:

| Variable          | Valor                    |
|-------------------|--------------------------|
| BINGX_API_KEY     | tu API key de BingX      |
| BINGX_API_SECRET  | tu API secret de BingX   |
| TELEGRAM_TOKEN    | tu token de Telegram     |
| TELEGRAM_CHAT_ID  | tu chat ID de Telegram   |
| BOT_MODE          | paper                    |

### 6. Deploy
Railway desplegará automáticamente el bot.
Ve a la pestaña "Logs" para ver que está corriendo.

---

## Para pasar a modo real
Cambia la variable `BOT_MODE` de `paper` a `real` en Railway Variables.
El bot se reinicia automáticamente.

---

## Ventajas de Railway vs PC local
- Corre 24/7 sin que tengas el PC encendido
- Se reinicia solo si hay un error
- Logs en tiempo real desde el navegador
- Gratis hasta $5/mes de uso (suficiente para el bot)
