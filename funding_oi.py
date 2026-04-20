"""
Funding Rate + Open Interest Filter
────────────────────────────────────
El filtro más importante para futuros perpetuos y que casi
ningún bot amateur usa. Lógica:

• Funding muy positivo (+0.05%) → demasiados longs apalancados
  → alto riesgo de flush → SKIP longs
• Funding muy negativo (-0.05%) → demasiados shorts → SKIP shorts
• OI cayendo mientras precio sube → short covering, no tendencia real
• OI subiendo confirma nueva entrada de capital → señal más fuerte
"""

from dataclasses import dataclass
from loguru import logger


@dataclass
class FundingOIResult:
    symbol:            str
    funding_rate:      float    # actual funding rate (e.g. 0.0001 = 0.01%)
    funding_pct:       float    # funding_rate * 100
    oi_current:        float    # open interest actual
    oi_prev:           float    # open interest N bars antes
    oi_delta_pct:      float    # cambio % en OI
    oi_trend:          str      # "rising" / "falling" / "flat"
    long_blocked:      bool     # si se deben bloquear longs
    short_blocked:     bool     # si se deben bloquear shorts
    oi_confirms_long:  bool     # OI subiendo + precio subiendo
    oi_confirms_short: bool     # OI subiendo + precio bajando
    reason:            str      # explicación del fallo/pass


class FundingOIFilter:
    """
    Obtiene y analiza funding rate + open interest de BingX.
    Integración directa con BingXClient.
    """

    def __init__(
        self,
        funding_long_block:  float = 0.0005,    # 0.05% → block longs
        funding_short_block: float = -0.0005,   # -0.05% → block shorts
        oi_min_change_pct:   float = 2.0,        # OI debe moverse >2% para confirmar
        oi_lookback_bars:    int   = 5,           # comparar OI actual vs 5 velas atrás
        require_oi_confirm:  bool  = True,        # exigir OI creciente para señal
    ):
        self.funding_long_block  = funding_long_block
        self.funding_short_block = funding_short_block
        self.oi_min_change_pct   = oi_min_change_pct
        self.oi_lookback_bars    = oi_lookback_bars
        self.require_oi_confirm  = require_oi_confirm

    async def analyze(self, client, symbol: str, current_price: float, prev_price: float) -> FundingOIResult:
        """
        Llama a BingX para obtener funding + OI y devuelve decisión.
        """
        funding_rate = 0.0
        oi_current   = 0.0
        oi_prev      = 0.0

        try:
            # ── Funding rate ──────────────────────────────────────────────
            fr_data = await client._get(
                "/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol}
            )
            fr_val = fr_data.get("data", {}).get("lastFundingRate", "0")
            funding_rate = float(fr_val)

        except Exception as e:
            logger.debug(f"Funding fetch error {symbol}: {e}")

        try:
            # ── Open Interest ─────────────────────────────────────────────
            oi_data = await client._get(
                "/openApi/swap/v2/quote/openInterest", {"symbol": symbol}
            )
            oi_current = float(oi_data.get("data", {}).get("openInterest", 0))

            # OI histórico (últimas N velas de OI)
            oi_hist = await client._get(
                "/openApi/swap/v2/quote/openInterestHist",
                {"symbol": symbol, "period": "15m", "limit": self.oi_lookback_bars + 1},
            )
            hist_list = oi_hist.get("data", [])
            if len(hist_list) >= 2:
                oi_prev = float(hist_list[0].get("sumOpenInterest", oi_current))
            else:
                oi_prev = oi_current

        except Exception as e:
            logger.debug(f"OI fetch error {symbol}: {e}")
            oi_prev = oi_current

        # ── Compute deltas ────────────────────────────────────────────────
        funding_pct  = funding_rate * 100
        oi_delta_pct = (oi_current - oi_prev) / (oi_prev + 1e-10) * 100

        if oi_delta_pct > self.oi_min_change_pct:
            oi_trend = "rising"
        elif oi_delta_pct < -self.oi_min_change_pct:
            oi_trend = "falling"
        else:
            oi_trend = "flat"

        price_up   = current_price > prev_price
        price_down = current_price < prev_price

        # ── Funding blocks ────────────────────────────────────────────────
        long_blocked  = funding_rate >= self.funding_long_block
        short_blocked = funding_rate <= self.funding_short_block

        # ── OI confirmation ───────────────────────────────────────────────
        # Rising OI + price rising = new longs/shorts entering = real momentum
        oi_confirms_long  = oi_trend == "rising" and price_up
        oi_confirms_short = oi_trend == "rising" and price_down

        # Build reason string
        reasons = []
        if long_blocked:
            reasons.append(f"Funding {funding_pct:+.4f}% → longs bloqueados")
        if short_blocked:
            reasons.append(f"Funding {funding_pct:+.4f}% → shorts bloqueados")
        if not reasons:
            reasons.append(f"Funding OK ({funding_pct:+.4f}%)")
        if oi_trend == "rising":
            reasons.append(f"OI ↑ {oi_delta_pct:+.1f}%")
        elif oi_trend == "falling":
            reasons.append(f"OI ↓ {oi_delta_pct:+.1f}%")

        result = FundingOIResult(
            symbol=symbol,
            funding_rate=funding_rate,
            funding_pct=funding_pct,
            oi_current=oi_current,
            oi_prev=oi_prev,
            oi_delta_pct=oi_delta_pct,
            oi_trend=oi_trend,
            long_blocked=long_blocked,
            short_blocked=short_blocked,
            oi_confirms_long=oi_confirms_long,
            oi_confirms_short=oi_confirms_short,
            reason=" | ".join(reasons),
        )

        logger.debug(
            f"[FundingOI] {symbol}: funding={funding_pct:+.4f}% "
            f"OI_Δ={oi_delta_pct:+.1f}% ({oi_trend}) "
            f"{'🚫LONG' if long_blocked else ''} {'🚫SHORT' if short_blocked else ''}"
        )

        return result

    def passes(self, result: FundingOIResult, side: str) -> tuple[bool, str]:
        """
        ¿Pasa el filtro para el side dado (LONG/SHORT)?
        Devuelve (bool, reason)
        """
        if side == "LONG" and result.long_blocked:
            return False, f"Funding muy alto ({result.funding_pct:+.4f}%) → longs sobrecomprados"

        if side == "SHORT" and result.short_blocked:
            return False, f"Funding muy bajo ({result.funding_pct:+.4f}%) → shorts sobrevendidos"

        if self.require_oi_confirm:
            if side == "LONG"  and not result.oi_confirms_long  and result.oi_trend == "falling":
                return False, "OI cayendo con precio subiendo → short covering, no tendencia real"
            if side == "SHORT" and not result.oi_confirms_short and result.oi_trend == "falling":
                return False, "OI cayendo con precio bajando → long covering, no tendencia real"

        return True, "Funding + OI OK"
