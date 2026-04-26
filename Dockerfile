FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Instalar gcc para compilar algunas deps de numpy/pandas
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependencias
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Verificar deps críticas
RUN python -c "import loguru, httpx, pandas, numpy; print('✅ Deps OK')"

# Copiar TODO el código (está en la raíz del repo)
COPY *.py .

# Crear directorios de runtime
RUN mkdir -p logs data

# Verificar imports del bot
RUN python -c "from conflux4 import Conflux4Engine; from risk_manager import RiskManager; print('✅ Bot imports OK')"

CMD ["python", "main.py"]
