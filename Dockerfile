FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir ccxt pandas numpy requests
COPY fundingbot.py /app/bot.py
CMD ["python", "bot.py"]
