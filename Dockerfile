FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
RUN mkdir -p logs data

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
  CMD python -c "import sys; sys.exit(0)"

CMD ["python", "src/main.py"]
