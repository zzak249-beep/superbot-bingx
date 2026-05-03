"""
ANÁLISIS DE TRADES - Versión Interactiva
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Analiza tus trades de HOY en BingX y envía informe por Telegram
"""

import os
import sys
from datetime import datetime

print("""
╔════════════════════════════════════════════════════════════╗
║                                                            ║
║   📊 ANALIZADOR DE TRADES REALES — BingX                  ║
║                                                            ║
║   Revisa tus operaciones de HOY y calcula rentabilidad    ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
""")

# Verificar si estamos en Railway (tiene variables de entorno)
telegram_token = os.environ.get("TELEGRAM_TOKEN", "")
telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
bingx_api_key = os.environ.get("BINGX_API_KEY", "")
bingx_secret = os.environ.get("BINGX_SECRET", "")
bingx_testnet = os.environ.get("BINGX_TESTNET", "true").lower() == "true"

# Si no hay variables de entorno, modo demo
if not telegram_token or not bingx_api_key:
    print("⚠️  Variables de entorno NO encontradas")
    print()
    print("Este script necesita ejecutarse en Railway con las variables:")
    print("  • TELEGRAM_TOKEN")
    print("  • TELEGRAM_CHAT_ID")
    print("  • BINGX_API_KEY")
    print("  • BINGX_SECRET")
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("📋 MODO DEMO - Simulación de análisis")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    
    # Simular análisis
    demo_analysis = {
        'total_trades': 8,
        'winners': 5,
        'losers': 3,
        'win_rate': 62.5,
        'total_pnl': 47.50,
        'total_wins': 78.30,
        'total_losses': 30.80,
        'profit_factor': 2.54,
        'best_trade': 18.90,
        'worst_trade': -12.40,
        'trades': [
            {'symbol': 'BTC-USDT', 'pnl': 18.90, 'time': datetime.now(), 'entry': 67234.50, 'exit': 68242.50},
            {'symbol': 'ETH-USDT', 'pnl': 15.60, 'time': datetime.now(), 'entry': 3456.20, 'exit': 3507.80},
            {'symbol': 'SOL-USDT', 'pnl': 14.30, 'time': datetime.now(), 'entry': 142.30, 'exit': 144.60},
            {'symbol': 'BNB-USDT', 'pnl': -12.40, 'time': datetime.now(), 'entry': 587.50, 'exit': 584.60},
            {'symbol': 'XRP-USDT', 'pnl': 12.50, 'time': datetime.now(), 'entry': 0.5234, 'exit': 0.5312},
            {'symbol': 'ADA-USDT', 'pnl': -9.20, 'time': datetime.now(), 'entry': 0.4567, 'exit': 0.4523},
            {'symbol': 'DOGE-USDT', 'pnl': 9.00, 'time': datetime.now(), 'entry': 0.0823, 'exit': 0.0835},
            {'symbol': 'AVAX-USDT', 'pnl': -9.20, 'time': datetime.now(), 'entry': 34.56, 'exit': 34.12},
        ]
    }
    
    balance = 1047.50
    
    print(f"📊 INFORME DE TRADES — {datetime.now().strftime('%d/%m/%Y')}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"💰 Balance: ${balance:,.2f}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print("📈 RESUMEN DEL DÍA")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"🎯 Trades ejecutados: {demo_analysis['total_trades']}")
    print(f"   ✅ Ganadores: {demo_analysis['winners']}")
    print(f"   ❌ Perdedores: {demo_analysis['losers']}")
    print(f"   📊 Win Rate: {demo_analysis['win_rate']:.1f}%")
    print()
    print(f"💵 P&L Total: ${demo_analysis['total_pnl']:+,.2f}")
    print(f"   💚 Total ganancias: +${demo_analysis['total_wins']:,.2f}")
    print(f"   ❤️  Total pérdidas: -${demo_analysis['total_losses']:,.2f}")
    print(f"   📊 Profit Factor: {demo_analysis['profit_factor']:.2f}")
    print()
    print(f"🏆 Mejor trade: +${demo_analysis['best_trade']:,.2f}")
    print(f"💔 Peor trade: ${demo_analysis['worst_trade']:,.2f}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print("📋 DETALLE DE TRADES")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    
    for i, trade in enumerate(demo_analysis['trades'], 1):
        emoji = "✅" if trade['pnl'] > 0 else "❌"
        pnl_str = f"+${trade['pnl']:,.2f}" if trade['pnl'] > 0 else f"${trade['pnl']:,.2f}"
        time_str = trade['time'].strftime("%H:%M:%S")
        pct = (trade['exit'] - trade['entry']) / trade['entry'] * 100
        
        print(f"{emoji} Trade #{i}")
        print(f"   Par: {trade['symbol']}")
        print(f"   P&L: {pnl_str}")
        print(f"   Entry: ${trade['entry']:,.4f}")
        print(f"   Exit: ${trade['exit']:,.4f} ({pct:+.2f}%)")
        print(f"   Hora: {time_str}")
        print()
    
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("✅ DÍA RENTABLE (+$47.50)")
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("📊 ANÁLISIS DE RENTABILIDAD")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print("Con estos resultados, proyección mensual:")
    print(f"  • P&L diario promedio: ${demo_analysis['total_pnl']:,.2f}")
    print(f"  • P&L mensual (22 días): ${demo_analysis['total_pnl'] * 22:,.2f}")
    print(f"  • ROI mensual: {(demo_analysis['total_pnl'] * 22 / balance * 100):.1f}%")
    print()
    print("Métricas clave:")
    print(f"  ✅ Win Rate {demo_analysis['win_rate']:.1f}% > 55% (EXCELENTE)")
    print(f"  ✅ Profit Factor {demo_analysis['profit_factor']:.2f} > 1.5 (EXCELENTE)")
    print(f"  ✅ R:R promedio: {abs(demo_analysis['best_trade'] / demo_analysis['worst_trade']):.1f}:1")
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("🎯 VEREDICTO: ESTRATEGIA RENTABLE ✅")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print("📋 PARA EJECUTAR CON DATOS REALES:")
    print("   1. Configurar variables de entorno en Railway")
    print("   2. Ejecutar: python analyze_today_trades.py")
    print("   3. Recibirás el informe por Telegram")
    print()
    
    sys.exit(0)

# Si llegamos aquí, tenemos credenciales - ejecutar análisis real
print("✅ Credenciales encontradas")
print(f"   Modo: {'TESTNET' if bingx_testnet else '🔴 LIVE'}")
print()

# Importar el analizador
sys.path.insert(0, '/home/claude')
from analyze_today_trades import main as run_analysis

# Ejecutar
run_analysis()
