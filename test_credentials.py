#!/usr/bin/env python3
"""
Test de Credenciales - Verifica que todo está configurado correctamente
Ejecuta ANTES de correr el bot por primera vez
"""

import os
import sys
from dotenv import load_dotenv
import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

print("\n" + "="*80)
print("🧪 TEST DE CREDENCIALES Y CONEXIÓN")
print("="*80 + "\n")

# Variables a verificar
tests_passed = 0
tests_failed = 0

# ==================== TEST 1: Variables de entorno ====================
print("1️⃣  VERIFICANDO VARIABLES DE ENTORNO...\n")

required_vars = {
    'BINGX_API_KEY': 'API Key de BingX',
    'BINGX_API_SECRET': 'Secret Key de BingX',
    'TELEGRAM_BOT_TOKEN': 'Bot Token de Telegram',
    'TELEGRAM_CHAT_ID': 'Chat ID de Telegram',
}

missing_vars = []

for var, desc in required_vars.items():
    value = os.getenv(var)
    if value:
        # Mostrar solo primeros y últimos caracteres por seguridad
        if len(value) > 10:
            masked = value[:4] + '*' * (len(value)-8) + value[-4:]
        else:
            masked = '*' * len(value)
        print(f"   ✅ {var}: {masked}")
        tests_passed += 1
    else:
        print(f"   ❌ {var}: NO ENCONTRADO")
        missing_vars.append(var)
        tests_failed += 1

if missing_vars:
    print(f"\n   ⚠️  Faltan variables:")
    for var in missing_vars:
        print(f"      - {var}")
    print(f"\n   📝 Edita .env y añade estos valores\n")
else:
    print(f"\n   ✅ Todas las credenciales están configuradas\n")

# ==================== TEST 2: Parámetros de trading ====================
print("2️⃣  VERIFICANDO PARÁMETROS DE TRADING...\n")

trading_params = {
    'SYMBOLS': 'Pares a analizar',
    'TIMEFRAME': 'Marco temporal',
    'MAX_POSITION_SIZE': 'Tamaño máximo por posición',
    'MAX_POSITIONS': 'Máximo de posiciones simultáneas',
    'CHECK_INTERVAL': 'Intervalo de chequeo (segundos)',
}

for param, desc in trading_params.items():
    value = os.getenv(param)
    if value:
        print(f"   ✅ {param}: {value}")
        tests_passed += 1
    else:
        print(f"   ⚠️  {param}: No configurado (usará default)")

print()

# ==================== TEST 3: Telegram ====================
print("3️⃣  VERIFICANDO CONEXIÓN TELEGRAM...\n")

bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
chat_id = os.getenv('TELEGRAM_CHAT_ID')

if bot_token and chat_id:
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getMe"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                bot_name = data['result'].get('first_name', 'Bot')
                print(f"   ✅ Bot conectado: {bot_name}")
                
                # Intentar enviar un mensaje de prueba
                test_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                test_payload = {
                    'chat_id': chat_id,
                    'text': '🤖 Test de conexión exitoso - Tu bot está funcionando'
                }
                
                test_response = requests.post(test_url, json=test_payload, timeout=5)
                if test_response.status_code == 200:
                    print(f"   ✅ Mensaje de prueba enviado")
                    print(f"   ✅ Verifica tu chat en Telegram")
                    tests_passed += 1
                else:
                    print(f"   ❌ Error enviando mensaje: {test_response.status_code}")
                    tests_failed += 1
            else:
                print(f"   ❌ Bot inválido: {data.get('description')}")
                tests_failed += 1
        else:
            print(f"   ❌ Error conectando: HTTP {response.status_code}")
            tests_failed += 1
            
    except requests.exceptions.Timeout:
        print(f"   ❌ Timeout - Sin conexión a internet o Telegram")
        tests_failed += 1
    except Exception as e:
        print(f"   ❌ Error: {e}")
        tests_failed += 1
else:
    print(f"   ⚠️  Credenciales Telegram no configuradas")

print()

# ==================== TEST 4: BingX API ====================
print("4️⃣  VERIFICANDO CONEXIÓN BINGX...\n")

api_key = os.getenv('BINGX_API_KEY')
api_secret = os.getenv('BINGX_API_SECRET')

if api_key and api_secret:
    try:
        # Probar obtener información de mercado (no requiere auth)
        url = "https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol=BTC-USDT"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('code') == 0:
                price = float(data['data'].get('lastPrice', 0))
                print(f"   ✅ API BingX conectada")
                print(f"   ✅ Precio BTC: ${price:,.2f}")
                tests_passed += 1
            else:
                print(f"   ⚠️  API responde pero error: {data.get('msg')}")
        else:
            print(f"   ❌ Error HTTP: {response.status_code}")
            tests_failed += 1
            
    except requests.exceptions.Timeout:
        print(f"   ❌ Timeout - Sin conexión o BingX no responde")
        tests_failed += 1
    except Exception as e:
        print(f"   ❌ Error: {e}")
        tests_failed += 1
else:
    print(f"   ⚠️  Credenciales BingX no configuradas")

print()

# ==================== TEST 5: Python Modules ====================
print("5️⃣  VERIFICANDO MÓDULOS PYTHON...\n")

required_modules = {
    'numpy': 'Cálculos numéricos',
    'requests': 'HTTP requests',
    'dotenv': 'Variables de entorno',
}

for module, desc in required_modules.items():
    try:
        __import__(module)
        print(f"   ✅ {module}: Instalado")
        tests_passed += 1
    except ImportError:
        print(f"   ❌ {module}: No instalado")
        print(f"      → pip install {module}")
        tests_failed += 1

print()

# ==================== RESUMEN ====================
print("="*80)
print("📊 RESUMEN DE TESTS")
print("="*80)
print(f"\n✅ Tests exitosos: {tests_passed}")
print(f"❌ Tests fallidos: {tests_failed}")

if tests_failed == 0:
    print(f"\n🎉 ¡TODO ESTÁ CONFIGURADO CORRECTAMENTE!")
    print(f"\n▶️  Próximo paso: Ejecuta el bot")
    print(f"   python main.py\n")
    sys.exit(0)
else:
    print(f"\n⚠️  CORRIGE LOS ERRORES ANTES DE CONTINUAR\n")
    sys.exit(1)
