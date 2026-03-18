#!/usr/bin/env python3
"""
Dashboard Profesional - Monitoreo en Tiempo Real
Visualiza rentabilidad, signals, estadísticas del bot
"""

import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s'
)
logger = logging.getLogger(__name__)
load_dotenv()


class DashboardBot:
    """Dashboard profesional de monitoreo"""
    
    def __init__(self):
        """Inicializar dashboard"""
        self.data_file = 'bot_stats.json'
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        self.load_or_create_stats()
    
    def load_or_create_stats(self):
        """Cargar o crear archivo de estadísticas"""
        if Path(self.data_file).exists():
            try:
                with open(self.data_file, 'r') as f:
                    self.stats = json.load(f)
            except:
                self.stats = self.create_empty_stats()
        else:
            self.stats = self.create_empty_stats()
    
    def create_empty_stats(self):
        """Crear estructura vacía de estadísticas"""
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
                'total_percent': 0.0,
                'daily': []
            },
            'signals': {
                'long_total': 0,
                'short_total': 0,
                'long_today': 0,
                'short_today': 0
            },
            'historial_trades': []
        }
    
    def save_stats(self):
        """Guardar estadísticas"""
        try:
            with open(self.data_file, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            logger.error(f"Error guardando stats: {e}")
    
    def add_trade(self, symbol, direction, entry_price, tp1, tp2, sl, timestamp=None):
        """Registrar un trade"""
        if not timestamp:
            timestamp = datetime.now().isoformat()
        
        trade = {
            'id': len(self.stats['historial_trades']) + 1,
            'symbol': symbol,
            'direction': direction,
            'entry_price': entry_price,
            'tp1': tp1,
            'tp2': tp2,
            'sl': sl,
            'timestamp': timestamp,
            'status': 'OPEN',
            'exit_price': None,
            'pnl_usdt': 0.0,
            'pnl_percent': 0.0
        }
        
        self.stats['historial_trades'].append(trade)
        self.stats['trades']['total'] += 1
        self.stats['trades']['pending'] += 1
        
        if direction == 'LONG':
            self.stats['signals']['long_total'] += 1
            self.stats['signals']['long_today'] += 1
        else:
            self.stats['signals']['short_total'] += 1
            self.stats['signals']['short_today'] += 1
        
        self.save_stats()
        return trade
    
    def close_trade(self, trade_id, exit_price, pnl_usdt):
        """Cerrar un trade"""
        for trade in self.stats['historial_trades']:
            if trade['id'] == trade_id:
                trade['status'] = 'CLOSED'
                trade['exit_price'] = exit_price
                trade['pnl_usdt'] = pnl_usdt
                trade['pnl_percent'] = (pnl_usdt / (trade['entry_price'] * 100)) * 100
                
                self.stats['trades']['pending'] -= 1
                if pnl_usdt > 0:
                    self.stats['trades']['wins'] += 1
                else:
                    self.stats['trades']['losses'] += 1
                
                self.stats['pnl']['total_usdt'] += pnl_usdt
                break
        
        self.save_stats()
    
    def set_capital(self, initial, current=None):
        """Establecer capital"""
        self.stats['capital_inicial'] = initial
        if current:
            self.stats['capital_actual'] = current
        else:
            self.stats['capital_actual'] = initial
        
        if initial > 0:
            self.stats['pnl']['total_percent'] = ((current - initial) / initial) * 100
        
        self.save_stats()
    
    def get_dashboard_html(self):
        """Generar HTML del dashboard"""
        html = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard Bot Trading</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1e1e2e 0%, #2d2d44 100%);
            color: #fff;
            padding: 20px;
            min-height: 100vh;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        .header {
            text-align: center;
            margin-bottom: 40px;
        }
        
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
            background: linear-gradient(135deg, #00ff88 0%, #00ccff 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .header p {
            font-size: 1.1em;
            color: #888;
        }
        
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .card {
            background: linear-gradient(135deg, rgba(0, 255, 136, 0.1) 0%, rgba(0, 204, 255, 0.1) 100%);
            border: 2px solid rgba(0, 255, 136, 0.3);
            border-radius: 15px;
            padding: 25px;
            backdrop-filter: blur(10px);
            transition: all 0.3s ease;
        }
        
        .card:hover {
            transform: translateY(-5px);
            border-color: rgba(0, 255, 136, 0.6);
            box-shadow: 0 10px 30px rgba(0, 255, 136, 0.2);
        }
        
        .card-title {
            font-size: 0.9em;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 10px;
        }
        
        .card-value {
            font-size: 2.5em;
            font-weight: bold;
            margin-bottom: 5px;
        }
        
        .card-subtitle {
            font-size: 0.85em;
            color: #666;
        }
        
        .positive {
            color: #00ff88;
        }
        
        .negative {
            color: #ff4466;
        }
        
        .neutral {
            color: #00ccff;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }
        
        .stat-box {
            background: rgba(0, 0, 0, 0.3);
            border-left: 4px solid #00ff88;
            padding: 15px;
            border-radius: 8px;
        }
        
        .stat-box.loss {
            border-left-color: #ff4466;
        }
        
        .stat-box.pending {
            border-left-color: #ffaa00;
        }
        
        .stat-label {
            font-size: 0.8em;
            color: #888;
            margin-bottom: 5px;
        }
        
        .stat-value {
            font-size: 1.8em;
            font-weight: bold;
        }
        
        .table-container {
            background: linear-gradient(135deg, rgba(0, 255, 136, 0.1) 0%, rgba(0, 204, 255, 0.1) 100%);
            border: 2px solid rgba(0, 255, 136, 0.3);
            border-radius: 15px;
            padding: 25px;
            backdrop-filter: blur(10px);
            margin-bottom: 30px;
            overflow-x: auto;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
        }
        
        th {
            background: rgba(0, 255, 136, 0.1);
            padding: 15px;
            text-align: left;
            border-bottom: 2px solid rgba(0, 255, 136, 0.3);
            font-size: 0.9em;
            text-transform: uppercase;
            color: #00ff88;
        }
        
        td {
            padding: 15px;
            border-bottom: 1px solid rgba(0, 255, 136, 0.1);
        }
        
        tr:hover {
            background: rgba(0, 255, 136, 0.05);
        }
        
        .status-open {
            color: #ffaa00;
            font-weight: bold;
        }
        
        .status-closed {
            color: #00ff88;
            font-weight: bold;
        }
        
        .footer {
            text-align: center;
            margin-top: 40px;
            color: #666;
            font-size: 0.9em;
        }
        
        .progress-bar {
            width: 100%;
            height: 8px;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 10px;
            overflow: hidden;
            margin-top: 10px;
        }
        
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #00ff88 0%, #00ccff 100%);
            border-radius: 10px;
            transition: width 0.3s ease;
        }
        
        .badge {
            display: inline-block;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.8em;
            font-weight: bold;
            margin-right: 5px;
        }
        
        .badge-long {
            background: rgba(0, 255, 136, 0.2);
            color: #00ff88;
            border: 1px solid #00ff88;
        }
        
        .badge-short {
            background: rgba(255, 68, 102, 0.2);
            color: #ff4466;
            border: 1px solid #ff4466;
        }
        
        .info-box {
            background: rgba(0, 204, 255, 0.1);
            border: 1px solid #00ccff;
            border-radius: 10px;
            padding: 15px;
            margin-top: 20px;
            font-size: 0.9em;
            line-height: 1.6;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Bot Trading Dashboard</h1>
            <p>Monitoreo en tiempo real de rentabilidad</p>
        </div>
        
        <div class="grid">
            <!-- Capital -->
            <div class="card">
                <div class="card-title">💰 Capital Actual</div>
                <div class="card-value neutral">${capital_actual:.2f}</div>
                <div class="card-subtitle">Inicial: ${capital_inicial:.2f}</div>
            </div>
            
            <!-- PnL -->
            <div class="card">
                <div class="card-title">📈 Ganancia Total</div>
                <div class="card-value {pnl_color}">${pnl_total:.2f}</div>
                <div class="card-subtitle">{pnl_percent:.2f}% ROI</div>
            </div>
            
            <!-- Win Rate -->
            <div class="card">
                <div class="card-title">🎯 Win Rate</div>
                <div class="card-value {wr_color}">{win_rate:.1f}%</div>
                <div class="card-subtitle">{wins} Ganancias / {losses} Pérdidas</div>
            </div>
            
            <!-- Trades Totales -->
            <div class="card">
                <div class="card-title">📊 Trades Totales</div>
                <div class="card-value">{total_trades}</div>
                <div class="card-subtitle">{pending} Pendientes</div>
            </div>
            
            <!-- Signals LONG -->
            <div class="card">
                <div class="card-title">🟢 Signals LONG</div>
                <div class="card-value positive">{long_signals}</div>
                <div class="card-subtitle">Hoy: {long_today}</div>
            </div>
            
            <!-- Signals SHORT -->
            <div class="card">
                <div class="card-title">🔴 Signals SHORT</div>
                <div class="card-value negative">{short_signals}</div>
                <div class="card-subtitle">Hoy: {short_today}</div>
            </div>
        </div>
        
        <!-- Estadísticas Detalladas -->
        <div class="card">
            <div class="card-title">📉 Estadísticas Detalladas</div>
            <div class="stats-grid">
                <div class="stat-box">
                    <div class="stat-label">Trades Ganadores</div>
                    <div class="stat-value positive">{wins}</div>
                </div>
                <div class="stat-box loss">
                    <div class="stat-label">Trades Perdedores</div>
                    <div class="stat-value negative">{losses}</div>
                </div>
                <div class="stat-box pending">
                    <div class="stat-label">Trades Pendientes</div>
                    <div class="stat-value">{pending}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Promedio PnL/Trade</div>
                    <div class="stat-value {avg_color}">${avg_pnl:.2f}</div>
                </div>
            </div>
        </div>
        
        <!-- Historial de Trades -->
        <div class="table-container">
            <h3 style="margin-bottom: 20px;">Historial de Trades</h3>
            <table>
                <thead>
                    <tr>
                        <th>#ID</th>
                        <th>Símbolo</th>
                        <th>Dirección</th>
                        <th>Entry</th>
                        <th>Status</th>
                        <th>PnL</th>
                        <th>%</th>
                        <th>Fecha</th>
                    </tr>
                </thead>
                <tbody>
                    {trades_html}
                </tbody>
            </table>
        </div>
        
        <!-- Info Box -->
        <div class="info-box">
            <strong>📌 Información:</strong>
            <ul style="margin-left: 20px; margin-top: 10px;">
                <li>Dashboard se actualiza automáticamente cada 5 minutos</li>
                <li>Los trades se registran automáticamente desde el bot</li>
                <li>ROI = (Capital Actual - Capital Inicial) / Capital Inicial * 100</li>
                <li>Win Rate = Ganancias / Total Trades * 100</li>
            </ul>
        </div>
        
        <div class="footer">
            <p>🚀 Bot Trading Profesional v2 | Actualizado: {timestamp}</p>
            <p>Dashboard © 2026 - Monitoreo en Tiempo Real</p>
        </div>
    </div>
</body>
</html>
"""
        
        # Calcular valores
        capital_actual = self.stats['capital_actual']
        capital_inicial = self.stats['capital_inicial']
        pnl_total = self.stats['pnl']['total_usdt']
        pnl_percent = self.stats['pnl']['total_percent']
        
        wins = self.stats['trades']['wins']
        losses = self.stats['trades']['losses']
        total_trades = self.stats['trades']['total']
        pending = self.stats['trades']['pending']
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        long_signals = self.stats['signals']['long_total']
        short_signals = self.stats['signals']['short_total']
        long_today = self.stats['signals']['long_today']
        short_today = self.stats['signals']['short_today']
        
        avg_pnl = (pnl_total / total_trades) if total_trades > 0 else 0
        
        # Colores
        pnl_color = 'positive' if pnl_total >= 0 else 'negative'
        wr_color = 'positive' if win_rate >= 50 else 'negative'
        avg_color = 'positive' if avg_pnl >= 0 else 'negative'
        
        # Historial de trades
        trades_html = ""
        for trade in self.stats['historial_trades'][-20:]:  # Últimos 20
            status_class = 'status-open' if trade['status'] == 'OPEN' else 'status-closed'
            direction_badge = f"<span class='badge badge-long'>LONG</span>" if trade['direction'] == 'LONG' else f"<span class='badge badge-short'>SHORT</span>"
            
            trades_html += f"""
                <tr>
                    <td>#{trade['id']}</td>
                    <td>{trade['symbol']}</td>
                    <td>{direction_badge}</td>
                    <td>${trade['entry_price']:.4f}</td>
                    <td><span class="{status_class}">{trade['status']}</span></td>
                    <td>${trade['pnl_usdt']:.2f}</td>
                    <td>{trade['pnl_percent']:.2f}%</td>
                    <td>{trade['timestamp'][:10]}</td>
                </tr>
            """
        
        # Reemplazar valores
        html = html.format(
            capital_actual=capital_actual,
            capital_inicial=capital_inicial,
            pnl_total=pnl_total,
            pnl_percent=pnl_percent,
            pnl_color=pnl_color,
            win_rate=win_rate,
            wr_color=wr_color,
            wins=wins,
            losses=losses,
            total_trades=total_trades,
            pending=pending,
            long_signals=long_signals,
            short_signals=short_signals,
            long_today=long_today,
            short_today=short_today,
            avg_pnl=avg_pnl,
            avg_color=avg_color,
            trades_html=trades_html if trades_html else '<tr><td colspan="8" style="text-align:center; color:#888;">Sin trades registrados</td></tr>',
            timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
        
        return html
    
    def save_dashboard(self, filename='dashboard.html'):
        """Guardar dashboard en archivo HTML"""
        html = self.get_dashboard_html()
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(html)
            logger.info(f"✅ Dashboard guardado en {filename}")
            return filename
        except Exception as e:
            logger.error(f"❌ Error guardando dashboard: {e}")
            return None
    
    def get_summary_telegram(self):
        """Generar resumen para Telegram"""
        trades = self.stats['trades']
        pnl = self.stats['pnl']
        signals = self.stats['signals']
        
        win_rate = (trades['wins'] / trades['total'] * 100) if trades['total'] > 0 else 0
        
        msg = f"""
📊 <b>RESUMEN BOT TRADING</b>

💰 <b>Capital:</b> ${self.stats['capital_actual']:.2f}
📈 <b>Ganancia:</b> ${pnl['total_usdt']:+.2f} ({pnl['total_percent']:+.2f}%)

📊 <b>Trades:</b> {trades['total']} total
   ✅ Ganancias: {trades['wins']}
   ❌ Pérdidas: {trades['losses']}
   ⏳ Pendientes: {trades['pending']}

🎯 <b>Win Rate:</b> {win_rate:.1f}%

🟢 <b>LONG:</b> {signals['long_total']} total ({signals['long_today']} hoy)
🔴 <b>SHORT:</b> {signals['short_total']} total ({signals['short_today']} hoy)

🚀 Bot funcionando perfectamente
"""
        return msg


# Script para usar el dashboard
if __name__ == "__main__":
    dashboard = DashboardBot()
    
    # Ejemplo: Establecer capital inicial
    dashboard.set_capital(initial=1000, current=1250)
    
    # Ejemplo: Registrar algunos trades
    dashboard.add_trade('BTC-USDT', 'LONG', 45000, 45500, 46000, 44500)
    dashboard.add_trade('ETH-USDT', 'SHORT', 2500, 2475, 2450, 2525)
    dashboard.add_trade('SOL-USDT', 'LONG', 100, 102, 105, 99)
    
    # Cerrar trades
    dashboard.close_trade(1, 45600, 600)
    dashboard.close_trade(2, 2480, 200)
    dashboard.close_trade(3, 104, 400)
    
    # Generar y guardar dashboard
    dashboard.save_dashboard()
    
    # Mostrar resumen para Telegram
    print(dashboard.get_summary_telegram())
    
    logger.info("✅ Dashboard listo - Abre dashboard.html en tu navegador")
