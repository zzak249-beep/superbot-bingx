FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

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
RUN [ -f "telegram
