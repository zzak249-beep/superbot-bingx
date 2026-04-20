import { useState, useEffect, useCallback } from "react";

const DEMO_DATA = {
  balance: 1247.83,
  daily_pnl: 2.34,
  open_positions: [
    { symbol: "SOL-USDT", side: "LONG", entry: 142.5, current: 148.9, sl: 138.2, tp: 154.8, score: 78, qty: 0.42, mtf: 65, funding: 0.0002 },
    { symbol: "AVAX-USDT", side: "SHORT", entry: 38.4, current: 37.1, sl: 39.8, tp: 34.2, score: 71, qty: 1.8, mtf: -45, funding: -0.0003 },
  ],
  recent_trades: [
    { symbol: "BTC-USDT", side: "LONG", pnl: 3.21, reason: "TAKE_PROFIT", time: "14:22" },
    { symbol: "ETH-USDT", side: "LONG", pnl: -1.08, reason: "STOP_LOSS", time: "12:45" },
    { symbol: "MATIC-USDT", side: "SHORT", pnl: 2.87, reason: "TAKE_PROFIT", time: "11:10" },
    { symbol: "DOGE-USDT", side: "LONG", pnl: 1.54, reason: "TAKE_PROFIT", time: "09:33" },
    { symbol: "LINK-USDT", side: "SHORT", pnl: -0.92, reason: "STOP_LOSS", time: "08:01" },
  ],
  filters_blocked: [
    { symbol: "XRP-USDT", side: "LONG", filter: "Funding", reason: "Funding +0.08% → longs sobrecomprados" },
    { symbol: "BNB-USDT", side: "LONG", filter: "MTF", reason: "4h tendencia DOWN, confluencia -40" },
    { symbol: "DOGE-USDT", side: "SHORT", filter: "Correlación", reason: "Correlación 0.93 con SHIB-USDT abierto" },
  ],
  stats: { total: 34, wins: 23, losses: 11, win_rate: 67.6, avg_rr: 1.87, max_dd: -3.2 },
  scanner_top: [
    { symbol: "PEPE-USDT", score: 88, vol_spike: 4.2, change: 6.1, category: "micro" },
    { symbol: "SOL-USDT", score: 82, vol_spike: 2.8, change: 4.3, category: "large" },
    { symbol: "WIF-USDT", score: 76, vol_spike: 3.1, change: 5.7, category: "small" },
    { symbol: "AVAX-USDT", score: 71, vol_spike: 2.2, change: 3.8, category: "large" },
    { symbol: "JUP-USDT", score: 68, vol_spike: 2.9, change: 4.1, category: "small" },
  ],
};

function Badge({ text, color }) {
  const colors = {
    green: { bg: "var(--color-background-success)", text: "var(--color-text-success)" },
    red:   { bg: "var(--color-background-danger)",  text: "var(--color-text-danger)" },
    amber: { bg: "var(--color-background-warning)", text: "var(--color-text-warning)" },
    blue:  { bg: "var(--color-background-info)",    text: "var(--color-text-info)" },
    gray:  { bg: "var(--color-background-secondary)", text: "var(--color-text-secondary)" },
  };
  const c = colors[color] || colors.gray;
  return (
    <span style={{ background: c.bg, color: c.text, fontSize: 11, fontWeight: 500, padding: "2px 8px", borderRadius: 4 }}>
      {text}
    </span>
  );
}

function MetricCard({ label, value, sub, color }) {
  return (
    <div style={{ background: "var(--color-background-secondary)", borderRadius: 8, padding: "12px 14px", minWidth: 0 }}>
      <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 500, color: color || "var(--color-text-primary)" }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function PnlBar({ pnl, max = 5 }) {
  const pct = Math.min(Math.abs(pnl) / max * 100, 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ flex: 1, height: 4, background: "var(--color-background-tertiary)", borderRadius: 2, overflow: "hidden" }}>
        <div style={{ width: pct + "%", height: "100%", background: pnl >= 0 ? "var(--color-text-success)" : "var(--color-text-danger)", borderRadius: 2, transition: "width 0.5s" }} />
      </div>
      <span style={{ fontSize: 12, fontWeight: 500, color: pnl >= 0 ? "var(--color-text-success)" : "var(--color-text-danger)", minWidth: 48, textAlign: "right" }}>
        {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}%
      </span>
    </div>
  );
}

function PositionCard({ pos }) {
  const pnl = pos.side === "LONG"
    ? (pos.current - pos.entry) / pos.entry * 100
    : (pos.entry - pos.current) / pos.entry * 100;
  const sl_dist = Math.abs(pos.current - pos.sl) / pos.current * 100;
  const tp_dist = Math.abs(pos.tp - pos.current) / pos.current * 100;
  return (
    <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 8, padding: "12px 14px", background: "var(--color-background-primary)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontWeight: 500, fontSize: 14 }}>{pos.symbol.replace("-USDT", "")}</span>
          <Badge text={pos.side} color={pos.side === "LONG" ? "green" : "red"} />
        </div>
        <span style={{ fontSize: 13, fontWeight: 500, color: pnl >= 0 ? "var(--color-text-success)" : "var(--color-text-danger)" }}>
          {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}%
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 4, fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 8 }}>
        <span>Entry: <b style={{ color: "var(--color-text-primary)" }}>{pos.entry}</b></span>
        <span>SL: <b style={{ color: "var(--color-text-danger)" }}>{pos.sl}</b></span>
        <span>TP: <b style={{ color: "var(--color-text-success)" }}>{pos.tp}</b></span>
      </div>
      <div style={{ display: "flex", gap: 12, fontSize: 11 }}>
        <span style={{ color: "var(--color-text-secondary)" }}>Score <b style={{ color: "var(--color-text-primary)" }}>{pos.score}</b></span>
        <span style={{ color: "var(--color-text-secondary)" }}>MTF <b style={{ color: pos.mtf > 0 ? "var(--color-text-success)" : "var(--color-text-danger)" }}>{pos.mtf > 0 ? "+" : ""}{pos.mtf}</b></span>
        <span style={{ color: "var(--color-text-secondary)" }}>Funding <b>{(pos.funding * 100).toFixed(4)}%</b></span>
      </div>
      <div style={{ marginTop: 8 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--color-text-tertiary)", marginBottom: 2 }}>
          <span>SL -{sl_dist.toFixed(1)}%</span>
          <span>TP +{tp_dist.toFixed(1)}%</span>
        </div>
        <div style={{ height: 5, background: "var(--color-background-tertiary)", borderRadius: 3, position: "relative" }}>
          <div style={{ position: "absolute", left: 0, width: "100%", height: "100%", borderRadius: 3, background: "linear-gradient(90deg, var(--color-background-danger) 0%, var(--color-background-success) 100%)", opacity: 0.3 }} />
          <div style={{
            position: "absolute",
            left: `${sl_dist / (sl_dist + tp_dist) * 100}%`,
            top: -2, width: 9, height: 9, borderRadius: "50%",
            background: "var(--color-text-primary)",
            transform: "translateX(-50%)",
          }} />
        </div>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState(DEMO_DATA);
  const [tab, setTab] = useState("positions");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => {
      setTick(t => t + 1);
      setData(d => ({
        ...d,
        balance: +(d.balance + (Math.random() - 0.48) * 0.5).toFixed(2),
        daily_pnl: +(d.daily_pnl + (Math.random() - 0.48) * 0.05).toFixed(2),
        open_positions: d.open_positions.map(p => ({
          ...p,
          current: +(p.current * (1 + (Math.random() - 0.5) * 0.002)).toFixed(4)
        }))
      }));
    }, 2500);
    return () => clearInterval(id);
  }, []);

  const tabs = ["positions", "trades", "scanner", "filters"];

  return (
    <div style={{ fontFamily: "var(--font-sans)", padding: "16px 0", maxWidth: 680 }}>
      <h2 style={{ fontSize: 14, color: "var(--sr-only)" }} className="sr-only">BingX Bot v2 Live Dashboard</h2>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 500 }}>BingX Bot v2</div>
          <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginTop: 2 }}>
            <span style={{ display: "inline-block", width: 6, height: 6, borderRadius: "50%", background: "#22c55e", marginRight: 5 }} />
            Live · {data.open_positions.length} posiciones · actualizado hace {tick * 2}s
          </div>
        </div>
        <Badge text="DEMO MODE" color="amber" />
      </div>

      {/* Metrics */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginBottom: 16 }}>
        <MetricCard label="Balance" value={`$${data.balance.toFixed(0)}`} sub="USDT disponible" />
        <MetricCard label="P&L hoy" value={`${data.daily_pnl >= 0 ? "+" : ""}${data.daily_pnl.toFixed(2)}%`}
          color={data.daily_pnl >= 0 ? "var(--color-text-success)" : "var(--color-text-danger)"}
          sub={`Max DD: ${data.stats.max_dd}%`}
        />
        <MetricCard label="Win rate" value={`${data.stats.win_rate}%`}
          sub={`${data.stats.wins}W / ${data.stats.losses}L`}
          color="var(--color-text-info)"
        />
        <MetricCard label="Avg R:R" value={`${data.stats.avg_rr}x`}
          sub={`${data.stats.total} trades`}
        />
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 4, marginBottom: 14, borderBottom: "0.5px solid var(--color-border-tertiary)", paddingBottom: 0 }}>
        {tabs.map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            background: "none", border: "none", cursor: "pointer",
            fontSize: 13, padding: "6px 12px",
            color: tab === t ? "var(--color-text-primary)" : "var(--color-text-secondary)",
            fontWeight: tab === t ? 500 : 400,
            borderBottom: tab === t ? "2px solid var(--color-text-primary)" : "2px solid transparent",
            marginBottom: -1,
          }}>
            {t === "positions" ? `Posiciones (${data.open_positions.length})`
             : t === "trades"   ? `Trades (${data.recent_trades.length})`
             : t === "scanner"  ? "Scanner"
             : `Bloqueados (${data.filters_blocked.length})`}
          </button>
        ))}
      </div>

      {/* Tab: Positions */}
      {tab === "positions" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {data.open_positions.length === 0 && (
            <div style={{ color: "var(--color-text-secondary)", fontSize: 13, padding: "20px 0", textAlign: "center" }}>Sin posiciones abiertas</div>
          )}
          {data.open_positions.map(p => <PositionCard key={p.symbol} pos={p} />)}
        </div>
      )}

      {/* Tab: Recent trades */}
      {tab === "trades" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          {data.recent_trades.map((t, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
              <Badge text={t.side} color={t.side === "LONG" ? "green" : "red"} />
              <span style={{ fontWeight: 500, fontSize: 13, flex: 1 }}>{t.symbol.replace("-USDT", "")}</span>
              <Badge text={t.reason} color={t.reason === "TAKE_PROFIT" ? "green" : "red"} />
              <div style={{ minWidth: 110 }}>
                <PnlBar pnl={t.pnl} />
              </div>
              <span style={{ fontSize: 11, color: "var(--color-text-tertiary)", minWidth: 36 }}>{t.time}</span>
            </div>
          ))}
        </div>
      )}

      {/* Tab: Scanner */}
      {tab === "scanner" && (
        <div>
          <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 10 }}>Top monedas explosivas detectadas</div>
          {data.scanner_top.map((c, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 0", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
              <span style={{ fontSize: 12, color: "var(--color-text-tertiary)", minWidth: 16 }}>#{i + 1}</span>
              <span style={{ fontWeight: 500, fontSize: 13, flex: 1 }}>{c.symbol.replace("-USDT", "")}</span>
              <Badge text={c.category} color={c.category === "large" ? "blue" : c.category === "small" ? "green" : "amber"} />
              <div style={{ display: "flex", gap: 8, fontSize: 11, color: "var(--color-text-secondary)" }}>
                <span>Vol {c.vol_spike}x</span>
                <span style={{ color: "var(--color-text-success)" }}>+{c.change}%</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <div style={{ width: 50, height: 4, background: "var(--color-background-tertiary)", borderRadius: 2, overflow: "hidden" }}>
                  <div style={{ width: c.score + "%", height: "100%", background: c.score > 80 ? "var(--color-text-success)" : c.score > 60 ? "var(--color-text-warning)" : "var(--color-text-danger)", borderRadius: 2 }} />
                </div>
                <span style={{ fontSize: 11, fontWeight: 500 }}>{c.score}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Tab: Blocked signals */}
      {tab === "filters" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 2 }}>Señales bloqueadas por los filtros v2</div>
          {data.filters_blocked.map((b, i) => (
            <div key={i} style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 8, padding: "10px 12px" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 5 }}>
                <span style={{ fontWeight: 500, fontSize: 13 }}>{b.symbol.replace("-USDT", "")}</span>
                <Badge text={b.side} color={b.side === "LONG" ? "green" : "red"} />
                <Badge text={b.filter} color="amber" />
              </div>
              <div style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>{b.reason}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
