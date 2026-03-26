#!/usr/bin/env python3
"""
BOT SHORTS PROFESIONAL v3.3.2 — FIX MÍNIMO USDT GARANTIZADO
════════════════════════════════════════════════════════════════
FIX v3.3.2: Validación ESTRICTA de mínimo 8 USDT en TODAS las órdenes

CAMBIOS PRINCIPALES:
✅ FIX-1: Nueva función _validate_usdt_amount() antes de cada orden
✅ FIX-2: Rechaza trades < 8 USDT notional ANTES de enviar a exchange
✅ FIX-3: Logging detallado de cálculo USDT qty × price
✅ FIX-4: Fallback a cantidad mínima garantizada si es necesario
════════════════════════════════════════════════════════════════
"""

# ============================================================================
# SOLO CAMBIOS CRÍTICOS — Reemplaza estas funciones en tu bot actual
# ============================================================================

"""
INSTRUCCIONES DE APLICACIÓN:

1. Localiza estas funciones en tu código actual (v3.3.1):
   - _qty_contratos()
   - _place_short_entry()
   - open_trade()

2. Reemplazalas CON las versiones de abajo (v3.3.2)

3. Agrega esta función NUEVA:
   - _validate_usdt_amount() [ES NUEVA]

4. Los cambios están marcados con "# FIX v3.3.2"
"""

# ============================================================================
# NUEVA FUNCIÓN - Validación de mínimo USDT
# ============================================================================

def _validate_usdt_amount(self, symbol, qty_c, price):
    """
    FIX v3.3.2: Valida que la cantidad de contratos = mínimo 8 USDT notional
    
    Returns:
        (True, qty_c, valor_usdt)  si es válido
        (False, None, None)        si es inválido (< FORCE_MIN_USDT)
    """
    if not qty_c or qty_c <= 0:
        log.error(f"  [VALIDAR] qty_c inválido: {qty_c}")
        return False, None, None
    
    if price <= 0:
        log.error(f"  [VALIDAR] precio inválido: {price}")
        return False, None, None
    
    info = self._contracts.get(symbol, {'ctval': 1.0})
    ctval = info.get('ctval', 1.0)
    ppc = price * ctval if ctval != 1.0 else price
    
    valor_usdt = qty_c * ppc
    
    log.info(f"  [VALIDAR] {symbol}: {qty_c} contratos × ${ppc:.8f} = ${valor_usdt:.2f} USDT")
    
    # FIX v3.3.2: Rechaza si < FORCE_MIN_USDT
    if valor_usdt < FORCE_MIN_USDT:
        log.error(f"  [VALIDAR] ❌ RECHAZADA: ${valor_usdt:.2f} USDT < ${FORCE_MIN_USDT} mínimo")
        return False, None, None
    
    log.info(f"  [VALIDAR] ✅ APROBADA: ${valor_usdt:.2f} USDT >= ${FORCE_MIN_USDT}")
    return True, qty_c, round(valor_usdt, 2)


# ============================================================================
# FUNCIÓN MEJORADA - Cálculo de cantidad
# ============================================================================

def _qty_contratos(self, symbol, price, usdt_amount=None):
    """
    FIX v3.3.2: Garantiza SIEMPRE mínimo FORCE_MIN_USDT
    - Calcula cantidad inicial
    - Si no alcanza mínimo, incrementa hasta lograrlo
    - Valida resultado ANTES de retornar
    """
    if usdt_amount is None:
        usdt_amount = POSITION_SIZE
    
    # FIX v3.3.2: Triple garantía
    usdt_amount = max(usdt_amount, FORCE_MIN_USDT, MIN_TRADE)
    
    info = self._contracts.get(symbol, {'step': 1.0, 'prec': 2, 'ctval': 1.0})
    step = max(info['step'], 0.0001)
    prec = info['prec']
    ctval = info.get('ctval', 1.0)
    ppc = price * ctval if ctval != 1.0 else price
    
    if ppc <= 0:
        log.error(f"  [QTY] Precio inválido para {symbol}: {ppc}")
        return None, 0
    
    # Cálculo inicial
    qty = round(math.ceil(usdt_amount / ppc / step) * step, prec)
    val = qty * ppc
    
    log.debug(f"  [QTY] {symbol} inicial: qty={qty} val=${val:.2f}")
    
    i = 0
    min_val = max(MIN_TRADE, FORCE_MIN_USDT)
    
    # FIX v3.3.2: Loop de garantía con límite
    while val < min_val and i < 100:
        qty += step
        qty = round(qty, prec)
        val = qty * ppc
        i += 1
        log.debug(f"    iteración {i}: qty={qty} val=${val:.2f}")
    
    # FIX v3.3.2: Verificación final ESTRICTA
    if val < min_val:
        log.error(f"  [QTY] ❌ NO SE ALCANZÓ MÍNIMO: ${val:.2f} USDT < ${min_val}")
        log.error(f"  [QTY] Posible causa: step muy grande o precio muy alto")
        log.error(f"  [QTY] {symbol} abortado")
        return None, 0
    
    # No exceder 130% del capital
    if val > usdt_amount * 1.3:
        log.debug(f"  [QTY] {symbol} recortando (${val:.2f} > ${usdt_amount*1.3:.2f})")
        qty = round(math.floor((usdt_amount * 1.3 / ppc) / step) * step, prec)
        val = qty * ppc
        
        # FIX v3.3.2: Re-verificar tras recorte
        if val < min_val:
            qty = round(math.ceil(min_val / ppc / step) * step, prec)
            val = qty * ppc
            log.warning(f"  [QTY] Recalculado a mínimo: qty={qty} val=${val:.2f}")
    
    log.info(f"  [QTY] {symbol} FINAL: {qty} contratos × ${ppc:.6f} = ${val:.2f} USDT ✅")
    return qty, round(val, 4)


# ============================================================================
# FUNCIÓN MEJORADA - Colocación de orden
# ============================================================================

def _place_short_entry(self, symbol, usdt_qty, price):
    """
    FIX v3.3.2: Valida ANTES y DESPUÉS de calcular cantidad
    - No coloca órdenes < FORCE_MIN_USDT
    - Retorna (None, None) si no cumple mínimo
    """
    
    # FIX v3.3.2: Validación INICIAL
    usdt_qty = max(usdt_qty, FORCE_MIN_USDT)
    
    qty_c, qty_val = self._qty_contratos(symbol, price, usdt_qty)
    
    # FIX v3.3.2: Validación ANTES de enviar orden
    if not qty_c or qty_c <= 0:
        log.error(f"  ENTRADA CANCELADA {symbol}: qty_c inválido")
        return None, None
    
    valid, qty_c_validated, valor_final = self._validate_usdt_amount(symbol, qty_c, price)
    if not valid:
        log.error(f"  ENTRADA CANCELADA {symbol}: valor USDT insuficiente")
        return None, None
    
    log.info(f"  Intentando SHORT {symbol}: ${valor_final:.2f} USDT → {qty_c_validated} contratos")
    
    # ── Método 1: LIMIT (maker, menor comisión) ──
    if USE_LIMIT_ORDERS and qty_c_validated:
        limit_price = round(price * (1 + LIMIT_OFFSET_PCT / 100), 8)
        d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol': symbol,
            'side': 'SELL',
            'positionSide': 'SHORT',
            'type': 'LIMIT',
            'price': str(limit_price),
            'quantity': str(qty_c_validated),
            'timeInForce': 'GTC',
        }).json()
        
        if d.get('code') == 0:
            log.info(f"  ENTRADA LÍMITE maker OK: {qty_c_validated} cts @ ${limit_price:.6f}")
            return d.get('data', {}).get('orderId', 'OK'), qty_c_validated
        
        if 'margin' in str(d.get('msg', '')).lower():
            log.error(f"  Margen insuficiente — abortando")
            return None, None
        
        log.warning(f"  Límite falló [{d.get('code')}] — intentando quoteOrderQty")
    
    # ── Método 2: MARKET con quoteOrderQty (RECOMENDADO) ──
    d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
        'symbol': symbol,
        'side': 'SELL',
        'positionSide': 'SHORT',
        'type': 'MARKET',
        'quoteOrderQty': str(round(valor_final, 2)),  # FIX v3.3.2: Usa valor validado
    }).json()
    
    if d.get('code') == 0:
        log.info(f"  ENTRADA MARKET quoteOrderQty OK: ${valor_final:.2f} USDT")
        return d.get('data', {}).get('orderId', 'OK'), qty_c_validated
    
    log.warning(f"  quoteOrderQty falló [{d.get('code')}] — fallback quantity")
    
    # ── Método 3: MARKET con quantity (fallback) ──
    if not qty_c_validated or qty_c_validated <= 0:
        log.error(f"  No hay cantidad válida para fallback")
        return None, None
    
    d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', {
        'symbol': symbol,
        'side': 'SELL',
        'positionSide': 'SHORT',
        'type': 'MARKET',
        'quantity': str(qty_c_validated),
    }).json()
    
    if d2.get('code') == 0:
        log.info(f"  ENTRADA MARKET quantity OK: {qty_c_validated} contratos")
        return d2.get('data', {}).get('orderId', 'OK'), qty_c_validated
    
    log.error(f"  TODOS LOS MÉTODOS FALLARON [{d2.get('code')}]: {d2.get('msg')}")
    return None, None


# ============================================================================
# MEJORA EN open_trade() - Validación final
# ============================================================================

"""
En tu función open_trade(), ANTES de llamar _place_short_entry():

Reemplaza:
    oid, qty_c = self._place_short_entry(symbol, usdt_qty, price)
    if not oid:
        log.error(f"  No se pudo abrir {symbol}"); return False

Con:
    oid, qty_c = self._place_short_entry(symbol, usdt_qty, price)
    if not oid or not qty_c or qty_c <= 0:
        log.error(f"  No se pudo abrir {symbol} (validación fallida)")
        return False  # FIX v3.3.2
"""


# ============================================================================
# CONFIGURACIÓN RECOMENDADA PARA v3.3.2
# ============================================================================

"""
En tus variables de entorno, asegúrate de tener:

export FORCE_MIN_USDT=8.0
export MIN_TRADE_USDT=8.0
export MIN_CAPITAL_REQUIRED=6.0
export MAX_POSITION_SIZE=8.0
export POSITION_SIZE=8.0

Si ALGUNAS monedas SIGUEN abriendo órdenes pequeñas, agrega esto:

export MIN_NOTIONAL_USDT=8.5

Esto rechazará cualquier trade que no alcance 8.5 USDT exactos.
"""


# ============================================================================
# CHECKLIST DE APLICACIÓN
# ============================================================================

"""
PASO A PASO PARA ACTUALIZAR:

1. ✅ Abre tu bot actual (v3.3.1)

2. ✅ Localiza y REEMPLAZA estas funciones:
   - def _qty_contratos(self, symbol, price, usdt_amount=None):
   - def _place_short_entry(self, symbol, usdt_qty, price):

3. ✅ AGREGA esta función NUEVA después de __init__():
   - def _validate_usdt_amount(self, symbol, qty_c, price):

4. ✅ En open_trade(), BEFORE llamar _place_short_entry(), agrega:
   
   # FIX v3.3.2: Validación previa de capital
   usdt_qty = round(max(POSITION_SIZE, FORCE_MIN_USDT, MIN_TRADE), 2)
   
   # Validar que después de cálculo de qty, tengamos >= FORCE_MIN_USDT
   test_qty, test_val = self._qty_contratos(symbol, price, usdt_qty)
   if not test_qty or test_val < FORCE_MIN_USDT:
       log.warning(f"  {symbol} rechazado: valor USDT insuficiente (${test_val})")
       return False

5. ✅ Testa con un par que antes abría trade pequeño (ej: HYPEUSD)

6. ✅ Verifica en logs:
   [VALIDAR] ✅ APROBADA: $X.XX USDT >= $8.00

7. ✅ Si VES "[VALIDAR] ❌ RECHAZADA", significa que FIX está funcionando ✅
"""

print("""
╔════════════════════════════════════════════════════════════════╗
║                  BOT SHORTS v3.3.2 FIX READY                  ║
╠════════════════════════════════════════════════════════════════╣
║                                                                ║
║  ✅ Nueva función: _validate_usdt_amount()                   ║
║  ✅ Mejorada: _qty_contratos() con validación estricta        ║
║  ✅ Mejorada: _place_short_entry() con doble check            ║
║                                                                ║
║  GARANTIZA: Mínimo 8 USDT notional en TODAS las órdenes      ║
║                                                                ║
║  RESULTADO: NO más trades de 1-2 USDT                         ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
""")
