"""
Scanner v5 — Escaneo multi-timeframe con filtros de volumen
- Obtiene klines de 15m, 1h y 4h para cada símbolo
- Filtra símbolos por volumen mínimo (MIN_VOLUME_USDT)
- Devuelve señales ordenadas por score + tier
"""
import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from signals import Signal, generate_signal

log = logging.getLogger("Scanner")

# Pares a excluir (stablecoins, tokens de baja calidad)
EXCLUDE_SYMBOLS = {
    "USDCUSDT", "TUSDUSDT", "BUSDUSDT", "USDTUSDT",
    "FDUSDUSDT", "SUIUSDT",  # alta manipulación
}

# Top pares por liquidez para priorizar
PRIORITY_SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT",
    "XRP-USDT", "DOGE-USDT", "ADA-USDT", "AVAX-USDT",
    "LINK-USDT", "DOT-USDT", "MATIC-USDT", "LTC-USDT",
]


@dataclass
class ScanResult:
    symbol: str
    signal: Signal
    volume_24h: float


class Scanner:
    def __init__(self, client, max_symbols: int = 30, min_volume_usdt: float = 500_000):
        self.client = client
        self.max_symbols = max_symbols
        self.min_volume_usdt = min_volume_usdt
    
    def _get_top_symbols(self) -> List[str]:
        """Obtiene símbolos filtrados por volumen."""
        try:
            symbols_info = self.client.get_symbols()
            result = []
            
            for s in symbols_info:
                sym = s.get("symbol", "")
                if not sym or sym in EXCLUDE_SYMBOLS:
                    continue
                if not sym.endswith("-USDT"):
                    continue
                
                try:
                    ticker = self.client.get_ticker(sym)
                    vol_24h = float(ticker.get("quoteVolume", 0) or 0)
                    if vol_24h >= self.min_volume_usdt:
                        result.append((sym, vol_24h))
                except Exception:
                    pass
                
                time.sleep(0.05)  # respetar rate limit
            
            # Ordenar por volumen desc, priorizar los top pares
            result.sort(key=lambda x: x[1], reverse=True)
            
            # Poner priority symbols primero
            priority = [(s, v) for s, v in result if s.replace("-", "") in [p.replace("-", "") for p in PRIORITY_SYMBOLS]]
            others   = [(s, v) for s, v in result if s not in [p[0] for p in priority]]
            
            final = [s for s, v in (priority + others)[:self.max_symbols]]
            log.info(f"🔍 {len(final)} símbolos activos (vol > ${self.min_volume_usdt:,.0f})")
            return final
            
        except Exception as e:
            log.error(f"get_top_symbols error: {e}")
            return PRIORITY_SYMBOLS[:10]
    
    def _fetch_klines_multi_tf(self, symbol: str) -> tuple:
        """Obtiene klines de 15m, 1h y 4h."""
        try:
            klines_15m = self.client.get_klines(symbol, "15m", 200)
            time.sleep(0.1)
            klines_1h  = self.client.get_klines(symbol, "1h",  100)
            time.sleep(0.1)
            klines_4h  = self.client.get_klines(symbol, "4h",  60)
            return klines_15m, klines_1h, klines_4h
        except Exception as e:
            log.debug(f"Klines error {symbol}: {e}")
            return [], [], []
    
    def scan(self) -> List[ScanResult]:
        """Escanea el mercado y devuelve señales ordenadas."""
        symbols = self._get_top_symbols()
        results = []
        
        for symbol in symbols:
            try:
                # Klines multi-TF
                k15, k1h, k4h = self._fetch_klines_multi_tf(symbol)
                if not k15:
                    continue
                
                # Funding rate (filtro adicional)
                funding = self.client.get_funding_rate(symbol)
                
                # Volume 24h para logging
                try:
                    ticker = self.client.get_ticker(symbol)
                    vol_24h = float(ticker.get("quoteVolume", 0) or 0)
                except Exception:
                    vol_24h = 0.0
                
                # Generar señal
                signal = generate_signal(
                    symbol=symbol,
                    klines_15m=k15,
                    klines_1h=k1h,
                    klines_4h=k4h,
                    funding_rate=funding,
                    min_volume_usdt=self.min_volume_usdt,
                )
                
                if signal:
                    results.append(ScanResult(symbol, signal, vol_24h))
                    log.info(f"✅ Señal: {symbol} {signal.direction} score={signal.score:.0f} tier={signal.tier}")
                
                time.sleep(0.15)
                
            except Exception as e:
                log.debug(f"scan {symbol}: {e}")
        
        # Ordenar: tier A > B > C, luego por score
        tier_order = {"A": 0, "B": 1, "C": 2}
        results.sort(key=lambda r: (tier_order.get(r.signal.tier, 3), -r.signal.score))
        
        log.info(f"📊 Scan completo: {len(results)} señales de {len(symbols)} símbolos")
        return results
