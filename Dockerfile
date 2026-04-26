FROM python:3.11-slim

# Evitar prompts interactivos
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencias del sistema
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python PRIMERO (capa cacheada)
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# Verificar que loguru está instalado
RUN python -c "import loguru; import httpx; import pandas; import numpy; print('✅ All deps OK')"

# Copiar código fuente
COPY src/ /app/src/

# Crear directorios de datos
RUN mkdir -p /app/logs /app/data

# Verificar que el código importa correctamente
RUN python -c "import sys; sys.path.insert(0,'/app/src'); from conflux4 import Conflux4Engine; print('✅ Code imports OK')"

CMD ["python", "/app/src/main.py"]
