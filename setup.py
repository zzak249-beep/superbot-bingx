#!/usr/bin/env python3
"""
Configurador Interactivo del Bot
Guía paso a paso para configurar el bot
"""

import os
from pathlib import Path


def get_input(prompt: str, default: str = "", required: bool = False) -> str:
    """Obtener input del usuario"""
    if default:
        prompt += f" [{default}]: "
    else:
        prompt += ": "
    
    value = input(prompt).strip()
    
    if not value:
        if required:
            print("⚠️  Este campo es requerido")
            return get_input(prompt.replace(": ", ""), default, required)
        return default
    
    return value


def configure_bingx():
    """Configurar credenciales BingX"""
    print("\n" + "="*80)
    print("🔐 CONFIGURAR CREDENCIALES BINGX")
    print("="*80)
    print("\n1. Ve a https://bingx.com")
    print("2. Login en tu cuenta")
    print("3. Account → API Management")
    print("4. Create New API Key")
    print("5. Copia los siguientes valores:\n")
    
    api_key = get_input("API Key", required=True)
    api_secret = get_input("API Secret", required=True)
    
    return api_key, api_secret


def configure_telegram():
    """Configurar Telegram"""
    print("\n" + "="*80)
    print("📱 CONFIGURAR TELEGRAM")
    print("="*80)
    print("\n1. Abre Telegram y busca @BotFather")
    print("2. Envía /newbot y sigue las instrucciones")
    print("3. Copia el Bot Token")
    print("4. Busca @userinfobot para obtener tu Chat ID\n")
    
    bot_token = get_input("Bot Token", required=True)
    chat_id = get_input("Chat ID", required=True)
    
    return bot_token, chat_id


def configure_trading():
    """Configurar parámetros de trading"""
    print("\n" + "="*80)
    print("📊 CONFIGURAR PARÁMETROS DE TRADING")
    print("="*80)
    
    print("\n🎯 Selecciona un perfil de configuración:\n")
    print("1. 🟢 CONSERVADOR (Principiantes, $500-1000)")
    print("   - 3-5 pares, Timeframe 1h, Win rate 60-70%")
    print("")
    print("2. 🟡 MODERADO (Intermedios, $1500-3000) ← RECOMENDADO")
    print("   - 10 pares, Timeframe 15m, Win rate 55-65%")
    print("")
    print("3. 🔴 AGRESIVO (Avanzados, $3000+)")
    print("   - 20+ pares, Timeframe 5m, Win rate 50-60%")
    print("")
    
    choice = get_input("Selecciona perfil (1/2/3)", "2")
    
    if choice == "1":
        # Conservador
        symbols = "BTC-USDT,ETH-USDT,SOL-USDT"
        timeframe = "1h"
        max_size = "50"
        max_positions = "2"
        check_interval = "300"
    elif choice == "3":
        # Agresivo
        symbols = "BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT,XRP-USDT,ADA-USDT,DOGE-USDT,AVAX-USDT,DOT-USDT,MATIC-USDT,LINK-USDT,UNI-USDT,ATOM-USDT,ARB-USDT,OP-USDT"
        timeframe = "5m"
        max_size = "150"
        max_positions = "5"
        check_interval = "30"
    else:
        # Moderado (default)
        symbols = "BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT,XRP-USDT,ADA-USDT,AVAX-USDT,MATIC-USDT,LINK-USDT,DOT-USDT"
        timeframe = "15m"
        max_size = "100"
        max_positions = "3"
        check_interval = "60"
    
    print(f"\n✅ Perfil seleccionado\n")
    
    # Permitir personalización
    print("Puedes personalizar estos valores (enter para usar defaults):\n")
    
    symbols = get_input("Pares (SYMBOLS)", symbols)
    timeframe = get_input("Timeframe (1m/5m/15m/1h/4h/1d)", timeframe)
    max_size = get_input("Tamaño máximo por posición ($)", max_size)
    max_positions = get_input("Máximo posiciones simultáneas", max_positions)
    check_interval = get_input("Intervalo de chequeo (segundos)", check_interval)
    
    return symbols, timeframe, max_size, max_positions, check_interval


def configure_strategy():
    """Configurar parámetros de estrategia"""
    print("\n" + "="*80)
    print("⚙️ CONFIGURAR ESTRATEGIA (AVANZADO)")
    print("="*80)
    print("\nEstos parámetros ya están optimizados.")
    print("Solo cámbia si sabes lo que haces.\n")
    
    change_strategy = get_input("¿Cambiar parámetros de estrategia? (s/n)", "n")
    
    if change_strategy.lower() in ['s', 'si', 'yes']:
        linreg_len = get_input("Linear Regression Length", "50")
        linreg_mult = get_input("Linear Regression Multiplier", "2.2")
        adx_thr = get_input("ADX Threshold", "25")
        rr = get_input("Risk/Reward Ratio", "2.5")
    else:
        linreg_len = "50"
        linreg_mult = "2.2"
        adx_thr = "25"
        rr = "2.5"
    
    return linreg_len, linreg_mult, adx_thr, rr


def create_env_file(config: dict):
    """Crear archivo .env"""
    
    env_content = f"""# ============================================
# 🤖 BOT TRADING - CONFIGURACIÓN GENERADA
# ============================================

# 🔐 CREDENCIALES BINGX
BINGX_API_KEY={config['api_key']}
BINGX_API_SECRET={config['api_secret']}

# 📱 CREDENCIALES TELEGRAM
TELEGRAM_BOT_TOKEN={config['bot_token']}
TELEGRAM_CHAT_ID={config['chat_id']}

# ================================================
# 📊 CONFIGURACIÓN DE TRADING
# ================================================

SYMBOLS={config['symbols']}
TIMEFRAME={config['timeframe']}
CHECK_INTERVAL={config['check_interval']}
MAX_POSITION_SIZE={config['max_size']}
MAX_POSITIONS={config['max_positions']}

# ================================================
# ⚙️ PARÁMETROS DE ESTRATEGIA
# ================================================

LINREG_LENGTH={config['linreg_len']}
LINREG_MULT={config['linreg_mult']}
ADX_THRESHOLD={config['adx_thr']}
RISK_REWARD={config['rr']}

# ================================================
# Generado: {config['timestamp']}
# ================================================
"""
    
    # Guardar
    with open('.env', 'w') as f:
        f.write(env_content)
    
    print("\n✅ Archivo .env creado exitosamente\n")
    
    # Mostrar contenido
    print("Contenido del .env:")
    print("=" * 80)
    print(env_content)
    print("=" * 80)


def main():
    """Función principal"""
    
    print("\n" + "="*80)
    print("🤖 CONFIGURADOR INTERACTIVO DEL BOT DE TRADING")
    print("="*80)
    
    print("\nEste script te guiará para configurar el bot en 5 minutos.\n")
    
    # Obtener credenciales
    api_key, api_secret = configure_bingx()
    bot_token, chat_id = configure_telegram()
    
    # Obtener configuración de trading
    symbols, timeframe, max_size, max_positions, check_interval = configure_trading()
    
    # Obtener estrategia
    linreg_len, linreg_mult, adx_thr, rr = configure_strategy()
    
    # Crear configuración
    from datetime import datetime
    
    config = {
        'api_key': api_key,
        'api_secret': api_secret,
        'bot_token': bot_token,
        'chat_id': chat_id,
        'symbols': symbols,
        'timeframe': timeframe,
        'max_size': max_size,
        'max_positions': max_positions,
        'check_interval': check_interval,
        'linreg_len': linreg_len,
        'linreg_mult': linreg_mult,
        'adx_thr': adx_thr,
        'rr': rr,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    # Crear archivo .env
    create_env_file(config)
    
    # Próximos pasos
    print("\n" + "="*80)
    print("✅ CONFIGURACIÓN COMPLETADA")
    print("="*80)
    print("\n📝 Próximos pasos:\n")
    print("1. Verifica el contenido de .env")
    print("2. Ejecuta el test de credenciales:")
    print("   $ python test_credentials.py\n")
    print("3. Monitorea los pares:")
    print("   $ python monitor_pairs.py\n")
    print("4. Inicia el bot:")
    print("   $ python main.py\n")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
