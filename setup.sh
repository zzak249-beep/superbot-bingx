#!/bin/bash
# setup.sh — Instalación rápida SuperBot v5
# Uso: bash setup.sh

set -e

echo "🤖 SuperBot v5 — Setup"
echo "======================"

# Verificar Python 3.10+
python3 --version || { echo "❌ Python 3 requerido"; exit 1; }

# Crear entorno virtual
if [ ! -d "venv" ]; then
    echo "📦 Creando entorno virtual..."
    python3 -m venv venv
fi

# Activar
source venv/bin/activate

# Instalar dependencias
echo "📦 Instalando dependencias..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Crear .env si no existe
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "✅ Archivo .env creado desde .env.example"
    echo "⚠️  EDITA .env con tus API keys antes de continuar:"
    echo "    nano .env"
    echo ""
else
    echo "✅ .env ya existe"
fi

# Crear directorio de datos
mkdir -p data

echo ""
echo "✅ Setup completo. Para iniciar el bot:"
echo "   source venv/bin/activate"
echo "   python main.py"
echo ""
echo "📋 Recuerda: DRY_RUN=true por defecto (paper trading)"
