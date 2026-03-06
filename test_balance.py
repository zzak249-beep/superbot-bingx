"""
test_balance.py — Diagnostica exactamente qué devuelve BingX para tu balance
Ejecutar: python test_balance.py
"""

import hmac
import hashlib
import time
import requests
import json
from dotenv import load_dotenv
import os

load_dotenv()

API_KEY    = os.getenv("BINGX_API_KEY", "")
SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
BASE_URL   = "https://open-api.bingx.com"

if not API_KEY or not SECRET_KEY:
    print("❌ ERROR: BINGX_API_KEY o BINGX_SECRET_KEY no encontradas en .env")
    exit(1)

def _sign(qs):
    return hmac.new(SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()

def _get(path, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sig = _sign(qs)
    url = f"{BASE_URL}{path}?{qs}&signature={sig}"
    r = requests.get(url, headers={"X-BX-APIKEY": API_KEY}, timeout=10)
    return r.json()

SEP = "─" * 60

print(f"\n{'═'*60}")
print(f"  TEST BALANCE BINGX")
print(f"{'═'*60}\n")

# ── Endpoint 1: con currency=USDT ──────────────────────────
print("Prueba 1: /balance con currency=USDT")
print(SEP)
resp1 = _get("/openApi/swap/v2/user/balance", {"currency": "USDT"})
print(json.dumps(resp1, indent=2))

# ── Endpoint 2: sin parámetros ─────────────────────────────
print(f"\nPrueba 2: /balance sin parámetros")
print(SEP)
resp2 = _get("/openApi/swap/v2/user/balance", {})
print(json.dumps(resp2, indent=2))

# ── Endpoint 3: cuenta perpetual v3 ────────────────────────
print(f"\nPrueba 3: /openApi/swap/v3/user/balance")
print(SEP)
try:
    resp3 = _get("/openApi/swap/v3/user/balance", {"currency": "USDT"})
    print(json.dumps(resp3, indent=2))
except Exception as e:
    print(f"No disponible: {e}")

# ── Resumen ────────────────────────────────────────────────
print(f"\n{'═'*60}")
print("  RESUMEN — campos encontrados")
print(SEP)

def buscar_numeros(obj, ruta=""):
    """Busca todos los campos numéricos en la respuesta"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            buscar_numeros(v, f"{ruta}.{k}" if ruta else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            buscar_numeros(v, f"{ruta}[{i}]")
    elif isinstance(obj, (int, float, str)):
        try:
            num = float(obj)
            if num > 0:
                print(f"  {ruta} = {num}")
        except:
            pass

print("\nEndpoint 1 (currency=USDT) — valores > 0:")
buscar_numeros(resp1)

print("\nEndpoint 2 (sin params) — valores > 0:")
buscar_numeros(resp2)

print(f"\n{'═'*60}")
print("  Copia el campo correcto y díselo para actualizar exchange.py")
print(f"{'═'*60}\n")
