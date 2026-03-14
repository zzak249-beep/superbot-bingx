"""
metaclaw.py — APEX MetaClaw v7.0 [INSTITUCIONAL]
=================================================
IA que piensa como un trader institucional:
  - Rechaza setups que no tienen sweep previo
  - Entiende la lógica de la trampa de liquidez
  - Aprende patrones ganadores específicos por sesión y par
  - Sistema de skills con decay: skills viejas pesan menos
"""

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests
import config

log = logging.getLogger("metaclaw")

_CLAUDE_MODEL  = "claude-sonnet-4-6"
_skills_lock   = threading.Lock()


def _skills_path() -> str:
    base = config.MEMORY_DIR or ""
    return os.path.join(base, "apex_skills.json") if base else "apex_skills.json"


def _load_skills() -> list:
    try:
        p = _skills_path()
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, list) else []
    except Exception as e:
        log.warning(f"[MCL] load error: {e}")
    return []


def _save_skills(skills: list):
    with _skills_lock:
        try:
            # Mantener máx 120, ordenadas por utilidad (WR × trades)
            if len(skills) > 120:
                skills = sorted(
                    skills,
                    key=lambda s: (s.get("wins", 0) / max(s.get("trades", 1), 1)) * min(s.get("trades", 0), 10),
                    reverse=True,
                )[:120]
            with open(_skills_path(), "w", encoding="utf-8") as f:
                json.dump(skills, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.warning(f"[MCL] save error: {e}")


def _relevante(skill: dict, señal: dict) -> bool:
    tags    = set(skill.get("tags", []))
    lado    = señal.get("lado", "")
    kz      = señal.get("kz", "")
    motivos = set(señal.get("motivos", []))
    par     = señal.get("par", "")
    pts = 0
    if lado and lado in tags:           pts += 3
    if kz   and kz   in tags:          pts += 2
    if any(t in motivos for t in tags): pts += 2
    if par  and par   in tags:          pts += 4
    if "GENERAL" in tags:               pts += 1
    return pts >= 3


def _api_key() -> str:
    return (os.getenv("ANTHROPIC_API_KEY", "") or "").strip()


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    for attempt in [
        lambda t: json.loads(t.strip()),
        lambda t: json.loads(re.sub(r"```(?:json)?\s*", "", t).replace("```", "").strip()),
        lambda t: json.loads(re.search(r"\{.*\}", t, re.DOTALL).group()),
    ]:
        try:
            return attempt(text)
        except Exception:
            pass
    return None


def _call_claude(system: str, user: str, max_tokens: int = 150, timeout: int = 18) -> Optional[str]:
    key = _api_key()
    if not key:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": _CLAUDE_MODEL, "max_tokens": max_tokens,
                  "system": system, "messages": [{"role": "user", "content": user}]},
            timeout=timeout,
        )
        if r.status_code != 200:
            log.warning(f"[MCL] API {r.status_code}: {r.text[:150]}")
            return None
        return r.json()["content"][0]["text"].strip()
    except requests.exceptions.Timeout:
        log.warning("[MCL] Timeout")
        return None
    except Exception as e:
        log.warning(f"[MCL] Error: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# VALIDAR — PROMPT INSTITUCIONAL
# ══════════════════════════════════════════════════════════════

_SYSTEM_VALIDAR = """Eres un trader institucional ICT/SMC con 15 años de experiencia.
Validas señales de trading de futuros perpetuos.

RESPONDE SOLO JSON sin markdown:
{"aprobar": true/false, "confianza": 1-10, "razon": "max 80 chars"}

═══ RECHAZAR SIEMPRE (aprobar=false) ═══
1. Sin sweep de liquidez previo Y sin CHoCH — setup no institucional
2. RSI > 73 en LONG o < 27 en SHORT — precio sobreextendido
3. R:R < 2.0 — riesgo no justifica beneficio
4. HTF_1H contrario al trade Y HTF_4H también contrario — triple contradicción
5. Score < 8 sin SWEEP+MSS ni OB+FVG — señal débil
6. Fuera de killzone Y score < 9 — sin liquidez institucional
7. OB sin displacement posterior — OB de baja calidad
8. BTC macro doble BEAR y trade es LONG en altcoin (excepto score >= 12)
9. Vol_ratio < 0.25 — vela muerta, sin participación
10. LONG en zona premium SIN sweep previo que justifique

═══ APROBAR ALTA CONFIANZA (8-10) ═══
Todos estos presentes:
- SWEEP + MSS/CHoCH confirmado
- OB+FVG en zona
- Killzone activa (London o NY preferiblemente)
- HTF_4H y HTF_1H alineados
- DISCOUNT para LONG / PREMIUM para SHORT
- Score >= 11
- Vol_delta en dirección del trade

═══ CONFIANZA MEDIA (5-7) ═══
- Sweep sin MSS pero con OB+FVG
- CHoCH sin sweep pero con score alto
- Señal en range (mercado lateral) — TP conservador

Piensa: ¿Estaría un banco entrando aquí? Si no, rechaza."""


def validar(señal: dict) -> dict:
    fallback = {"aprobar": True, "confianza": 5, "razon": "offline", "metaclaw_activo": False}
    if not _api_key():
        return fallback

    skills     = _load_skills()
    relevantes = sorted([s for s in skills if _relevante(s, señal)],
                        key=lambda s: s.get("wins", 0) / max(s.get("trades", 1), 1),
                        reverse=True)[:8]

    skills_txt = "SKILLS:\n" + "\n".join(
        f"• WR={s['wins']}/{s['trades']} | {s['texto']}" for s in relevantes
    ) if relevantes else "SKILLS: ninguna aún."

    s = señal
    user = f"""SEÑAL: {s.get('par')} {s.get('lado')} Score:{s.get('score',0)}/20
RSI:{s.get('rsi',50):.1f} | StochRSI:{s.get('srsi_k',50):.0f} | R:R:{s.get('rr',0):.2f}
KZ:{s.get('kz','FUERA')} | HTF_1H:{s.get('htf','?')} | HTF_4H:{s.get('htf_4h','?')}
VWAP:{'SOBRE' if s.get('sobre_vwap') else 'BAJO'} | Patron:{s.get('patron') or 'ninguno'}
Sweep:{('SÍ fuerza='+str(s.get('sweep_fuerza',0))) if (s.get('sweep_bull') or s.get('sweep_bear')) else 'NO'}
OB_quality:{s.get('ob_quality',0)} | OB+FVG:{'SÍ' if (s.get('ob_fvg_bull') or s.get('ob_fvg_bear')) else 'NO'}
CHoCH:{'SÍ' if (s.get('choch_bull') or s.get('choch_bear')) else 'NO'}
Premium:{s.get('premium',False)} | Discount:{s.get('discount',False)}
Vol_ratio:{s.get('vol_ratio',1):.1f} | Vol_delta:{s.get('vol_delta',0):.2f}
BTC_macro_4H:{s.get('macro_btc_4h','NEUTRAL')}
Señales: {' + '.join(s.get('motivos', []))}

{skills_txt}"""

    respuesta = _call_claude(_SYSTEM_VALIDAR, user, max_tokens=120, timeout=15)
    if not respuesta:
        return fallback

    data = _extract_json(respuesta)
    if not data:
        log.warning(f"[MCL] no JSON: {respuesta[:100]}")
        return fallback

    try:
        return {
            "aprobar":         bool(data.get("aprobar", True)),
            "confianza":       max(1, min(10, int(data.get("confianza", 5)))),
            "razon":           str(data.get("razon", ""))[:80],
            "ajuste_sl":       0.0,
            "metaclaw_activo": True,
        }
    except Exception as e:
        log.warning(f"[MCL] parse error: {e}")
        return fallback


# ══════════════════════════════════════════════════════════════
# APRENDER — EXTRAE PATRONES INSTITUCIONALES
# ══════════════════════════════════════════════════════════════

_SYSTEM_APRENDER = """Eres MetaClaw, aprendes de trades para mejorar.

RESPONDE SOLO JSON sin markdown:
{"texto": "skill max 100 chars", "tags": ["LONG/SHORT","KZ","INDICADOR",...], "actualizar_id": "id_o_null"}

REGLAS para skills BUENAS (específicas y accionables):
✅ "SWEEP+MSS+CHoCH LONDON LONG → muy alta fiabilidad cuando discount"
✅ "OB+FVG en H1 BULL + London open = setup A+ consistente"
✅ "CUIDADO: BTC macro BEAR + LONG altcoin = pérdida casi segura"
✅ "BTC/ETH/SOL: SWEEP+OB+FVG NY = setup más rentable del sistema"
✅ "SHORT sin sweep previo = trampa, evitar siempre"

Para trades PERDIDOS genera warnings:
✅ "CUIDADO: RSI>65 LONDON sin sweep = overextended, volver"
✅ "WARN: OB quality 0 sin displacement = entrada falsa"

Tags útiles: par, LONG/SHORT, KZ, motivos, SWEEP, MSS, OB_FVG, etc."""


def aprender(señal: dict, ganado: bool, pnl: float):
    if not _api_key():
        return
    par  = señal.get("par", "")
    lado = señal.get("lado", "")
    if not par or not lado:
        return

    skills     = _load_skills()
    motivos    = señal.get("motivos", [])
    kz         = señal.get("kz", "FUERA")
    htf        = señal.get("htf", "NEUTRAL")
    htf_4h     = señal.get("htf_4h", "NEUTRAL")
    patron     = señal.get("patron") or "ninguno"
    rsi        = señal.get("rsi", 50)
    sweep      = señal.get("sweep_bull") or señal.get("sweep_bear")
    ob_fvg     = señal.get("ob_fvg_bull") or señal.get("ob_fvg_bear")
    choch      = señal.get("choch_bull") or señal.get("choch_bear")
    discount   = señal.get("discount", False)
    premium    = señal.get("premium", False)
    ob_q       = señal.get("ob_quality", 0)
    vol_delta  = señal.get("vol_delta", 0)
    macro      = señal.get("macro_btc_4h", "NEUTRAL")
    score      = señal.get("score", 0)
    resultado  = f"{'GANADO ✅' if ganado else 'PERDIDO ❌'} PnL={pnl:+.4f} USDT"

    # Buscar skill existente para actualizar
    upd_id = None
    for s in skills:
        tags_s = set(s.get("tags", []))
        if lado in tags_s and kz in tags_s and len(tags_s.intersection(set(motivos))) >= 2:
            upd_id = s.get("id")
            break

    recientes = sorted(skills, key=lambda x: x.get("updated", ""), reverse=True)[:5]
    rec_txt = "\n".join(f"• [{s['wins']}/{s['trades']}] {s['texto']}" for s in recientes)

    user = f"""Trade cerrado:
{par} | {lado} | Score:{score}/20 | KZ:{kz}
HTF_1H:{htf} | HTF_4H:{htf_4h} | RSI:{rsi:.1f}
Sweep:{sweep} | OB+FVG:{ob_fvg} | CHoCH:{choch} | OB_quality:{ob_q}
Discount:{discount} | Premium:{premium} | Vol_delta:{vol_delta:.2f}
Patron:{patron} | BTC_macro:{macro}
Señales: {' + '.join(motivos[:6])}
Resultado: {resultado}

{'Actualizar ID: ' + upd_id if upd_id else 'Crear nueva skill'}
Skills recientes:
{rec_txt}"""

    respuesta = _call_claude(_SYSTEM_APRENDER, user, max_tokens=220, timeout=25)
    if not respuesta:
        _skill_simple(skills, señal, ganado)
        return

    data = _extract_json(respuesta)
    if not data:
        _skill_simple(skills, señal, ganado)
        return

    try:
        texto  = str(data.get("texto", ""))[:100].strip()
        tags   = [t for t in (data.get("tags", []) or []) if isinstance(t, str)]
        act_id = data.get("actualizar_id")
        if act_id in (None, "null", "none", "", "undefined"):
            act_id = None

        if not texto:
            _skill_simple(skills, señal, ganado)
            return

        now = datetime.now(timezone.utc).isoformat()

        if act_id:
            for s in skills:
                if s.get("id") == act_id:
                    s["trades"]  = s.get("trades", 0) + 1
                    s["wins"]    = s.get("wins", 0) + (1 if ganado else 0)
                    s["texto"]   = texto
                    s["updated"] = now
                    _save_skills(skills)
                    wr = s["wins"] / s["trades"] * 100
                    log.info(f"[MCL] Skill actualizada [{s['wins']}/{s['trades']} {wr:.0f}%]: {texto}")
                    return

        nueva = {"id": str(uuid.uuid4())[:8], "texto": texto, "tags": tags,
                 "trades": 1, "wins": 1 if ganado else 0,
                 "created": now, "updated": now}
        skills.append(nueva)
        _save_skills(skills)
        log.info(f"[MCL] {'✅' if ganado else '📚'} Nueva skill: {texto}")

    except Exception as e:
        log.warning(f"[MCL] aprender error: {e}")
        _skill_simple(skills, señal, ganado)


def _skill_simple(skills: list, señal: dict, ganado: bool):
    lado = señal.get("lado", "?")
    kz   = señal.get("kz", "FUERA")
    mots = señal.get("motivos", [])
    res  = "ganador" if ganado else "CUIDADO — perdedor"
    texto = f"{lado} {kz} {' + '.join(mots[:3])} → {res}"[:100]
    tags  = [t for t in [lado, kz] + mots[:4] if t]
    now   = datetime.now(timezone.utc).isoformat()
    skills.append({"id": str(uuid.uuid4())[:8], "texto": texto, "tags": tags,
                   "trades": 1, "wins": 1 if ganado else 0, "created": now, "updated": now})
    _save_skills(skills)


def get_resumen() -> str:
    skills = _load_skills()
    if not skills:
        return "🤖 *APEX MetaClaw v7*: Sin skills aún"
    tt = sum(s.get("trades", 0) for s in skills)
    tw = sum(s.get("wins", 0)   for s in skills)
    wr = tw / tt * 100 if tt > 0 else 0
    top = sorted([s for s in skills if s.get("trades", 0) >= 2],
                 key=lambda s: s["wins"] / max(s["trades"], 1), reverse=True)[:3] or skills[:3]
    top_txt = "\n".join(f"  • [{s['wins']}/{s['trades']} {s['wins']/max(s['trades'],1)*100:.0f}%] {s['texto']}" for s in top)
    return f"🦞 *APEX MetaClaw v7* — {len(skills)} skills | WR:{wr:.0f}%\n{top_txt}"


def get_stats() -> dict:
    skills = _load_skills()
    tt = sum(s.get("trades", 0) for s in skills)
    tw = sum(s.get("wins", 0)   for s in skills)
    return {"total_skills": len(skills), "total_trades": tt, "total_wins": tw,
            "wr_pct": round(tw/tt*100, 1) if tt > 0 else 0, "api_ok": bool(_api_key())}
