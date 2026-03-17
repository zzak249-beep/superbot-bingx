"""
Notificador Telegram para alertas del bot
"""

import os
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Cliente para enviar notificaciones a Telegram"""
    
    def __init__(self):
        """Inicializar con credenciales"""
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        self.enabled = bool(self.bot_token and self.chat_id)
        
        if not self.enabled:
            logger.warning("⚠️ Telegram deshabilitado")
        else:
            logger.info("✅ Telegram configurado")
    
    def send_message(self, message: str, parse_mode: str = 'HTML') -> bool:
        """Enviar mensaje a Telegram"""
        if not self.enabled:
            logger.info(f"📱 [Telegram] {message}")
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': parse_mode,
                'disable_web_page_preview': True
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                logger.debug("✅ Mensaje enviado")
                return True
            else:
                logger.error(f"❌ Error: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error Telegram: {e}")
            return False
