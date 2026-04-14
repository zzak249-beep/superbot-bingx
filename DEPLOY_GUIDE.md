# 🚀 SuperBot v5 — Guía de Despliegue Completa

## Estructura de archivos en tu repo

```
superbot/
├── bot.py
├── strategy.py
├── signals.py
├── filters.py
├── scanner.py
├── risk_manager.py
├── bingx_client.py
├── kronos_filter.py
├── data_fetcher.py
├── requirements.txt
├── Dockerfile
├── .env.template        ← renombrar a .env localmente
├── .env.example         ← versión sin secrets (para el repo)
├── .gitignore           ← CRÍTICO: nunca subir .env
├── railway.toml         ← config de Railway
└── README.md
```

---

## PASO 1 — Crear repo en GitHub

```bash
# En tu máquina local
mkdir superbot && cd superbot

# Copiar todos tus archivos .py aquí
# Luego:
git init
git add .
git commit -m "feat: SuperBot v5 initial commit"

# En GitHub.com → New repository → nombre: superbot
git remote add origin https://github.com/TU_USUARIO/superbot.git
git branch -M main
git push -u origin main
```

---

## PASO 2 — Archivos que necesitas crear

### `.gitignore` (OBLIGATORIO — protege tus API keys)
```
.env
*.pyc
__pycache__/
*.egg-info/
dist/
.DS_Store
*.log
data/
```

### `.env.example` (esto SÍ va al repo, sin valores reales)
```
BINGX_API_KEY=
BINGX_API_SECRET=
DRY_RUN=true
RISK_PER_TRADE=0.02
MAX_OPEN_TRADES=4
LEVERAGE=10
DAILY_LOSS_LIMIT=0.06
MIN_CONFIDENCE=0.52
MIN_VOLUME_USDT=500000
SCAN_PERIOD_SECONDS=900
```

### `railway.toml` (para Railway — despliegue automático)
```toml
[build]
builder = "DOCKERFILE"

[deploy]
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

---

## PASO 3 — Despliegue en Railway (recomendado — gratis hasta $5/mes)

Railway es la opción más fácil para bots Python con Docker.

1. Ve a **railway.app** → Login con GitHub
2. **New Project** → **Deploy from GitHub repo** → selecciona `superbot`
3. Railway detecta el Dockerfile automáticamente
4. Ve a **Variables** → añade una por una:

```
BINGX_API_KEY     → tu_key_real
BINGX_API_SECRET  → tu_secret_real
DRY_RUN           → true   (empieza siempre en true)
RISK_PER_TRADE    → 0.02
MAX_OPEN_TRADES   → 4
LEVERAGE          → 10
DAILY_LOSS_LIMIT  → 0.06
MIN_CONFIDENCE    → 0.52
MIN_VOLUME_USDT   → 500000
SCAN_PERIOD_SECONDS → 900
```

5. Click **Deploy** → Railway construye la imagen y lanza el bot
6. Ve a **Logs** para verificar que arranca bien

---

## PASO 4 — Alternativa: VPS con GitHub Actions (más control)

Si prefieres un servidor propio (DigitalOcean, Hetzner, etc.):

### `.github/workflows/deploy.yml`
```yaml
name: Deploy SuperBot

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Deploy to VPS
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /opt/superbot
            git pull origin main
            docker compose down
            docker compose up -d --build
            echo "✅ SuperBot desplegado"
```

### `docker-compose.yml` (para el VPS)
```yaml
version: "3.9"
services:
  superbot:
    build: .
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/data
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

En el VPS:
```bash
# Primera vez
git clone https://github.com/TU_USUARIO/superbot.git /opt/superbot
cd /opt/superbot
cp .env.example .env
nano .env  # poner tus keys reales
docker compose up -d
docker compose logs -f  # ver logs en vivo
```

---

## PASO 5 — Checklist antes de DRY_RUN=false

- [ ] Bot corriendo 24h+ en `DRY_RUN=true` sin errores
- [ ] Logs muestran "Balance futuros: $XXX USDT" (no $0)
- [ ] Señales generándose con tier A/B correctamente
- [ ] BingX → Wallet → fondos en **Perpetual Futures** (no Spot)
- [ ] API key con permisos de **Futures Trading** activados
- [ ] Empezar con balance pequeño ($100-500) para verificar

---

## Comandos útiles

```bash
# Ver logs en Railway
railway logs

# Ver logs en VPS
docker compose logs -f superbot

# Reiniciar bot
docker compose restart superbot

# Parar bot de emergencia
docker compose stop superbot
```

---

## ⚠️ Nunca hagas esto

- Nunca subas `.env` al repo (usa `.gitignore`)
- Nunca pongas las API keys directamente en el código
- Nunca pongas `DRY_RUN=false` sin haber testeado en paper trading
- Nunca uses más de 10x de apalancamiento hasta tener 3+ meses de historial
