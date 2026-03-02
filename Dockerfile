FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir ccxt pandas numpy requests
COPY funding_bot.py /app/bot.py
CMD ["python", "funding_bot.py"]
