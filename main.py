//@version=6
strategy("Saty Unified Strategy v7 — High WR", overlay=true,
         initial_capital=1000,
         default_qty_type=strategy.percent_of_equity,
         default_qty_value=15,
         margin_long=100, margin_short=100,
         commission_type=strategy.commission.percent,
         commission_value=0.05,
         slippage=1)

// ==========================================
// 1. INPUTS
// ==========================================
grp_trend     = "Tendencia (EMA Ribbon)"
fast_len      = input.int(8,  "EMA Rápida",           group=grp_trend)
pivot_len     = input.int(21, "EMA Pivot",             group=grp_trend)
bias_len      = input.int(48, "EMA Sesgo",             group=grp_trend)

grp_filters   = "Filtros de Calidad"
adx_len       = input.int(14,     "Longitud ADX",                      group=grp_filters)
adx_min       = input.int(20,     "ADX mínimo (fuerza tendencia)",      group=grp_filters)
rsi_len       = input.int(14,     "Longitud RSI",                       group=grp_filters)
rsi_long_min  = input.int(52,     "RSI Long mínimo",                    group=grp_filters)
rsi_long_max  = input.int(75,     "RSI Long máximo",                    group=grp_filters)
rsi_short_min = input.int(25,     "RSI Short mínimo",                   group=grp_filters)
rsi_short_max = input.int(48,     "RSI Short máximo",                   group=grp_filters)
atr_vol_min   = input.float(0.0005, "ATR mínimo (% precio)",            group=grp_filters)
use_htf       = input.bool(true,  "Filtro Higher Timeframe (HTF)",      group=grp_filters)
htf_tf        = input.timeframe("15", "Timeframe HTF",                  group=grp_filters)

grp_osc       = "Phase Oscillator & Volatilidad"
atr_len       = input.int(14,    "Longitud ATR",                        group=grp_osc)
osc_ema_len   = input.int(3,     "Suavizado Oscilador",                 group=grp_osc)
tp_mult       = input.float(2.5, "Take Profit Long (ATR Mult)",  minval=0.1, group=grp_osc)
sl_mult       = input.float(1.5, "Stop Loss Long (ATR Mult)",    minval=0.1, group=grp_osc)
short_tp_mult = input.float(2.5, "Take Profit Short (ATR Mult)", minval=0.1, group=grp_osc)
short_sl_mult = input.float(1.5, "Stop Loss Short (ATR Mult)",   minval=0.1, group=grp_osc)
use_trailing  = input.bool(true, "Usar Trailing Stop",                  group=grp_osc)
trail_mult    = input.float(1.0, "Trailing Stop (ATR Mult)",     minval=0.1, group=grp_osc)

grp_visual    = "Visualización"
show_labels   = input.bool(true, "Mostrar Etiquetas", group=grp_visual)

// ==========================================
// 2. CÁLCULOS BASE
// ==========================================
ema8  = ta.ema(close, fast_len)
ema21 = ta.ema(close, pivot_len)
ema48 = ta.ema(close, bias_len)

bullish_ribbon = ema8 > ema21
bearish_ribbon = ema8 < ema21

atr        = ta.atr(atr_len)
raw_signal = ((close - ema21) / (3.0 * atr)) * 100
oscillator = ta.ema(raw_signal, osc_ema_len)

// FIX: crossover/crossunder como variables independientes
osc_cross_up   = ta.crossover(oscillator,  0)
osc_cross_down = ta.crossunder(oscillator, 0)

stdev        = ta.stdev(close, pivot_len)
bb_upper     = ema21 + (2.0 * stdev)
kc_upper     = ema21 + (2.0 * atr)
is_squeezing = bb_upper < kc_upper

buy_vol          = volume * (close - low)  / (high - low)
sell_vol         = volume * (high - close) / (high - low)
buyers_dominant  = buy_vol  > sell_vol
sellers_dominant = sell_vol > buy_vol

// ==========================================
// 3. FILTROS DE CALIDAD
// ==========================================

// Filtro 1: ADX — solo entrar con tendencia real
[diplus, diminus, adx_val] = ta.dmi(adx_len, adx_len)
trending_market = adx_val > adx_min

// Filtro 2: RSI Sweet Spot
rsi_val      = ta.rsi(close, rsi_len)
rsi_ok_long  = rsi_val >= rsi_long_min  and rsi_val <= rsi_long_max
rsi_ok_short = rsi_val >= rsi_short_min and rsi_val <= rsi_short_max

// Filtro 3: ATR mínimo — evitar rangos planos
atr_pct = atr / close
atr_ok  = atr_pct >= atr_vol_min

// Filtro 4: Higher Timeframe Bias
htf_ema48    = request.security(syminfo.tickerid, htf_tf, ta.ema(close, bias_len), lookahead=barmerge.lookahead_off)
htf_close    = request.security(syminfo.tickerid, htf_tf, close,                   lookahead=barmerge.lookahead_off)
htf_bull     = htf_close > htf_ema48
htf_bear     = htf_close < htf_ema48
htf_ok_long  = not use_htf or htf_bull
htf_ok_short = not use_htf or htf_bear

// Filtro 5: Volumen sobre la media
vol_ma    = ta.sma(volume, 10)
vol_above = volume > vol_ma * 0.8

// ==========================================
// 4. CONDICIONES DE ENTRADA
// FIX: "and" siempre al FINAL de línea en Pine Script v6
// ==========================================

// LONG
base_long   = close > ema48 and bullish_ribbon and osc_cross_up and buyers_dominant and not is_squeezing
filter_long = trending_market and rsi_ok_long and atr_ok and htf_ok_long and vol_above
long_entry  = base_long and filter_long

// SHORT
base_short   = close < ema48 and bearish_ribbon and osc_cross_down and sellers_dominant and not is_squeezing
filter_short = trending_market and rsi_ok_short and atr_ok and htf_ok_short and vol_above
short_entry  = base_short and filter_short

// ==========================================
// 5. EJECUCIÓN Y GESTIÓN DE SALIDAS
// ==========================================
if long_entry
    strategy.entry("Long", strategy.long, comment="▲ LONG")

if short_entry
    strategy.entry("Short", strategy.short, comment="▼ SHORT")

if strategy.position_size > 0
    long_tp = strategy.position_avg_price + (atr * tp_mult)
    long_sl = strategy.position_avg_price - (atr * sl_mult)
    if use_trailing
        strategy.exit("Exit Long", "Long", limit=long_tp, stop=long_sl, trail_price=strategy.position_avg_price + (atr * trail_mult), trail_offset=atr * trail_mult / syminfo.mintick, comment_loss="SL", comment_profit="TP")
    else
        strategy.exit("Exit Long", "Long", limit=long_tp, stop=long_sl, comment_loss="SL", comment_profit="TP")

if strategy.position_size < 0
    short_tp = strategy.position_avg_price - (atr * short_tp_mult)
    short_sl = strategy.position_avg_price + (atr * short_sl_mult)
    if use_trailing
        strategy.exit("Exit Short", "Short", limit=short_tp, stop=short_sl, trail_price=strategy.position_avg_price - (atr * trail_mult), trail_offset=atr * trail_mult / syminfo.mintick, comment_loss="SL", comment_profit="TP")
    else
        strategy.exit("Exit Short", "Short", limit=short_tp, stop=short_sl, comment_loss="SL", comment_profit="TP")

// ==========================================
// 6. VISUALES
// ==========================================
plot(ema48, color=color.new(color.white, 0), linewidth=2, title="EMA 48 (Sesgo)")
plot(htf_ema48, color=color.new(color.yellow, 40), linewidth=1, title="HTF EMA 48", style=plot.style_circles)

p8  = plot(ema8,  display=display.none, title="EMA8")
p21 = plot(ema21, display=display.none, title="EMA21")
fill(p8, p21, color = ema8 > ema21 ? color.new(color.green, 80) : color.new(color.red, 80), title="Ribbon")

bgcolor(is_squeezing    ? color.new(color.gray,   92) : na, title="Squeeze")
bgcolor(not trending_market ? color.new(color.orange, 95) : na, title="Sin Tendencia")

plotshape(long_entry  and show_labels, "Long",  shape.labelup,   location.belowbar, color.green, text="L", textcolor=color.white, size=size.small)
plotshape(short_entry and show_labels, "Short", shape.labeldown, location.abovebar, color.red,   text="S", textcolor=color.white, size=size.small)

// Dashboard
var tbl = table.new(position.top_right, 2, 6, border_width=1)
if barstate.islast
    table.cell(tbl, 0, 0, "ADX",     bgcolor=color.new(color.gray, 20), text_color=color.white)
    table.cell(tbl, 1, 0, str.tostring(math.round(adx_val, 1)), bgcolor = adx_val > adx_min ? color.new(color.green, 20) : color.new(color.red, 20), text_color=color.white)
    table.cell(tbl, 0, 1, "RSI",     bgcolor=color.new(color.gray, 20), text_color=color.white)
    table.cell(tbl, 1, 1, str.tostring(math.round(rsi_val, 1)), bgcolor = rsi_ok_long ? color.new(color.green, 20) : rsi_ok_short ? color.new(color.red, 20) : color.new(color.gray, 20), text_color=color.white)
    table.cell(tbl, 0, 2, "HTF",     bgcolor=color.new(color.gray, 20), text_color=color.white)
    table.cell(tbl, 1, 2, htf_bull ? "BULL" : "BEAR", bgcolor = htf_bull ? color.new(color.green, 20) : color.new(color.red, 20), text_color=color.white)
    table.cell(tbl, 0, 3, "SQUEEZE", bgcolor=color.new(color.gray, 20), text_color=color.white)
    table.cell(tbl, 1, 3, is_squeezing ? "ON" : "OFF", bgcolor = is_squeezing ? color.new(color.orange, 20) : color.new(color.green, 20), text_color=color.white)
    table.cell(tbl, 0, 4, "VOL/MA",  bgcolor=color.new(color.gray, 20), text_color=color.white)
    table.cell(tbl, 1, 4, str.tostring(math.round(volume / vol_ma, 2)) + "x", bgcolor = vol_above ? color.new(color.green, 20) : color.new(color.gray, 20), text_color=color.white)
    table.cell(tbl, 0, 5, "TREND",   bgcolor=color.new(color.gray, 20), text_color=color.white)
    table.cell(tbl, 1, 5, trending_market ? "OK" : "LATERAL", bgcolor = trending_market ? color.new(color.green, 20) : color.new(color.orange, 20), text_color=color.white)
