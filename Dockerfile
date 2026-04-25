// This Pine Script® code is subject to the terms of the Mozilla Public License 2.0 at https://mozilla.org/MPL/2.0/
// © QuantNomad

//@version=6
indicator("Signal Projection Explorer", shorttitle = "YrlPrj", overlay = true, max_lines_count = 500)

////////////
// INPUTS /{

proj_length     = input.int(10, "Projection Length")

fast_len = input.int(50,  "Fast Length")
slow_len = input.int(200, "Fast Length")

//}


//////////////////
// CALCULATIONS /{

ma_fast = ta.sma(close, fast_len)
ma_slow = ta.sma(close, slow_len)

signal = ta.crossover(ma_fast, ma_slow)

type DayPnl
    array<float> values

var array<DayPnl> day_pnls = array.new<DayPnl>()

var array<int>   tracked_signals  = array.new<int>()
var array<float> tracked_last_pnl = array.new<float>()

if barstate.isfirst
    for i = 0 to proj_length - 1
        array.push(day_pnls, DayPnl.new(array.new_float(0)))

pnl = close / close[1] - 1

if tracked_signals.size() > 0
    for i = 0 to tracked_signals.size() - 1
        day_i = tracked_signals.get(i)
        if day_i == proj_length
            tracked_signals.remove(i)
            tracked_last_pnl.remove(i)
        else 
            new_pnl = (tracked_last_pnl.get(i) + 1) * (1 + pnl) - 1
            day_pnls.get(day_i).values.push(new_pnl) 
            tracked_signals.set(i, day_i + 1)
            tracked_last_pnl.set(i, new_pnl)

if signal
    tracked_signals.unshift(0)
    tracked_last_pnl.unshift(0.0)
 
worst_pnl  = array.new_float(0)
best_pnl   = array.new_float(0)
p25_pnl    = array.new_float(0)
p75_pnl    = array.new_float(0)
mean_pnl   = array.new_float(0)
median_pnl = array.new_float(0)

for day_i = 0 to proj_length - 1
    worst_pnl.push(day_pnls.get(day_i).values.min())
    best_pnl.push(day_pnls.get(day_i).values.max())
    p25_pnl.push(day_pnls.get(day_i).values.percentile_linear_interpolation(25))
    p75_pnl.push(day_pnls.get(day_i).values.percentile_linear_interpolation(75))
    mean_pnl.push(day_pnls.get(day_i).values.avg())
    median_pnl.push(day_pnls.get(day_i).values.median())

if barstate.islast
    log.info(str.tostring(day_pnls.get(9).values))


plot(ma_fast, color = color.red,   linewidth = 2)
plot(ma_slow, color = color.green, linewidth = 2)

bgcolor(signal ? color.new(color.green, 75) : na)

///////////
// PLOTS //

if barstate.islast
    // Worst 

    cur_price = close 
    next_price = close 

    cur_col = #FF5252

    for i = 0 to worst_pnl.size() - 1
        cur_pnl = worst_pnl.get(i)

        next_price := close * (1 + cur_pnl)

        line.new(bar_index + i, cur_price, bar_index + i + 1, next_price, color = cur_col)

        cur_price := next_price

    total_pnl = str.tostring(math.round((next_price / close - 1) * 100, 2)) + "%"
    label.new(bar_index + worst_pnl.size(), cur_price, text = 'Worst P&L ' + "(" +  total_pnl+ ")", style = label.style_none, textcolor = cur_col)        
     
    // Best 

    cur_price := close 
    next_price := close 

    cur_col := #00E676

    for i = 0 to best_pnl.size() - 1
        cur_pnl = best_pnl.get(i)

        next_price := close * (1 + cur_pnl)

        line.new(bar_index + i, cur_price, bar_index + i + 1, next_price, color = cur_col)

        cur_price := next_price

    total_pnl := str.tostring(math.round((next_price / close - 1) * 100, 2)) + "%"
    label.new(bar_index + best_pnl.size(), cur_price, text = 'Best P&L ' + "(" +  total_pnl+ ")", style = label.style_none, textcolor = cur_col)        
     
    // Perc 25 

    cur_price := close 
    next_price := close 

    cur_col := #64B5F6

    for i = 0 to p25_pnl.size() - 1
        cur_pnl = p25_pnl.get(i)

        next_price := close * (1 + cur_pnl)

        line.new(bar_index + i, cur_price, bar_index + i + 1, next_price, color = cur_col)

        cur_price := next_price

    total_pnl := str.tostring(math.round((next_price / close - 1) * 100, 2)) + "%"
    label.new(bar_index + p25_pnl.size(), cur_price, text = '25th %tile ' + "(" +  total_pnl+ ")", style = label.style_none, textcolor = cur_col)        
     
    // Perc 75

    cur_price := close 
    next_price := close 

    cur_col := #FFB74D

    for i = 0 to p75_pnl.size() - 1
        cur_pnl = p75_pnl.get(i)

        next_price := close * (1 + cur_pnl)

        line.new(bar_index + i, cur_price, bar_index + i + 1, next_price, color = cur_col)

        cur_price := next_price

    total_pnl := str.tostring(math.round((next_price / close - 1) * 100, 2)) + "%"
    label.new(bar_index + p75_pnl.size(), cur_price, text = '75th %tile ' + "(" +  total_pnl+ ")", style = label.style_none, textcolor = cur_col)        
     
    // Mean

    cur_price := close 
    next_price := close 

    cur_col := #E0E0E0

    for i = 0 to mean_pnl.size() - 1
        cur_pnl = mean_pnl.get(i)

        next_price := close * (1 + cur_pnl)

        line.new(bar_index + i, cur_price, bar_index + i + 1, next_price, color = cur_col)

        cur_price := next_price

    total_pnl := str.tostring(math.round((next_price / close - 1) * 100, 2)) + "%"
    label.new(bar_index + mean_pnl.size(), cur_price, text = 'Mean ' + "(" +  total_pnl+ ")", style = label.style_none, textcolor = cur_col)        
     
    // Meadian

    cur_price := close 
    next_price := close 

    cur_col := #BA68C8

    for i = 0 to median_pnl.size() - 1
        cur_pnl = median_pnl.get(i)

        next_price := close * (1 + cur_pnl)

        line.new(bar_index + i, cur_price, bar_index + i + 1, next_price, color = cur_col)

        cur_price := next_price

    total_pnl := str.tostring(math.round((next_price / close - 1) * 100, 2)) + "%"
    label.new(bar_index + median_pnl.size(), cur_price, text = 'Median ' + "(" +  total_pnl+ ")", style = label.style_none, textcolor = cur_col)        
     


f_last(arr) =>
    array.size(arr) > 0 ? array.get(arr, array.size(arr) - 1) : na

f_pct(v) =>
    na(v) ? "n/a" : str.tostring(v * 100, "#.##") + "%"

f_dir_col(v) =>
    na(v) ? color.rgb(160, 160, 160) : v > 0 ? color.rgb(0, 230, 118) : v < 0 ? color.rgb(255, 82, 82) : color.rgb(220, 220, 220)

// Better dark-mode colors
colWorst   = color.rgb(255, 82, 82)     // red
colBest    = color.rgb(0, 230, 118)     // green
colP25     = color.rgb(79, 195, 247)    // bright cyan-blue
colP75     = color.rgb(255, 193, 7)     // amber
colMean    = color.rgb(240, 240, 240)   // near white
colMedian  = color.rgb(171, 71, 188)    // vivid purple
colMetric  = color.rgb(185, 185, 185)   // brighter left column
colNeutral = color.rgb(210, 210, 210)
bgHeader   = color.rgb(45, 50, 60)
bgCell     = color.rgb(22, 24, 30)
bgAlt      = color.rgb(28, 30, 36)

signal_count = day_pnls.get(0).values.size()

var table t = table.new(position.top_right, 2, 10, border_width=1)

if barstate.islast
    worst  = f_last(worst_pnl)
    best   = f_last(best_pnl)
    p25    = f_last(p25_pnl)
    p75    = f_last(p75_pnl)
    mean   = f_last(mean_pnl)
    median = f_last(median_pnl)

    spread = na(best) or na(worst) ? na : best - worst
    iqr    = na(p75) or na(p25) ? na : p75 - p25
    skew   = na(mean) or na(median) ? na : mean - median

    table.clear(t, 0, 0, 1, 9)

    // Header
    table.cell(t, 0, 0, "Metric", text_color=color.white, bgcolor=bgHeader)
    table.cell(t, 1, 0, "Value",  text_color=color.white, bgcolor=bgHeader)

    // Rows
    table.cell(t, 0, 1, "Signals", text_color=colMetric, bgcolor=bgAlt)
    table.cell(t, 1, 1, str.tostring(signal_count), text_color=color.white, bgcolor=bgAlt)

    table.cell(t, 0, 2, "Worst P&L", text_color=colMetric, bgcolor=bgCell)
    table.cell(t, 1, 2, f_pct(worst), text_color=colWorst, bgcolor=bgCell)

    table.cell(t, 0, 3, "Best P&L", text_color=colMetric, bgcolor=bgAlt)
    table.cell(t, 1, 3, f_pct(best), text_color=colBest, bgcolor=bgAlt)

    table.cell(t, 0, 4, "25th %tile", text_color=colMetric, bgcolor=bgCell)
    table.cell(t, 1, 4, f_pct(p25), text_color=colP25, bgcolor=bgCell)

    table.cell(t, 0, 5, "75th %tile", text_color=colMetric, bgcolor=bgAlt)
    table.cell(t, 1, 5, f_pct(p75), text_color=colP75, bgcolor=bgAlt)

    table.cell(t, 0, 6, "Mean", text_color=colMetric, bgcolor=bgCell)
    table.cell(t, 1, 6, f_pct(mean), text_color=colMean, bgcolor=bgCell)

    table.cell(t, 0, 7, "Median", text_color=colMetric, bgcolor=bgAlt)
    table.cell(t, 1, 7, f_pct(median), text_color=colMedian, bgcolor=bgAlt)

    table.cell(t, 0, 8, "Spread", text_color=colMetric, bgcolor=bgCell)
    table.cell(t, 1, 8, f_pct(spread), text_color=colNeutral, bgcolor=bgCell)

    table.cell(t, 0, 9, "IQR", text_color=colMetric, bgcolor=bgAlt)
    table.cell(t, 1, 9, f_pct(iqr), text_color=colNeutral, bgcolor=bgAlt)
