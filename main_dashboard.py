#!/usr/bin/env python3
"""
Bot Trading Profesional v2 CON DASHBOARD
Análisis direccional + Registro automático de trades + Dashboard HTML
"""

import os
import asyncio
import logging
import requests
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
load_dotenv()


class DashboardStats:
    """Gestión de estadísticas para dashboard"""
    
    def __init__(self):
        self.data_file = 'bot_stats.json'
        self.load_stats()
    
    def load_stats(self):
        if Path(self.data_file).exists():
            try:
                with open(self.data_file, 'r') as f:
                    self.stats = json.load(f)
            except:
                self.stats = self.create_empty()
        else:
            self.stats = self.create_empty()
    
    def create_empty(self):
        return {
            'start_date': datetime.now().isoformat(),
            'capital_inicial': 0,
            'capital_actual': 0,
            'trades': {
                'total': 0,
                'wins': 0,
                'losses': 0,
                'pending': 0
            },
            'pnl': {
                'total_usdt': 0.0,
                'total_percent': 0.0
            },
            'signals': {
                'long_total': 0,
                'short_total': 0,
                'long_today': 0,
                'short_today': 0
            },
            'historial': []
        }
    
    def save(self):
        try:
            with open(self.data_file, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except:
            pass
    
    def add_signal(self, symbol, direction, entry_price, tp1, tp2, sl):
        """Registrar nueva señal"""
        signal = {
            'id': len(self.stats['historial']) + 1,
            'symbol': symbol,
            'direction': direction,
            'entry': entry_price,
            'tp1': tp1,
            'tp2': tp2,
            'sl': sl,
            'timestamp': datetime.now().isoformat(),
            'status': 'OPEN',
            'exit': 0,
            'pnl': 0
        }
        
        self.stats['historial'].append(signal)
        self.stats['trades']['total'] += 1
        self.stats['trades']['pending'] += 1
        
        if direction == 'LONG':
            self.stats['signals']['long_total'] += 1
            self.stats['signals']['long_today'] += 1
        else:
            self.stats['signals']['short_total'] += 1
            self.stats['signals']['short_today'] += 1
        
        self.save()
        return signal


class BotProfesionalConDashboard:
    """Bot profesional con integración de dashboard"""
    
    def __init__(self):
        """Inicializar"""
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        
        self.symbols = [
            'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'MATIC-USDT', 'AVAX-USDT',
            'DOGE-USDT', 'ADA-USDT', 'XRP-USDT', 'DOT-USDT', 'LINK-USDT'
        ]
        
        self.interval = int(os.getenv('CHECK_INTERVAL', '180'))
        self.position_size = float(os.getenv('MAX_POSITION_SIZE', '100'))
        
        self.dashboard = DashboardStats()
        self.trade_stats = {'total': 0, 'wins': 0, 'losses': 0, 'profit': 0.0}
        
        logger.info("="*80)
        logger.info("🚀 BOT PROFESIONAL v2 + DASHBOARD")
        logger.info(f"📊 Pares: {len(self.symbols)}")
        logger.info(f"⏱️ Intervalo: {self.interval}s")
        logger.info("🎯 Características:")
        logger.info("   ✅ Análisis direccional")
        logger.info("   ✅ LONG y SHORT automático")
        logger.info("   ✅ Dashboard HTML en tiempo real")
        logger.info("   ✅ Registro automático de trades")
        logger.info("="*80)
        
        self._notify("🚀 Bot + Dashboard iniciado\n✅ Analizando pares\n📊 Dashboard: dashboard.html")
    
    def _notify(self, msg: str):
        """Enviar notificación Telegram"""
        try:
            if not self.telegram_token or not self.chat_id:
                return
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {'chat_id': self.chat_id, 'text': msg, 'parse_mode': 'HTML'}
            requests.post(url, json=data, timeout=5)
        except:
            pass
    
    def get_price_data(self, symbol: str) -> dict:
        """Obtener datos de precio"""
        try:
            url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0 and data.get('data'):
                    ticker = data['data']
                    price = float(ticker.get('lastPrice', 0))
                    
                    if price > 0:
                        return {
                            'symbol': symbol,
                            'price': price,
                            'high': float(ticker.get('highPrice', 0)),
                            'low': float(ticker.get('lowPrice', 0)),
                            'change': float(ticker.get('priceChangePercent', 0)),
                            'volume': float(ticker.get('volume', 0)),
                            'status': 'OK'
                        }
            
            return {'symbol': symbol, 'status': 'ERROR'}
        except:
            return {'symbol': symbol, 'status': 'ERROR'}
    
    def analyze_direction(self, symbol: str, data: dict) -> dict:
        """Analizar dirección"""
        try:
            change = data.get('change', 0)
            
            if change > 2:
                direction = 'LONG'
                score = min(90, 50 + abs(change))
            elif change < -2:
                direction = 'SHORT'
                score = min(90, 50 + abs(change))
            else:
                direction = 'NEUTRAL'
                score = 50 + abs(change)
            
            return {
                'direction': direction,
                'score': score,
                'change': change,
                'strength': 'FUERTE' if score > 70 else ('MEDIA' if score > 50 else 'DÉBIL')
            }
        except:
            return {'direction': 'NEUTRAL', 'score': 0, 'change': 0, 'strength': 'ERROR'}
    
    def detect_zones(self, symbol: str, data: dict) -> dict:
        """Detectar zonas"""
        try:
            price = data.get('price', 0)
            high = data.get('high', 0)
            low = data.get('low', 0)
            
            if price <= 0:
                return {'support': 0, 'resistance': 0, 'current_price': 0, 'status': 'ERROR'}
            
            range_price = high - low
            support = price - (range_price * 0.5)
            resistance = price + (range_price * 0.5)
            
            support = min(support, price * 0.95)
            resistance = max(resistance, price * 1.05)
            
            return {
                'support': support,
                'resistance': resistance,
                'current_price': price,
                'high': high,
                'low': low,
                'status': 'OK'
            }
        except:
            return {'support': 0, 'resistance': 0, 'current_price': 0, 'status': 'ERROR'}
    
    def get_confirmation(self, data: dict) -> dict:
        """Obtener confirmación"""
        try:
            change = data.get('change', 0)
            volume = data.get('volume', 0)
            
            confirmations = 0
            
            if abs(change) > 0.5:
                confirmations += 1
            if volume > 0:
                confirmations += 1
            if change > 1 or change < -1:
                confirmations += 1
            
            score = (confirmations / 3) * 100
            
            return {
                'confirmations': confirmations,
                'total': 3,
                'score': score,
                'strength': 'EXCELENTE' if score > 70 else ('BUENA' if score > 50 else 'DÉBIL')
            }
        except:
            return {'confirmations': 0, 'total': 3, 'score': 0, 'strength': 'ERROR'}
    
    def should_enter_long(self, direction: dict, zones: dict, confirmation: dict) -> bool:
        try:
            rule1 = direction['direction'] == 'LONG'
            rule2 = direction['score'] > 60
            rule3 = zones['current_price'] > zones['support']
            rule4 = confirmation['confirmations'] >= 2
            return rule1 and rule2 and rule3 and rule4
        except:
            return False
    
    def should_enter_short(self, direction: dict, zones: dict, confirmation: dict) -> bool:
        try:
            rule1 = direction['direction'] == 'SHORT'
            rule2 = direction['score'] > 60
            rule3 = zones['current_price'] < zones['resistance']
            rule4 = confirmation['confirmations'] >= 2
            return rule1 and rule2 and rule3 and rule4
        except:
            return False
    
    def calculate_targets(self, entry_price: float, direction: str, zones: dict) -> dict:
        try:
            if direction == 'LONG':
                resistance = zones['resistance']
                support = zones['support']
                distance = abs(resistance - support)
                
                tp1 = entry_price + (distance * 0.3)
                tp2 = entry_price + (distance * 0.6)
                sl = entry_price - (distance * 0.2)
                
            else:
                resistance = zones['resistance']
                support = zones['support']
                distance = abs(resistance - support)
                
                tp1 = entry_price - (distance * 0.3)
                tp2 = entry_price - (distance * 0.6)
                sl = entry_price + (distance * 0.2)
            
            return {
                'tp1': tp1,
                'tp2': tp2,
                'sl': sl,
                'distance': abs(tp1 - entry_price)
            }
        except:
            return {'tp1': 0, 'tp2': 0, 'sl': 0, 'distance': 0}
    
    def generate_dashboard_html(self):
        """Generar HTML del dashboard"""
        stats = self.dashboard.stats
        trades = stats['trades']
        pnl = stats['pnl']
        signals = stats['signals']
        
        win_rate = (trades['wins'] / trades['total'] * 100) if trades['total'] > 0 else 0
        
        # HTML simplificado (versión completa en dashboard.py)
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Dashboard Bot Trading</title>
    <style>
        body {{ font-family: Arial; background: #1a1a2e; color: #fff; margin: 20px; }}
        .header {{ text-align: center; margin-bottom: 30px; }}
        h1 {{ color: #00ff88; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }}
        .card {{ background: #16213e; border: 2px solid #00ff88; padding: 20px; border-radius: 10px; }}
        .card h3 {{ color: #00ff88; margin-top: 0; }}
        .value {{ font-size: 2em; font-weight: bold; margin: 10px 0; }}
        .positive {{ color: #00ff88; }}
        .negative {{ color: #ff4466; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; background: #16213e; border: 2px solid #00ff88; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #00ff88; }}
        th {{ background: #0f3460; color: #00ff88; }}
        .info-box {{ background: #16213e; border: 2px solid #00ccff; padding: 15px; border-radius: 10px; margin-top: 20px; }}
        .footer {{ text-align: center; margin-top: 30px; color: #888; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 Bot Trading Dashboard</h1>
        <p>Monitoreo en tiempo real - Actualizado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
    
    <div class="grid">
        <div class="card">
            <h3>💰 Capital</h3>
            <div class="value">${stats['capital_actual']:.2f}</div>
            <p>Inicial: ${stats['capital_inicial']:.2f}</p>
        </div>
        
        <div class="card">
            <h3>📈 Ganancia</h3>
            <div class="value {('positive' if pnl['total_usdt'] >= 0 else 'negative')}">${pnl['total_usdt']:+.2f}</div>
            <p>{pnl['total_percent']:+.2f}% ROI</p>
        </div>
        
        <div class="card">
            <h3>🎯 Win Rate</h3>
            <div class="value {('positive' if win_rate >= 50 else 'negative')}">{win_rate:.1f}%</div>
            <p>✅ {trades['wins']} | ❌ {trades['losses']}</p>
        </div>
        
        <div class="card">
            <h3>📊 Trades</h3>
            <div class="value">{trades['total']}</div>
            <p>⏳ {trades['pending']} Pendientes</p>
        </div>
        
        <div class="card">
            <h3>🟢 LONG Signals</h3>
            <div class="value positive">{signals['long_total']}</div>
            <p>Hoy: {signals['long_today']}</p>
        </div>
        
        <div class="card">
            <h3>🔴 SHORT Signals</h3>
            <div class="value negative">{signals['short_total']}</div>
            <p>Hoy: {signals['short_today']}</p>
        </div>
    </div>
    
    <div class="info-box">
        <h3>📌 Últimas Señales</h3>
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Símbolo</th>
                    <th>Dirección</th>
                    <th>Entry</th>
                    <th>TP1</th>
                    <th>TP2</th>
                    <th>SL</th>
                    <th>Estado</th>
                </tr>
            </thead>
            <tbody>
"""
        
        for signal in stats['historial'][-15:]:
            direction = '🟢 LONG' if signal['direction'] == 'LONG' else '🔴 SHORT'
            html += f"""
                <tr>
                    <td>#{signal['id']}</td>
                    <td>{signal['symbol']}</td>
                    <td>{direction}</td>
                    <td>${signal['entry']:.4f}</td>
                    <td>${signal['tp1']:.4f}</td>
                    <td>${signal['tp2']:.4f}</td>
                    <td>${signal['sl']:.4f}</td>
                    <td>{signal['status']}</td>
                </tr>
"""
        
        html += """
            </tbody>
        </table>
    </div>
    
    <div class="footer">
        <p>🚀 Bot Profesional v2 | Dashboard en tiempo real</p>
        <p>Recarga la página para actualizar datos</p>
    </div>
</body>
</html>
"""
        return html
    
    def save_dashboard(self):
        """Guardar dashboard HTML"""
        html = self.generate_dashboard_html()
        try:
            with open('dashboard.html', 'w', encoding='utf-8') as f:
                f.write(html)
        except:
            pass
    
    async def run(self):
        """Loop principal"""
        logger.info("\n🚀 Bot iniciado...\n")
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"⏱️ ITERACIÓN #{iteration} - {datetime.now().strftime('%H:%M:%S')}")
                logger.info(f"{'='*80}\n")
                
                long_signals = []
                short_signals = []
                analyzed = 0
                
                logger.info(f"📊 Analizando {len(self.symbols)} pares...\n")
                
                for i, symbol in enumerate(self.symbols, 1):
                    try:
                        data = self.get_price_data(symbol)
                        if data['status'] != 'OK':
                            continue
                        
                        analyzed += 1
                        
                        direction = self.analyze_direction(symbol, data)
                        zones = self.detect_zones(symbol, data)
                        confirmation = self.get_confirmation(data)
                        
                        if zones['status'] != 'OK':
                            continue
                        
                        long_ok = self.should_enter_long(direction, zones, confirmation)
                        short_ok = self.should_enter_short(direction, zones, confirmation)
                        
                        if long_ok:
                            targets = self.calculate_targets(zones['current_price'], 'LONG', zones)
                            logger.info(f"{i:2d}. 🟢 {symbol:12} ${zones['current_price']:.4f} LONG")
                            
                            long_signals.append(symbol)
                            self.dashboard.add_signal(symbol, 'LONG', zones['current_price'], targets['tp1'], targets['tp2'], targets['sl'])
                            
                            msg = f"🟢 <b>LONG</b>\n{symbol}\n💰 ${zones['current_price']:.4f}\n"
                            msg += f"🎯 TP1: ${targets['tp1']:.4f} | TP2: ${targets['tp2']:.4f}\n"
                            msg += f"🛑 SL: ${targets['sl']:.4f}"
                            self._notify(msg)
                        
                        elif short_ok:
                            targets = self.calculate_targets(zones['current_price'], 'SHORT', zones)
                            logger.info(f"{i:2d}. 🔴 {symbol:12} ${zones['current_price']:.4f} SHORT")
                            
                            short_signals.append(symbol)
                            self.dashboard.add_signal(symbol, 'SHORT', zones['current_price'], targets['tp1'], targets['tp2'], targets['sl'])
                            
                            msg = f"🔴 <b>SHORT</b>\n{symbol}\n💰 ${zones['current_price']:.4f}\n"
                            msg += f"🎯 TP1: ${targets['tp1']:.4f} | TP2: ${targets['tp2']:.4f}\n"
                            msg += f"🛑 SL: ${targets['sl']:.4f}"
                            self._notify(msg)
                        
                        else:
                            logger.info(f"{i:2d}. ⚪ {symbol:12} ${zones['current_price']:.4f} - {direction['direction']}")
                    
                    except Exception as e:
                        logger.debug(f"Error analizando {symbol}: {str(e)[:40]}")
                    
                    await asyncio.sleep(0.05)
                
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 RESUMEN #{iteration}:")
                logger.info(f"   ✅ Analizados: {analyzed}/{len(self.symbols)}")
                logger.info(f"   🟢 LONG: {len(long_signals)}")
                logger.info(f"   🔴 SHORT: {len(short_signals)}")
                logger.info(f"{'='*80}")
                
                # Guardar dashboard
                self.save_dashboard()
                
                # Resumen cada 20 iteraciones
                if iteration % 20 == 0:
                    stats = self.dashboard.stats
                    trades = stats['trades']
                    msg = f"📊 <b>Resumen #{iteration}</b>\n"
                    msg += f"🟢 LONG: {len(long_signals)} | 🔴 SHORT: {len(short_signals)}\n"
                    msg += f"📈 Total trades: {trades['total']}\n"
                    msg += f"💰 Profit: ${stats['pnl']['total_usdt']:+.2f}"
                    self._notify(msg)
                
                logger.info(f"\n⏱️ Próximo en {self.interval}s...\n")
                await asyncio.sleep(self.interval)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Bot detenido")
                break
            except Exception as e:
                logger.error(f"\n❌ Error: {e}")
                await asyncio.sleep(10)


async def main():
    try:
        bot = BotProfesionalConDashboard()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot terminado")
