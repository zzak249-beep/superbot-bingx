#!/usr/bin/env python3
"""
Bot Trading Profesional v2 CON DASHBOARD Y AUTO-TRADING REAL
Análisis direccional + Registro automático de trades + Dashboard HTML + Ejecución en BingX
"""

import os
import asyncio
import logging
import requests
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# IMPORTACIÓN DEL MÓDULO DE BINGX AÑADIDA
from bingx_autotrader import BingXAutoTrader

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
            'direction
