"""
reset_errores.py — Limpia errores API acumulados en bot_memoria.json
Ejecutar en Railway con: python reset_errores.py

Esto desbloquea todos los pares que fueron bloqueados por errores de API
(margin, connectivity, etc.) pero NO borra el historial de trades ni PnL.
"""
import json, os, sys
from datetime import datetime, timezone

# Buscar bot_memoria.json
PATHS = [
    "bot_memoria.json",
    "/data/bot_memoria.json",
    "/app/bot_memoria.json",
]

memoria_path = None
for p in PATHS:
    if os.path.exists(p):
        memoria_path = p
        break

if not memoria_path:
    # Intentar desde MEMORY_DIR en config
    try:
        import config
        if config.MEMORY_DIR:
            p = os.path.join(config.MEMORY_DIR, "bot_memoria.json")
            if os.path.exists(p):
                memoria_path = p
    except Exception:
        pass

if not memoria_path:
    print("❌ No se encontró bot_memoria.json")
    print("   Busca el archivo en Railway (volume montado) y ejecuta este script allí.")
    sys.exit(1)

print(f"✅ Encontrado: {memoria_path}")

with open(memoria_path, encoding="utf-8") as f:
    data = json.load(f)

pares_stats = data.get("pares_stats", {})
print(f"\n📊 Pares en memoria: {len(pares_stats)}")

# Mostrar pares bloqueados actualmente
bloqueados = [(p, s) for p, s in pares_stats.items() if s.get("errores", 0) >= 5]
print(f"🔴 Pares bloqueados por errores (≥5): {len(bloqueados)}")
for par, stats in bloqueados:
    print(f"   {par}: errores={stats['errores']} trades={stats.get('trades',0)} wins={stats.get('wins',0)} pnl={stats.get('pnl_total',0):.2f}")

# Resetear errores
resetados = 0
for par, stats in pares_stats.items():
    if stats.get("errores", 0) > 0:
        print(f"  🔧 {par}: errores {stats['errores']} → 0")
        stats["errores"] = 0
        stats["ultimo_error_ts"] = ""
        resetados += 1

print(f"\n✅ {resetados} pares desbloqueados")

# Guardar
with open(memoria_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"💾 Guardado en {memoria_path}")
print("\n⚡ Reinicia el bot para que los cambios tengan efecto.")
