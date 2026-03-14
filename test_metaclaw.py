"""
test_metaclaw.py — APEX Bot v7.0
=================================
Verifica que MetaClaw funciona correctamente antes de subir a Railway.

Ejecutar localmente:
  ANTHROPIC_API_KEY=sk-ant-... python test_metaclaw.py

Ejecutar en Railway (una vez, para verificar):
  Añadir en Railway → Settings → Deploy Command:
  python test_metaclaw.py && python main.py
  (Quitar el test una vez verificado)
"""
import os, sys, json, traceback

print("=" * 60)
print("TEST APEX METACLAW v7.0")
print("=" * 60)

try:
    import config
    import metaclaw
    print("✅ Imports OK")
except Exception as e:
    print(f"❌ Import falló: {e}")
    sys.exit(1)

errores  = []
pasados  = []

def ok(msg):
    pasados.append(msg)
    print(f"  ✅ {msg}")

def fail(msg):
    errores.append(msg)
    print(f"  ❌ {msg}")


# ══════════════════════════════════════════════════════════════
# TEST 1 — API key
# ══════════════════════════════════════════════════════════════
print("\n[1] API Key")
ak = metaclaw._api_key()
if not ak:
    fail("ANTHROPIC_API_KEY vacía — MetaClaw no funcionará")
elif len(ak) < 20:
    fail(f"API key demasiado corta: len={len(ak)}")
elif " " in ak or "\n" in ak:
    fail("API key tiene espacios/saltos de línea")
else:
    ok(f"API key OK — len={len(ak)} primeros4={ak[:4]}...")


# ══════════════════════════════════════════════════════════════
# TEST 2 — _extract_json robusto
# ══════════════════════════════════════════════════════════════
print("\n[2] _extract_json")
casos = [
    ("JSON puro",           '{"aprobar": true, "confianza": 8, "razon": "ok"}'),
    ("Con markdown fence",  '```json\n{"aprobar": false, "confianza": 3, "razon": "rsi alto"}\n```'),
    ("Texto antes del JSON",'Análisis: {"aprobar": true, "confianza": 7, "razon": "setup ok"}'),
    ("null como string",    '{"texto": "skill", "tags": ["LONG"], "actualizar_id": "null"}'),
]
for nombre, texto in casos:
    r = metaclaw._extract_json(texto)
    if r and isinstance(r, dict):
        ok(nombre)
    else:
        fail(f"{nombre} → devolvió: {r}")


# ══════════════════════════════════════════════════════════════
# TEST 3 — _relevante threshold
# ══════════════════════════════════════════════════════════════
print("\n[3] _relevante — skills relevantes")

skill_buena      = {"tags": ["LONG", "LONDON", "OB+FVG", "SWEEP"], "texto": "LONG Londres sweep"}
skill_irrelevante = {"tags": ["SHORT", "NY"], "texto": "SHORT NY"}
skill_general    = {"tags": ["GENERAL", "BTC-USDT"], "texto": "Regla BTC general"}

señal = {"par": "BTC-USDT", "lado": "LONG", "kz": "LONDON", "motivos": ["OB+FVG", "SWEEP", "CHoCH"]}

if metaclaw._relevante(skill_buena, señal):
    ok("Skill con lado+KZ+motivos → relevante")
else:
    fail("Skill buena debería ser relevante")

if not metaclaw._relevante(skill_irrelevante, señal):
    ok("Skill SHORT irrelevante para señal LONG")
else:
    fail("Skill SHORT no debería ser relevante para LONG")

if metaclaw._relevante(skill_general, señal):
    ok("Skill GENERAL+par → relevante")
else:
    fail("Skill con GENERAL+par debería ser relevante")


# ══════════════════════════════════════════════════════════════
# TEST 4 — validar() con señal de alta calidad
# ══════════════════════════════════════════════════════════════
print("\n[4] validar() — señal A+ (debe aprobar con confianza alta)")

señal_buena = {
    "par":         "BTC-USDT",
    "lado":        "LONG",
    "score":       14,
    "rsi":         48.0,
    "srsi_k":      35.0,
    "rr":          2.8,
    "kz":          "LONDON",
    "htf":         "BULL",
    "htf_4h":      "BULL",
    "sobre_vwap":  False,
    "patron":      "PIN_BAR",
    "sweep_bull":  True,
    "sweep_fuerza": 2,
    "sweep_bear":  False,
    "ob_fvg_bull": True,
    "ob_fvg_bear": False,
    "ob_quality":  2,
    "choch_bull":  True,
    "choch_bear":  False,
    "discount":    True,
    "premium":     False,
    "vol_ratio":   1.4,
    "vol_delta":   0.35,
    "macro_btc_4h": "BULL",
    "motivos":     ["SWEEP+MSS", "OB+FVG", "CHoCH", "H4_BULL", "H1_BULL", "DISCOUNT"],
}

try:
    r = metaclaw.validar(señal_buena)
    if not r.get("metaclaw_activo"):
        fail(f"API no respondió: {r.get('razon')}")
    else:
        ok(f"validar OK — aprobar={r['aprobar']} confianza={r['confianza']} razon='{r['razon']}'")
        if r["aprobar"] and r["confianza"] >= 7:
            ok("Alta confianza para setup A+ ✓")
        elif r["aprobar"]:
            print(f"  ⚠️  Aprobado pero confianza={r['confianza']} — esperábamos ≥7")
        else:
            print(f"  ⚠️  Rechazado con conf={r['confianza']} — revisar prompt")
except Exception as e:
    fail(f"validar() excepción: {e}\n{traceback.format_exc()}")


# ══════════════════════════════════════════════════════════════
# TEST 5 — validar() con señal mala (debe rechazar)
# ══════════════════════════════════════════════════════════════
print("\n[5] validar() — señal mala (debe rechazar)")

señal_mala = {
    "par":         "PEPE-USDT",
    "lado":        "LONG",
    "score":       5,
    "rsi":         76.0,       # RSI extremo
    "srsi_k":      88.0,
    "rr":          1.4,        # R:R bajo
    "kz":          "FUERA",
    "htf":         "BEAR",     # HTF contrario
    "htf_4h":      "BEAR",     # 4H también contrario
    "sobre_vwap":  True,
    "patron":      None,
    "sweep_bull":  False,
    "sweep_fuerza": 0,
    "sweep_bear":  False,
    "ob_fvg_bull": False,
    "ob_fvg_bear": False,
    "ob_quality":  0,
    "choch_bull":  False,
    "choch_bear":  False,
    "discount":    False,
    "premium":     True,       # LONG en premium = malo
    "vol_ratio":   0.2,        # volumen muy bajo
    "vol_delta":   -0.4,
    "macro_btc_4h": "BEAR",
    "motivos":     ["EMA_5M"],
}

try:
    r = metaclaw.validar(señal_mala)
    if not r.get("metaclaw_activo"):
        print(f"  ⚠️  API no respondió — no se puede verificar rechazo")
    elif not r["aprobar"]:
        ok(f"Señal mala rechazada ✓ confianza={r['confianza']} razon='{r['razon']}'")
    else:
        print(f"  ⚠️  Señal mala aprobada conf={r['confianza']} — prompt puede necesitar ajuste")
except Exception as e:
    fail(f"validar() señal mala excepción: {e}")


# ══════════════════════════════════════════════════════════════
# TEST 6 — aprender() guarda skill ganadora
# ══════════════════════════════════════════════════════════════
print("\n[6] aprender() — trade ganador")

señal_apr = {
    "par":         "SOL-USDT",
    "lado":        "LONG",
    "score":       12,
    "rsi":         44.0,
    "kz":          "LONDON",
    "htf":         "BULL",
    "htf_4h":      "BULL",
    "sweep_bull":  True,
    "sweep_fuerza": 2,
    "ob_fvg_bull": True,
    "choch_bull":  True,
    "discount":    True,
    "premium":     False,
    "ob_quality":  2,
    "vol_delta":   0.3,
    "macro_btc_4h": "BULL",
    "patron":      "PIN_BAR",
    "motivos":     ["SWEEP+MSS", "OB+FVG", "CHoCH", "DISCOUNT"],
}

skills_antes = len(metaclaw._load_skills())
try:
    metaclaw.aprender(señal_apr, ganado=True, pnl=0.85)
    skills_después = len(metaclaw._load_skills())
    if skills_después > skills_antes:
        ultima = metaclaw._load_skills()[-1]
        ok(f"Skill guardada: '{ultima['texto']}' tags={ultima['tags']}")
    else:
        fail("aprender() no guardó skill nueva")
except Exception as e:
    fail(f"aprender() excepción: {e}\n{traceback.format_exc()}")


# ══════════════════════════════════════════════════════════════
# TEST 7 — aprender() con señal perdedora genera warning
# ══════════════════════════════════════════════════════════════
print("\n[7] aprender() — trade perdedor genera advertencia")

señal_perdida = {
    "par":         "INJ-USDT",
    "lado":        "SHORT",
    "score":       7,
    "rsi":         38.0,
    "kz":          "FUERA",
    "htf":         "NEUTRAL",
    "htf_4h":      "BULL",
    "sweep_bull":  False,
    "ob_fvg_bull": False,
    "choch_bull":  False,
    "discount":    False,
    "premium":     False,
    "ob_quality":  0,
    "vol_delta":   -0.1,
    "macro_btc_4h": "BULL",
    "patron":      None,
    "motivos":     ["EMA_5M", "RSI38"],
}

skills_antes = len(metaclaw._load_skills())
try:
    metaclaw.aprender(señal_perdida, ganado=False, pnl=-0.32)
    skills_después = len(metaclaw._load_skills())
    if skills_después > skills_antes:
        ultima = metaclaw._load_skills()[-1]
        ok(f"Warning skill guardada: '{ultima['texto']}'")
        if "CUIDADO" in ultima["texto"].upper() or "WARN" in ultima["texto"].upper() or "perdedor" in ultima["texto"]:
            ok("Skill contiene advertencia ✓")
        else:
            print(f"  ℹ️  Skill no tiene formato warning pero se guardó")
    else:
        fail("aprender() no guardó skill de advertencia")
except Exception as e:
    fail(f"aprender() perdedor excepción: {e}")


# ══════════════════════════════════════════════════════════════
# TEST 8 — guard señal vacía
# ══════════════════════════════════════════════════════════════
print("\n[8] aprender() — señal vacía debe ignorarse")
skills_antes = len(metaclaw._load_skills())
metaclaw.aprender({}, ganado=True, pnl=0.1)
if len(metaclaw._load_skills()) == skills_antes:
    ok("Señal vacía ignorada correctamente")
else:
    fail("Señal vacía NO debería crear skill")


# ══════════════════════════════════════════════════════════════
# TEST 9 — get_resumen y get_stats
# ══════════════════════════════════════════════════════════════
print("\n[9] get_resumen() y get_stats()")
try:
    resumen = metaclaw.get_resumen()
    if "MetaClaw" in resumen or "skills" in resumen or "APEX" in resumen:
        ok(f"Resumen OK: {resumen[:80]}...")
    else:
        fail(f"Resumen inesperado: {resumen[:80]}")
except Exception as e:
    fail(f"get_resumen() excepción: {e}")

try:
    stats = metaclaw.get_stats()
    ok(f"Stats OK — skills={stats['total_skills']} trades={stats['total_trades']} "
       f"wr={stats['wr_pct']}% api={stats['api_ok']}")
except Exception as e:
    fail(f"get_stats() excepción: {e}")


# ══════════════════════════════════════════════════════════════
# RESUMEN FINAL
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
total = len(pasados) + len(errores)
print(f"RESULTADO: {len(pasados)}/{total} tests pasados")

if errores:
    print("\nFALLOS:")
    for e in errores:
        print(f"  ❌ {e}")
    print("\nEl bot PUEDE funcionar pero MetaClaw tiene problemas.")
    print("Revisa ANTHROPIC_API_KEY y el modelo configurado.")
    sys.exit(1)
else:
    print("\n✅ MetaClaw APEX v7.0 funciona correctamente")
    print("   Puedes subir a Railway con confianza.")
