#!/bin/bash
# ============================================================
#  setup_repo.sh — Crea toda la estructura del bot de golpe
#  Uso: bash setup_repo.sh   (ejecutar en la raíz del repo)
# ============================================================
set -e

echo "📁 Creando estructura de carpetas..."
mkdir -p exchange strategy utils notifications

# ── __init__.py ──────────────────────────────────────────────
echo "# exchange package" > exchange/__init__.py
echo "# strategy package" > strategy/__init__.py
echo "# utils package"    > utils/__init__.py
echo "# notifications package" > notifications/__init__.py

echo "✅ Carpetas e __init__.py creados"

# ── Dockerfile ───────────────────────────────────────────────
cat > Dockerfile << 'DOCKERFILE'
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN test -f /app/main.py || (echo "ERROR: main.py no encontrado" && exit 1)

CMD ["python", "main.py"]
DOCKERFILE

# ── .dockerignore ────────────────────────────────────────────
cat > .dockerignore << 'DOCKERIGNORE'
.git
.gitignore
.env
.env.*
__pycache__
*.pyc
*.pyo
.pytest_cache
.mypy_cache
.venv
venv
node_modules
*.md
.DS_Store
tests/
test_*
DOCKERIGNORE

# ── requirements.txt ─────────────────────────────────────────
cat > requirements.txt << 'REQUIREMENTS'
aiohttp>=3.9.0
websockets>=12.0
numpy>=1.26.0
python-dotenv>=1.0.0
REQUIREMENTS

echo "✅ Dockerfile, .dockerignore y requirements.txt listos"

# ── Git ──────────────────────────────────────────────────────
echo ""
echo "📦 Haciendo commit y push..."
git add .
git commit -m "fix: add missing packages exchange/strategy/utils/notifications"
git push

echo ""
echo "🚀 ¡Listo! Railway debería arrancar en unos segundos."
echo ""
echo "Estructura final:"
find . -name "*.py" -not -path "./.git/*" -not -path "./__pycache__/*" | sort
