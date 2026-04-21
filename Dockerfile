FROM python:3.12-slim

WORKDIR /app

# Dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código fuente
COPY *.py ./

# Crear directorio de logs
RUN mkdir -p logs

# Variables de entorno por defecto (sobreescribir en Railway)
ENV PORT=8080
ENV SCAN_INTERVAL_SEC=60
ENV KLINE_INTERVAL=1h
ENV MA_FAST=50
ENV MA_SLOW=200
ENV PROJ_LENGTH=10
ENV MIN_MEAN_PNL=0.003
ENV TOP_SYMBOLS=30
ENV MIN_VOLUME_USDT=500000
ENV LEVERAGE=5
ENV RISK_PCT=0.01
ENV TP_MULT=2.0
ENV SL_MULT=1.0
ENV MAX_OPEN_TRADES=5
ENV MAX_HOLD_HOURS=48
ENV MIN_NOTIONAL=10

EXPOSE 8080

CMD ["python", "bot.py"]
