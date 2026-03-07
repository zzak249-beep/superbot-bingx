"""
config_pares.py — Lista completa de pares BingX Perpetual Futures
v5.2 — 202 pares activos (217 totales - 15 bloqueados por backtest)
Orden: prioritarios primero (WR > 36%), luego el resto por categoría
"""

# ══════════════════════════════════════════════════════
# ⭐ PRIORITARIOS — Backtest WR > 36%, PnL positivo
# Estos se escanean PRIMERO en cada ciclo
# ══════════════════════════════════════════════════════
PARES_PRIORITARIOS = [
    "BERA-USDT",    # WR 51.7% PnL +$15.32 ⭐
    "PI-USDT",      # WR 56.2% PnL  +$8.56 ⭐
    "OP-USDT",      # WR 46.2% PnL  +$7.97 ⭐
    "NEAR-USDT",    # WR 44.0% PnL  +$7.86 ⭐
    "ARB-USDT",     # WR 39.4% PnL  +$7.84 ⭐
    "LINK-USDT",    # WR 44.8% PnL  +$5.38 ⭐
    "GRASS-USDT",   # WR 39.1% PnL  +$9.62
    "MYX-USDT",     # WR 37.5% PnL  +$5.87
    "KAITO-USDT",   # WR 39.1% PnL  +$5.14
    "ONDO-USDT",    # WR 38.5% PnL  +$2.62
    "LTC-USDT",     # WR 43.5% PnL  +$1.80
    "POPCAT-USDT",  # WR 37.0% PnL  +$2.36
    "AVAX-USDT",    # WR 36.7% PnL  +$1.01
    "INJ-USDT",     # WR 37.0% PnL  +$0.46
]

# ══════════════════════════════════════════════════════
# 🚫 BLOQUEADOS — Backtest WR < 32%, PnL negativo
# El bot los ignora completamente
# ══════════════════════════════════════════════════════
PARES_BLOQUEADOS = [
    "BTC-USDT",     # WR 20.8% PnL -$6.10
    "ETH-USDT",     # WR 23.1% PnL -$5.49
    "DOGE-USDT",    # WR 22.2% PnL -$6.12
    "ADA-USDT",     # WR 24.1% PnL -$6.48
    "HYPE-USDT",    # WR 20.8% PnL -$9.35
    "WIF-USDT",     # WR 25.0% PnL -$8.13
    "BNB-USDT",     # WR 29.6% PnL -$3.66
    "XRP-USDT",     # WR 27.6% PnL -$2.06
    "RUNE-USDT",    # WR 31.2% PnL -$4.51
    "SEI-USDT",     # WR 29.0% PnL -$3.65
    "JUP-USDT",     # WR 29.6% PnL -$4.15
    "SUI-USDT",     # WR 32.0% PnL -$1.71
    "ATOM-USDT",    # WR 27.8% PnL -$2.11
    "SOL-USDT",     # WR 32.3% PnL -$1.67
    "TIA-USDT",     # WR 33.3% PnL -$0.59
]

_BLOQUEADOS_SET = set(PARES_BLOQUEADOS)

# ══════════════════════════════════════════════════════
# TODOS LOS PARES — 202 activos (prioritarios + resto)
# ══════════════════════════════════════════════════════

_RESTO = [
    # ── L2 / Ecosistemas ─────────────────────────────
    "APT-USDT","MATIC-USDT","FTM-USDT","IMX-USDT","MANTA-USDT",
    "STRK-USDT","ZETA-USDT","BLAST-USDT","STX-USDT","CFX-USDT",
    "LAYER-USDT","MOVE-USDT","IP-USDT","ME-USDT","POL-USDT",

    # ── DeFi ─────────────────────────────────────────
    "AAVE-USDT","UNI-USDT","MKR-USDT","CRV-USDT","COMP-USDT",
    "SUSHI-USDT","YFI-USDT","BAL-USDT","1INCH-USDT","CAKE-USDT",
    "GMX-USDT","DYDX-USDT","SNX-USDT","LDO-USDT","RPL-USDT",
    "PENDLE-USDT","ENA-USDT","PYTH-USDT","RAY-USDT","JTO-USDT",
    "W-USDT","EIGEN-USDT","MORPHO-USDT","SKY-USDT","COW-USDT",
    "LQTY-USDT","CVX-USDT","FXS-USDT","ALPHA-USDT","GME-USDT",

    # ── AI / Data ────────────────────────────────────
    "RNDR-USDT","FET-USDT","AGIX-USDT","GRT-USDT","OCEAN-USDT",
    "TAO-USDT","WLD-USDT","ARKM-USDT","VIRTUAL-USDT","AI16Z-USDT",
    "AIXBT-USDT","ZEREBRO-USDT","GRIFFAIN-USDT",

    # ── Memes ────────────────────────────────────────
    "PEPE-USDT","BONK-USDT","FLOKI-USDT","SHIB-USDT","MEME-USDT",
    "NEIRO-USDT","TURBO-USDT","MOG-USDT","BOME-USDT","MEW-USDT",
    "PONKE-USDT","GOAT-USDT","PNUT-USDT","ACT-USDT",
    "FARTCOIN-USDT","TRUMP-USDT","MELANIA-USDT","MOODENG-USDT",
    "CHILLGUY-USDT","PENGU-USDT","BANANA-USDT","DOGS-USDT",
    "NOT-USDT","HMSTR-USDT","CATI-USDT","MAJOR-USDT",

    # ── Gaming / Metaverso ───────────────────────────
    "SAND-USDT","MANA-USDT","AXS-USDT","GALA-USDT","ILV-USDT",
    "ENS-USDT","BLUR-USDT","BEAM-USDT","PIXEL-USDT","PORTAL-USDT",
    "RON-USDT","YGG-USDT","ALT-USDT","MAGIC-USDT",

    # ── Layer 1 alternativos ─────────────────────────
    "DOT-USDT","HBAR-USDT","ALGO-USDT","ICP-USDT","FLOW-USDT",
    "EGLD-USDT","KAVA-USDT","KAS-USDT","ROSE-USDT","IOTA-USDT",
    "NEO-USDT","QTUM-USDT","ETC-USDT","BCH-USDT","ZEC-USDT",
    "XMR-USDT","DASH-USDT","TRX-USDT","XTZ-USDT","EOS-USDT",
    "XLM-USDT","VET-USDT","THETA-USDT","ZIL-USDT","ONE-USDT",

    # ── Infraestructura / Oracle / Storage ───────────
    "BAND-USDT","API3-USDT","STORJ-USDT","ANKR-USDT","LRC-USDT",
    "GNO-USDT","CELO-USDT","ZRX-USDT","BAT-USDT","ENJ-USDT",
    "CHZ-USDT","AUDIO-USDT","JASMY-USDT","CTSI-USDT","NMR-USDT",
    "SKL-USDT","CELR-USDT","ACH-USDT","WAVES-USDT","RVN-USDT",
    "TRB-USDT","DUSK-USDT","REEF-USDT","HIGH-USDT","ID-USDT",
    "HOOK-USDT","CFX-USDT","SXP-USDT","SUPER-USDT","TFUEL-USDT",
    "LUNA-USDT","LUNC-USDT","CVC-USDT","POLS-USDT","SPELL-USDT",

    # ── Exchange tokens ──────────────────────────────
    "CRO-USDT","OKB-USDT","WOO-USDT","BGB-USDT",

    # ── Recientes alto volumen ────────────────────────
    "USUAL-USDT","RESOLV-USDT","COOKIE-USDT","UXLINK-USDT",
    "TST-USDT","BLUM-USDT","DEFI-USDT","CLANKER-USDT",
]

# Filtrar bloqueados del resto (por si hay duplicados)
_RESTO_LIMPIO = [p for p in _RESTO if p not in _BLOQUEADOS_SET]

# Lista final: prioritarios primero, luego resto
PARES = PARES_PRIORITARIOS + _RESTO_LIMPIO

if __name__ == "__main__":
    print(f"Prioritarios : {len(PARES_PRIORITARIOS)}")
    print(f"Bloqueados   : {len(PARES_BLOQUEADOS)}")
    print(f"Resto        : {len(_RESTO_LIMPIO)}")
    print(f"TOTAL ACTIVOS: {len(PARES)}")
