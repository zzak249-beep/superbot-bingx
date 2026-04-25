# python:3.11 (sin -slim) ya incluye gcc y build tools
# Evita el apt-get que crashea en el Metal builder de Railway
FROM python:3.11

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs data

RUN find /app -name "signal.py" -delete 2>/dev/null; echo "signal.py eliminado"

RUN mkdir -p exchange strategy notifications utils backtest && \
    for d in exchange strategy notifications utils backtest; do \
        [ -f "$d/__init__.py" ] || touch "$d/__init__.py"; \
    done

RUN [ -f "bingx_rest.py" ]        && mv bingx_rest.py exchange/        || true
RUN [ -f "bingx_ws.py" ]          && mv bingx_ws.py exchange/          || true
RUN [ -f "engine.py" ]            && mv engine.py strategy/            || true
RUN [ -f "telegram_notifier.py" ] && mv telegram_notifier.py notifications/ || true
RUN [ -f "symbol_scanner.py" ]    && mv symbol_scanner.py utils/       || true
RUN [ -f "backtest.py" ]          && mv backtest.py backtest/          || true

RUN found=$(find /app -name "indicators.py" | grep -v __pycache__ | head -1); \
    if [ -n "$found" ] && [ "$found" != "/app/utils/indicators.py" ]; then \
        cp "$found" /app/utils/indicators.py; \
    elif [ ! -f "/app/utils/indicators.py" ]; then \
        echo "ERROR: indicators.py no encontrado" && exit 1; \
    fi

RUN [ -f "strategy/engine.py" ] && \
    sed -i 's/from strategy\.indicators import/from utils.indicators import/g' strategy/engine.py && \
    sed -i 's/from indicators import/from utils.indicators import/g' strategy/engine.py || true

RUN echo "from utils.indicators import *" > strategy/indicators.py

RUN python3 -c "import sys; sys.path.insert(0,'/app'); from utils.indicators import ema_last,atr_last,hma,hma_last,rsi,macd,vwap,supertrend; print('indicators OK')"
RUN python3 -c "import sys; sys.path.insert(0,'/app'); from strategy.engine import engine, SignalResult; print('engine OK')" || (echo '==ERROR ENGINE==' && exit 1)

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["python", "main.py"]
