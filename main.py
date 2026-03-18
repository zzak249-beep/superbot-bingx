"""
Configuración centralizada del bot de trading
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _clean_value(value):
    """Limpiar valor removiendo comillas"""
    if isinstance(value, str):
        return value.strip('"').strip("'")
    return value


def _get_float(key, default):
    """Obtener float limpiando comillas"""
    value = os.getenv(key, str(default))
    value = _clean_value(value)
    return float(value)


def _get_int(key, default):
    """Obtener int limpiando comillas"""
    value = os.getenv(key, str(default))
    value = _clean_value(value)
    return int(value)


def _get_bool(key, default):
    """Obtener bool"""
    value = os.getenv(key, str(default))
    value = _clean_value(value)
    return value.lower() == 'true'


class Config:
    """Configuración global del bot"""
    
    # ========== CREDENCIALES ==========
    BINGX_API_KEY = os.getenv('BINGX_API_KEY', '')
    BINGX_API_SECRET = os.getenv('BINGX_API_SECRET', '')
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
    
    # ========== TRADING PARAMETERS ==========
    AUTO_TRADING_ENABLED = _get_bool('AUTO_TRADING_ENABLED', 'false')
    MAX_POSITION_SIZE = _get_float('MAX_POSITION_SIZE', '100')
    LEVERAGE = _get_int('LEVERAGE', '2')
    
    # ========== RISK MANAGEMENT ==========
    TAKE_PROFIT_PCT = _get_float('TAKE_PROFIT_PCT', '2.0')
    STOP_LOSS_PCT = _get_float('STOP_LOSS_PCT', '1.0')
    TRAILING_STOP_ENABLED = _get_bool('TRAILING_STOP_ENABLED', 'true')
    TRAILING_STOP_ACTIVATION = _get_float('TRAILING_STOP_ACTIVATION', '1.0')
    TRAILING_STOP_DISTANCE = _get_float('TRAILING_STOP_DISTANCE', '0.5')
    
    MAX_OPEN_TRADES = _get_int('MAX_OPEN_TRADES', '5')
    MAX_DAILY_LOSS = _get_float('MAX_DAILY_LOSS', '500')
    MAX_DRAWDOWN_PCT = _get_float('MAX_DRAWDOWN_PCT', '10')
    
    # ========== MARKET SCANNING ==========
    MIN_VOLUME_24H = _get_float('MIN_VOLUME_24H', '1000000')
    MIN_PRICE = _get_float('MIN_PRICE', '0.0001')
    MAX_SYMBOLS_TO_TRADE = _get_int('MAX_SYMBOLS_TO_TRADE', '50')
    
    # ========== ML/AI SETTINGS ==========
    ML_ENABLED = _get_bool('ML_ENABLED', 'true')
    ML_CONFIDENCE_THRESHOLD = _get_float('ML_CONFIDENCE_THRESHOLD', '0.65')
    ML_RETRAIN_INTERVAL = _get_int('ML_RETRAIN_INTERVAL', '3600')
    
    # ========== TECHNICAL ANALYSIS ==========
    RSI_PERIOD = _get_int('RSI_PERIOD', '14')
    RSI_OVERBOUGHT = _get_int('RSI_OVERBOUGHT', '70')
    RSI_OVERSOLD = _get_int('RSI_OVERSOLD', '30')
    
    MACD_FAST = _get_int('MACD_FAST', '12')
    MACD_SLOW = _get_int('MACD_SLOW', '26')
    MACD_SIGNAL = _get_int('MACD_SIGNAL', '9')
    
    BB_PERIOD = _get_int('BB_PERIOD', '20')
    BB_STD_DEV = _get_float('BB_STD_DEV', '2.0')
    
    # ========== TIMEFRAMES ==========
    TIMEFRAMES = ['1m', '5m', '15m', '1h']
    PRIMARY_TIMEFRAME = os.getenv('PRIMARY_TIMEFRAME', '5m')
    
    # ========== INTERVALS ==========
    CHECK_INTERVAL = _get_int('CHECK_INTERVAL', '60')
    MARKET_SCAN_INTERVAL = _get_int('MARKET_SCAN_INTERVAL', '300')
    
    # ========== DATABASE ==========
    DB_PATH = os.getenv('DB_PATH', 'trading_bot.db')
    
    # ========== DASHBOARD ==========
    DASHBOARD_ENABLED = _get_bool('DASHBOARD_ENABLED', 'true')
    DASHBOARD_PORT = _get_int('DASHBOARD_PORT', '8080')
    DASHBOARD_UPDATE_INTERVAL = _get_int('DASHBOARD_UPDATE_INTERVAL', '10')
    
    # ========== LOGGING ==========
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', 'trading_bot.log')
    
    # ========== BINGX API ==========
    BASE_URL = "https://open-api.bingx.com"
    
    @classmethod
    def validate(cls):
        """Validar configuración"""
        errors = []
        
        if cls.AUTO_TRADING_ENABLED:
            if not cls.BINGX_API_KEY:
                errors.append("BINGX_API_KEY no configurada")
            if not cls.BINGX_API_SECRET:
                errors.append("BINGX_API_SECRET no configurada")
        
        if cls.MAX_POSITION_SIZE <= 0:
            errors.append("MAX_POSITION_SIZE debe ser > 0")
        
        if cls.TAKE_PROFIT_PCT <= 0 or cls.STOP_LOSS_PCT <= 0:
            errors.append("TP y SL deben ser > 0")
        
        return errors
    
    @classmethod
    def get_summary(cls):
        """Resumen de configuración"""
        return f"""
╔══════════════════════════════════════════════════════════════╗
║           🤖 CONFIGURACIÓN DEL BOT DE TRADING 🤖            ║
╠══════════════════════════════════════════════════════════════╣
║ TRADING                                                      ║
║  • Auto-Trading: {'✅ ACTIVADO' if cls.AUTO_TRADING_ENABLED else '❌ DESACTIVADO'}                              ║
║  • Position Size: ${cls.MAX_POSITION_SIZE}                   ║
║  • Leverage: {cls.LEVERAGE}x                                 ║
║  • Max Trades: {cls.MAX_OPEN_TRADES}                         ║
║                                                              ║
║ RISK MANAGEMENT                                              ║
║  • Take Profit: {cls.TAKE_PROFIT_PCT}%                       ║
║  • Stop Loss: {cls.STOP_LOSS_PCT}%                           ║
║  • Trailing Stop: {'✅ ON' if cls.TRAILING_STOP_ENABLED else '❌ OFF'}                                ║
║  • Max Daily Loss: ${cls.MAX_DAILY_LOSS}                     ║
║  • Max Drawdown: {cls.MAX_DRAWDOWN_PCT}%                     ║
║                                                              ║
║ ML/AI                                                        ║
║  • ML Enabled: {'✅ ON' if cls.ML_ENABLED else '❌ OFF'}                                    ║
║  • Confidence: {cls.ML_CONFIDENCE_THRESHOLD}                 ║
║  • Retrain: {cls.ML_RETRAIN_INTERVAL}s                       ║
║                                                              ║
║ MARKET SCANNING                                              ║
║  • Min Volume 24h: ${cls.MIN_VOLUME_24H:,.0f}                ║
║  • Max Symbols: {cls.MAX_SYMBOLS_TO_TRADE}                   ║
║  • Check Interval: {cls.CHECK_INTERVAL}s                     ║
║                                                              ║
║ DASHBOARD                                                    ║
║  • Enabled: {'✅ ON' if cls.DASHBOARD_ENABLED else '❌ OFF'}                                    ║
║  • Port: {cls.DASHBOARD_PORT}                                ║
╚══════════════════════════════════════════════════════════════╝
"""
