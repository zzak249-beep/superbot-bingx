FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source code (archivos están en raíz, no en src/)
COPY bot.py .
COPY bot_v2.py .
COPY main.py .
COPY bingx_client.py .
COPY config.py .
COPY risk_manager.py .
COPY scanner.py .
COPY signal_engine.py .
COPY funding_oi.py .
COPY mtf_analyzer.py .
COPY smart_order.py .
COPY filters.py .

# Create logs directory
RUN mkdir -p logs

# Use main.py as entry point (Railway auto-detection compatible)
CMD ["python", "-u", "/app/main.py"]
