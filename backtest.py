import requests, numpy as np, time
import pandas as pd

# Simular costos reales
FEE = 0.00045 + 0.00020 # Comisión + Slippage

def ejecutar_backtest_realista(par, velas=2000):
    # ... (código de obtención de datos igual al tuyo) ...
    # Al calcular cada trade:
    
    costo_apertura = precio_entrada * FEE
    costo_cierre = precio_salida * FEE
    
    if win:
        # PnL neto quitando comisiones de entrada y salida
        pnl_usd = (cantidad * (precio_salida - precio_entrada)) - (cantidad * (costo_apertura + costo_cierre))
    else:
        pnl_usd = (cantidad * (precio_salida - precio_entrada)) - (cantidad * (costo_apertura + costo_cierre))
    
    return pnl_usd
