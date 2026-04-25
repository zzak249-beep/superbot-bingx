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

# Verificar imports críticos
RUN python3 -c "from utils.indicators import ema, atr, hma, hma_last, rsi, macd, vwap, supertrend; print('utils.indicators OK')"
RUN python3 -c "from strategy.indicators import hma_direction, hma_crossover, atr_now, adx_vals, market_regime, REGIME_TRENDING; print('strategy.indicators OK')"
RUN python3 -c "from strategy.engine import engine, SignalResult; print('engine OK')"

CMD ["python", "main.py"]
