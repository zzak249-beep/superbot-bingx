"""
ANALIZADOR DE TRADES REALES — BingX
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Analiza las operaciones reales del día en BingX y envía informe por Telegram:
  ✅ Trades ejecutados HOY
  ✅ Ganancia/Pérdida de cada trade
  ✅ Filtro por mínimo $7 USDT
  ✅ Estadísticas completas
  ✅ Win rate real
  ✅ Profit factor real

Uso:
  python analyze_today_trades.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict
import httpx
from loguru import logger

# Imports
sys.path.insert(0, '/home/claude')
from bingx_client_fixed import BingXClient, safe_float
from telegram_ultra import TelegramNotifierUltra


# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
BINGX_API_KEY = os.environ.get("BINGX_API_KEY", "")
BINGX_SECRET = os.environ.get("BINGX_SECRET", "")
BINGX_TESTNET = os.environ.get("BINGX_TESTNET", "true").lower() == "true"

MIN_TRADE_USDT = 7.0  # Filtrar trades menores a $7


# ═══════════════════════════════════════════════════════════════
# EXTENSOR DE BINGX CLIENT PARA HISTORIAL
# ═══════════════════════════════════════════════════════════════

class BingXAnalyzer(BingXClient):
    """Extensión de BingXClient para análisis de historial."""
    
    def get_trade_history(self, symbol: str = None, days_back: int = 1) -> List[dict]:
        """
        Obtiene historial de trades cerrados.
        
        Args:
            symbol: Filtrar por símbolo (None = todos)
            days_back: Días hacia atrás (1 = solo hoy)
        
        Returns:
            Lista de trades con detalles completos
        """
        if not self.api_key:
            raise ValueError("API key requerida")
        
        try:
            # Timestamp de inicio (hace X días)
            start_time = int((datetime.now() - timedelta(days=days_back)).timestamp() * 1000)
            
            params = {
                "startTime": start_time,
            }
            if symbol:
                params["symbol"] = symbol
            
            data = self._get_signed("/openApi/swap/v2/trade/allOrders", params)
            orders = data.get("data", {}).get("orders", [])
            
            # Filtrar solo órdenes cerradas/ejecutadas
            closed_orders = [
                o for o in orders 
                if o.get("status") in ["FILLED", "CANCELED", "EXPIRED"]
            ]
            
            logger.info(f"📊 Órdenes cerradas encontradas: {len(closed_orders)}")
            return closed_orders
        
        except Exception as e:
            logger.error(f"Error obteniendo historial: {e}")
            return []
    
    def get_position_history(self, days_back: int = 1) -> List[dict]:
        """
        Obtiene historial de posiciones cerradas (P&L real).
        
        Returns:
            Lista de posiciones con P&L calculado
        """
        if not self.api_key:
            raise ValueError("API key requerida")
        
        try:
            # BingX tiene endpoint específico para historial de posiciones
            start_time = int((datetime.now() - timedelta(days=days_back)).timestamp() * 1000)
            
            params = {
                "startTime": start_time,
                "limit": 100,
            }
            
            # Intentar obtener historial de posiciones
            try:
                data = self._get_signed("/openApi/swap/v2/user/income", params)
                income_records = data.get("data", [])
                
                # Filtrar solo P&L de cierre de posiciones
                pnl_records = [
                    r for r in income_records
                    if r.get("incomeType") in ["REALIZED_PNL", "FUNDING_FEE"]
                ]
                
                logger.info(f"💰 Registros de P&L encontrados: {len(pnl_records)}")
                return pnl_records
            
            except Exception:
                # Si no funciona, usar método alternativo
                logger.warning("Endpoint de income no disponible, usando órdenes")
                return []
        
        except Exception as e:
            logger.error(f"Error obteniendo posiciones: {e}")
            return []
    
    def analyze_trades_today(self) -> dict:
        """
        Analiza todos los trades de hoy y calcula estadísticas.
        
        Returns:
            {
                'trades': [...],
                'total_pnl': float,
                'total_trades': int,
                'winners': int,
                'losers': int,
                'win_rate': float,
                'profit_factor': float,
                'best_trade': float,
                'worst_trade': float,
            }
        """
        # Obtener historial
        orders = self.get_trade_history(days_back=1)
        pnl_records = self.get_position_history(days_back=1)
        
        # Procesar trades
        trades = []
        total_pnl = 0
        
        # Si tenemos P&L records, usar esos
        if pnl_records:
            for record in pnl_records:
                pnl = safe_float(record.get("income", 0))
                symbol = record.get("symbol", "")
                trade_time = record.get("time", 0)
                
                if abs(pnl) < MIN_TRADE_USDT:
                    continue
                
                trades.append({
                    'symbol': symbol,
                    'pnl': pnl,
                    'time': datetime.fromtimestamp(trade_time / 1000) if trade_time else datetime.now(),
                    'type': record.get("incomeType", ""),
                })
                total_pnl += pnl
        
        # Si no hay P&L records, calcular desde órdenes
        else:
            # Agrupar órdenes por símbolo para calcular P&L
            symbol_orders = {}
            for order in orders:
                symbol = order.get("symbol", "")
                if symbol not in symbol_orders:
                    symbol_orders[symbol] = []
                symbol_orders[symbol].append(order)
            
            # Calcular P&L aproximado por símbolo
            for symbol, orders_list in symbol_orders.items():
                # Buscar pares de entrada/salida
                buys = [o for o in orders_list if o.get("side") == "BUY" and o.get("status") == "FILLED"]
                sells = [o for o in orders_list if o.get("side") == "SELL" and o.get("status") == "FILLED"]
                
                # Calcular P&L simple
                for buy, sell in zip(buys, sells):
                    entry_price = safe_float(buy.get("avgPrice", 0))
                    exit_price = safe_float(sell.get("avgPrice", 0))
                    qty = safe_float(buy.get("executedQty", 0))
                    
                    if entry_price == 0 or exit_price == 0:
                        continue
                    
                    pnl = (exit_price - entry_price) * qty
                    
                    if abs(pnl) < MIN_TRADE_USDT:
                        continue
                    
                    trade_time = buy.get("time", 0)
                    
                    trades.append({
                        'symbol': symbol,
                        'pnl': pnl,
                        'entry': entry_price,
                        'exit': exit_price,
                        'qty': qty,
                        'time': datetime.fromtimestamp(trade_time / 1000) if trade_time else datetime.now(),
                        'type': 'TRADE',
                    })
                    total_pnl += pnl
        
        # Calcular estadísticas
        winners = [t for t in trades if t['pnl'] > 0]
        losers = [t for t in trades if t['pnl'] < 0]
        
        total_trades = len(trades)
        win_count = len(winners)
        loss_count = len(losers)
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
        
        total_wins = sum(t['pnl'] for t in winners)
        total_losses = abs(sum(t['pnl'] for t in losers))
        profit_factor = (total_wins / total_losses) if total_losses > 0 else 0
        
        best_trade = max([t['pnl'] for t in trades], default=0)
        worst_trade = min([t['pnl'] for t in trades], default=0)
        
        return {
            'trades': sorted(trades, key=lambda x: x['time'], reverse=True),
            'total_pnl': total_pnl,
            'total_trades': total_trades,
            'winners': win_count,
            'losers': loss_count,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'best_trade': best_trade,
            'worst_trade': worst_trade,
            'total_wins': total_wins,
            'total_losses': total_losses,
        }


# ═══════════════════════════════════════════════════════════════
# GENERADOR DE INFORME
# ═══════════════════════════════════════════════════════════════

def generate_telegram_report(analysis: dict, balance: float) -> str:
    """Genera reporte formateado para Telegram."""
    
    trades = analysis['trades']
    
    # Header
    report = (
        f"📊 <b>INFORME DE TRADES — {datetime.now().strftime('%d/%m/%Y')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance actual: <code>${balance:,.2f}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    # Estadísticas generales
    report += (
        f"<b>📈 RESUMEN DEL DÍA</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Trades ejecutados: <b>{analysis['total_trades']}</b>\n"
        f"   ✅ Ganadores: {analysis['winners']}\n"
        f"   ❌ Perdedores: {analysis['losers']}\n"
        f"   📊 Win Rate: <b>{analysis['win_rate']:.1f}%</b>\n\n"
        f"💵 P&L Total: <code>{analysis['total_pnl']:+,.2f} USDT</code>\n"
        f"   💚 Total ganancias: <code>+${analysis['total_wins']:,.2f}</code>\n"
        f"   ❤️ Total pérdidas: <code>-${analysis['total_losses']:,.2f}</code>\n"
        f"   📊 Profit Factor: <b>{analysis['profit_factor']:.2f}</b>\n\n"
        f"🏆 Mejor trade: <code>+${analysis['best_trade']:,.2f}</code>\n"
        f"💔 Peor trade: <code>${analysis['worst_trade']:,.2f}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    # Detalle de cada trade
    if trades:
        report += f"<b>📋 DETALLE DE TRADES (>{MIN_TRADE_USDT} USDT)</b>\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for i, trade in enumerate(trades, 1):
            emoji = "✅" if trade['pnl'] > 0 else "❌"
            pnl_str = f"+${trade['pnl']:,.2f}" if trade['pnl'] > 0 else f"${trade['pnl']:,.2f}"
            
            time_str = trade['time'].strftime("%H:%M:%S")
            
            report += (
                f"{emoji} <b>Trade #{i}</b>\n"
                f"   Par: <code>{trade['symbol']}</code>\n"
                f"   P&L: <code>{pnl_str}</code>\n"
                f"   Hora: {time_str}\n"
            )
            
            # Si tenemos detalles de entrada/salida
            if 'entry' in trade and 'exit' in trade:
                pct = (trade['exit'] - trade['entry']) / trade['entry'] * 100
                report += (
                    f"   Entry: ${trade['entry']:,.4f}\n"
                    f"   Exit: ${trade['exit']:,.4f} ({pct:+.2f}%)\n"
                )
            
            report += "\n"
    else:
        report += (
            f"<b>📋 SIN TRADES HOY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"No se encontraron trades con P&L > ${MIN_TRADE_USDT}\n\n"
        )
    
    # Veredicto
    report += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    
    if analysis['total_pnl'] > 0:
        report += f"✅ <b>DÍA RENTABLE</b> (+${analysis['total_pnl']:,.2f})"
    elif analysis['total_pnl'] < 0:
        report += f"❌ <b>DÍA CON PÉRDIDAS</b> (${analysis['total_pnl']:,.2f})"
    else:
        report += f"⚪ <b>DÍA NEUTRAL</b> (±$0.00)"
    
    return report


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    logger.info("="*60)
    logger.info("  ANALIZADOR DE TRADES REALES — BingX")
    logger.info("="*60)
    
    # Validar configuración
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.critical("❌ TELEGRAM_TOKEN y TELEGRAM_CHAT_ID requeridos")
        sys.exit(1)
    
    if not BINGX_API_KEY or not BINGX_SECRET:
        logger.critical("❌ BINGX_API_KEY y BINGX_SECRET requeridos")
        sys.exit(1)
    
    # Inicializar
    tg = TelegramNotifierUltra(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
    bingx = BingXAnalyzer(BINGX_API_KEY, BINGX_SECRET, testnet=BINGX_TESTNET)
    
    logger.info("Conectando a BingX...")
    
    # Obtener balance
    balance = bingx.get_balance()
    logger.info(f"Balance actual: ${balance:,.2f}")
    
    # Analizar trades
    logger.info("Analizando trades de hoy...")
    analysis = bingx.analyze_trades_today()
    
    logger.info(f"Trades encontrados: {analysis['total_trades']}")
    logger.info(f"P&L total: ${analysis['total_pnl']:+,.2f}")
    
    # Generar reporte
    report = generate_telegram_report(analysis, balance)
    
    # Enviar por Telegram
    logger.info("Enviando reporte por Telegram...")
    success = tg.send(report)
    
    if success:
        logger.success("✅ Reporte enviado correctamente")
    else:
        logger.error("❌ Error enviando reporte")
        print("\n" + "="*60)
        print("REPORTE (consola):")
        print("="*60)
        # Imprimir sin HTML tags
        import re
        clean_report = re.sub('<[^<]+?>', '', report)
        print(clean_report)
    
    # Resumen en consola
    print("\n" + "="*60)
    print("RESUMEN:")
    print("="*60)
    print(f"Trades: {analysis['total_trades']}")
    print(f"Win Rate: {analysis['win_rate']:.1f}%")
    print(f"P&L Total: ${analysis['total_pnl']:+,.2f}")
    print(f"Profit Factor: {analysis['profit_factor']:.2f}")
    print("="*60)


if __name__ == "__main__":
    main()
