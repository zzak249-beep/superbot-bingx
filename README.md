# 🤖 InstitutionalBot v4.0

Bot de trading algorítmico para BingX Perpetual Futures.  
Archivo único, 72/72 tests pasando, listo para Railway.

## Estructura

```
├── institutional_bot_v4.py   ← todo el bot aquí
├── test_bot_v4.py            ← suite de tests
├── requirements.txt
├── Procfile
├── railway.toml
├── railway.json
├── runtime.txt
├── .env.example              ← copia esto a .env con tus keys
└── .gitignore
```

## Deploy en Railway (5 minutos)

1. Sube este repo a GitHub
2. Railway → New Project → Deploy from GitHub
3. Variables → añade una por una (copia de `.env.example`):

```
BINGX_API_KEY        → tu key real
BINGX_API_SECRET     → tu secret real
AUTO_TRADING_ENABLED → false   ← SIEMPRE empieza en false
ACCOUNT_EQUITY       → 500
POSITION_SIZE_USD    → 10
LEVERAGE             → 2
```

4. Deploy → revisa los logs

## Checklist antes de AUTO_TRADING_ENABLED=true

- [ ] Bot corriendo 48h+ sin errores en logs
- [ ] Ves líneas `💡 SYMBOL | Score:XX | Edge:X.Xx` en los logs
- [ ] BingX → Wallet → fondos en **Perpetual Futures** (no Spot)
- [ ] API Key con permisos **Futures Trading** activados
- [ ] `extract_equity` muestra tu balance real (no $0)

## Parámetros clave para $500

| Parámetro | Valor | Por qué |
|---|---|---|
| `POSITION_SIZE_USD` | $10 | 2% del capital por trade |
| `LEVERAGE` | 2x | conservador, evita liquidaciones |
| `MAX_POSITIONS` | 2 | máximo $20 expuestos a la vez |
| `CIRCUIT_BREAKER_PCT` | 3% | pausa si pierdes $15/día |
| `MIN_ENTRY_SCORE` | 75 | solo señales de alta calidad |

## ⚠️ Aviso

Este software es solo para fines educativos.  
El trading con apalancamiento conlleva riesgo de pérdida total del capital.
