"""
Kronos Signal Filter v1 — Integración limpia con walk-forward evaluation
- Usa Kronos-mini (4.1M params) para inferencia rápida en CPU/GPU
- Walk-forward testing SIN look-ahead bias
- Filtro: solo opera si Kronos confirma la dirección de la señal técnica
- Fallback graceful si Kronos no está instalado
"""
import logging
import time
from typing import Optional, Tuple
import numpy as np
import pandas as pd

log = logging.getLogger("Kronos")

# Intentar importar Kronos (puede no estar instalado)
KRONOS_AVAILABLE = False
try:
    import torch
    from transformers import AutoTokenizer
    # Kronos usa su propia clase KronosPredictor
    import sys, importlib
    _kronos_spec = importlib.util.find_spec("kronos")
    if _kronos_spec:
        from kronos import KronosPredictor, KronosModel
        KRONOS_AVAILABLE = True
        log.info("✅ Kronos disponible — filtro AI activado")
    else:
        log.info("ℹ️ Kronos no instalado — usando solo señales técnicas")
except ImportError:
    log.info("ℹ️ torch/transformers no disponibles — Kronos desactivado")


class KronosFilter:
    """
    Filtro de señal usando Kronos. Si Kronos no está disponible,
    devuelve confianza neutra (no bloquea el bot).
    
    IMPORTANTE — walk-forward limpio:
    - Solo se alimenta con velas PASADAS (x_timestamp < tiempo actual)
    - y_timestamp es el futuro que queremos predecir
    - Nunca hay overlap entre train y eval en pre-entrenamiento
    """
    
    MODEL_NAME = "NeoQuasar/Kronos-mini"  # 4.1M params, corre en CPU
    
    def __init__(self, model_name: str = None, device: str = "cpu"):
        self.model_name = model_name or self.MODEL_NAME
        self.device = device
        self.predictor = None
        self.model_loaded = False
        
        if KRONOS_AVAILABLE:
            self._load_model()
    
    def _load_model(self):
        try:
            log.info(f"⏳ Cargando {self.model_name}...")
            model = KronosModel.from_pretrained(self.model_name)
            tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self.predictor = KronosPredictor(
                model, tokenizer,
                device=self.device,
                max_context=512
            )
            self.model_loaded = True
            log.info(f"✅ Kronos-mini cargado en {self.device}")
        except Exception as e:
            log.warning(f"Kronos no pudo cargar: {e}. Bot continúa sin filtro AI.")
    
    def get_direction_confidence(
        self,
        klines: list,
        signal_direction: str,
        pred_len: int = 12,  # predecir 12 velas hacia adelante (3h en 15m)
    ) -> Tuple[float, str]:
        """
        Retorna (confianza_kronos, razon).
        
        confianza_kronos:
          - 1.0 = Kronos confirma fuertemente la dirección técnica
          - 0.5 = neutral (Kronos no disponible o señal ambigua)
          - 0.0 = Kronos contradice la dirección técnica
        
        WALK-FORWARD LIMPIO:
          - x_timestamp: velas históricas reales (pasado)
          - y_timestamp: siguiente período (futuro)
          - NO se usa ninguna vela "futura" real como input
        """
        if not self.model_loaded or not klines or len(klines) < 50:
            return 0.5, "kronos_unavailable"
        
        try:
            df, x_ts, y_ts = self._prepare_data(klines, pred_len)
            
            # Inferencia Kronos
            pred_df = self.predictor.predict(
                df=df,
                x_timestamp=x_ts,
                y_timestamp=y_ts,
                pred_len=pred_len,
                T=0.8,       # temperatura baja → más conservador
                top_p=0.9,
                sample_count=5,  # 5 paths → promedio más estable
            )
            
            # Calcular dirección predicha
            first_close = float(df["close"].iloc[-1])
            pred_close_avg = float(pred_df["close"].mean())
            pred_direction = "LONG" if pred_close_avg > first_close else "SHORT"
            
            # Magnitud del cambio predicho
            pct_change = (pred_close_avg - first_close) / first_close * 100
            
            # Confianza basada en acuerdo de dirección y magnitud
            if pred_direction == signal_direction:
                # Acuerdo — confianza proporcional a magnitud (cap 1.0)
                confidence = min(0.5 + abs(pct_change) * 2, 1.0)
                reason = f"kronos_confirm_{pred_direction} Δ{pct_change:+.2f}%"
            else:
                # Desacuerdo — penalizar
                confidence = max(0.5 - abs(pct_change) * 2, 0.0)
                reason = f"kronos_contradict_{pred_direction} Δ{pct_change:+.2f}%"
            
            log.info(f"🤖 Kronos: pred={pred_direction} Δ{pct_change:+.2f}% → conf={confidence:.2f}")
            return confidence, reason
            
        except Exception as e:
            log.warning(f"Kronos inference error: {e}")
            return 0.5, "kronos_error"
    
    def _prepare_data(self, klines: list, pred_len: int) -> Tuple[pd.DataFrame, pd.Series, pd.Series]:
        """
        Convierte klines de BingX al formato que espera Kronos.
        
        BingX kline: [timestamp, open, high, low, close, volume, ...]
        Kronos: DataFrame con columnas open, high, low, close, volume
        
        CRÍTICO — no look-ahead:
        - Usamos las últimas N velas reales como contexto (x)
        - y son los próximos pred_len timestamps (futuro vacío)
        """
        # BingX devuelve klines más recientes al final
        rows = []
        for k in klines[-200:]:  # últimas 200 velas máx
            try:
                ts = pd.Timestamp(int(k[0]), unit="ms")
                rows.append({
                    "timestamp": ts,
                    "open":  float(k[1]),
                    "high":  float(k[2]),
                    "low":   float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]) if len(k) > 5 else 0.0,
                })
            except (IndexError, ValueError):
                continue
        
        df = pd.DataFrame(rows).set_index("timestamp").sort_index()
        df = df.dropna()
        
        # Usar máx 150 velas de contexto (dentro del max_context=512)
        context = df.tail(150).reset_index()
        x_timestamps = context["timestamp"]
        
        # Timestamps futuros: inferir frecuencia y proyectar
        freq = x_timestamps.diff().median()
        last_ts = x_timestamps.iloc[-1]
        y_timestamps = pd.Series([
            last_ts + freq * (i + 1) for i in range(pred_len)
        ])
        
        return context.drop("timestamp", axis=1), x_timestamps, y_timestamps


# ── Walk-Forward Evaluator (para testear sin bias) ─────────────────────
class WalkForwardEvaluator:
    """
    Evalúa Kronos con walk-forward riguroso:
    - Divide datos históricos en ventanas de tiempo
    - Nunca usa datos futuros en el contexto
    - Reporta métricas reales (no infladas)
    
    Uso:
        evaluator = WalkForwardEvaluator(klines_historicos)
        metrics = evaluator.evaluate(n_windows=20, pred_len=12)
        print(metrics)  # IC, RankIC, MAE, win_rate_direction
    """
    
    def __init__(self, klines: list, filter: KronosFilter = None):
        self.klines = klines
        self.filter = filter or KronosFilter()
    
    def evaluate(self, n_windows: int = 20, pred_len: int = 12,
                 context_len: int = 150) -> dict:
        if not self.filter.model_loaded:
            return {"error": "Kronos no disponible", "ic": None, "win_rate": None}
        
        results = []
        step = max(1, (len(self.klines) - context_len - pred_len) // n_windows)
        
        for i in range(n_windows):
            start = i * step
            end = start + context_len
            future_end = end + pred_len
            
            if future_end > len(self.klines):
                break
            
            # Contexto: velas [start:end] → PASADO
            ctx_klines = self.klines[start:end]
            # Futuro real: velas [end:future_end] → para comparar
            future_klines = self.klines[end:future_end]
            
            try:
                df, x_ts, y_ts = self.filter._prepare_data(ctx_klines, pred_len)
                pred_df = self.filter.predictor.predict(
                    df=df, x_timestamp=x_ts, y_timestamp=y_ts,
                    pred_len=pred_len, T=0.8, top_p=0.9, sample_count=3,
                )
                
                last_real = float(ctx_klines[-1][4])  # close de la última vela real
                pred_close = float(pred_df["close"].mean())
                real_close = float(future_klines[-1][4])  # close real futuro
                
                pred_dir = 1 if pred_close > last_real else -1
                real_dir = 1 if real_close > last_real else -1
                
                results.append({
                    "window": i,
                    "direction_correct": pred_dir == real_dir,
                    "pred_pct": (pred_close - last_real) / last_real * 100,
                    "real_pct": (real_close - last_real) / last_real * 100,
                })
                
            except Exception as e:
                log.debug(f"WF window {i} error: {e}")
        
        if not results:
            return {"error": "Sin resultados", "ic": None, "win_rate": None}
        
        df_r = pd.DataFrame(results)
        win_rate = df_r["direction_correct"].mean()
        
        # IC (Information Coefficient) — correlación entre pred y real
        if len(df_r) > 2:
            ic = df_r["pred_pct"].corr(df_r["real_pct"])
        else:
            ic = float("nan")
        
        mae = np.mean(np.abs(df_r["pred_pct"] - df_r["real_pct"]))
        
        log.info(f"📊 Walk-Forward | Ventanas={len(results)} | WinRate={win_rate:.1%} | IC={ic:.3f} | MAE={mae:.2f}%")
        
        return {
            "n_windows": len(results),
            "win_rate_direction": round(win_rate, 4),
            "ic": round(ic, 4),
            "mae_pct": round(mae, 4),
            "valid": win_rate > 0.52 and ic > 0.05,  # umbral mínimo para usar
        }
