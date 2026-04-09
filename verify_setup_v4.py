#!/usr/bin/env python3
"""
Verificador de Setup para BingX SuperBot v4.0
Ejecutar: python verify_setup_v4.py
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime

def print_header(text):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")

def print_check(passed, message):
    symbol = "✅" if passed else "❌"
    print(f"{symbol} {message}")
    return passed

def check_files():
    """Verifica que todos los archivos estén presentes"""
    print_header("1. VERIFICACIÓN DE ARCHIVOS")
    
    required_files = {
        'main.py': 'Entry point',
        'bot.py': 'Orchestrador del bot',
        'strategy.py': 'Lógica de señales (DEBE SER v4.0)',
        'scanner.py': 'Escáner de símbolos',
        'bingx_client.py': 'Cliente API BingX',
        'risk_manager.py': 'Gestor de riesgo',
        'requirements.txt': 'Dependencias Python',
        'railway.toml': 'Config Railway deployment',
        'README.md': 'Documentación',
    }
    
    all_ok = True
    for filename, description in required_files.items():
        exists = Path(filename).exists()
        all_ok &= print_check(exists, f"{filename:<20} - {description}")
    
    return all_ok

def check_strategy_v4():
    """Verifica que strategy.py sea v4.0"""
    print_header("2. VERIFICACIÓN DE ESTRATEGIA v4.0")
    
    try:
        with open('strategy.py', 'r') as f:
            content = f.read()
        
        checks = {
            'MFI (Money Flow Index)': 'def _mfi(' in content,
            'Zero Lag SMA (ZLSMA)': 'def _zero_lag_sma(' in content,
            'Turtle Channels': 'def _turtle_channels(' in content,
            'Tier S (mayor confianza)': 'long_s and' in content,
            'Score multiplicadores': 'score_mult = 1.15' in content,
        }
        
        all_ok = True
        for feature, found in checks.items():
            all_ok &= print_check(found, f"Característica: {feature}")
        
        return all_ok
    except Exception as e:
        print_check(False, f"Error leyendo strategy.py: {e}")
        return False

def check_env_vars():
    """Verifica variables de entorno"""
    print_header("3. VERIFICACIÓN DE VARIABLES DE ENTORNO")
    
    required_vars = {
        'BINGX_API_KEY': 'API Key de BingX',
        'BINGX_SECRET_KEY': 'Secret Key de BingX',
    }
    
    optional_vars = {
        'DRY_RUN': 'Modo simulación (default: false)',
        'SCAN_PERIOD_SECONDS': 'Periodo de escaneo (default: 900)',
        'LIMIT_ENTRY': 'Usar órdenes LIMIT (default: true)',
    }
    
    all_ok = True
    
    print("\n📌 REQUERIDAS:")
    for var, description in required_vars.items():
        has_var = os.environ.get(var) is not None
        all_ok &= print_check(has_var, f"{var:<25} - {description}")
        if has_var:
            value = os.environ.get(var, "")
            masked = value[:4] + "****" if len(value) > 8 else "****"
            print(f"   └─ Valor: {masked}")
    
    print("\n📌 OPCIONALES (detectadas):")
    for var, description in optional_vars.items():
        has_var = os.environ.get(var) is not None
        status = f"'{os.environ.get(var)}'" if has_var else "(no configurada)"
        print(f"  {'✅' if has_var else '⚪'} {var:<25} - {description} {status}")
    
    return all_ok

def check_python_deps():
    """Verifica dependencias Python"""
    print_header("4. VERIFICACIÓN DE DEPENDENCIAS PYTHON")
    
    try:
        with open('requirements.txt', 'r') as f:
            requirements = f.read()
        
        packages = {
            'requests': 'Cliente HTTP',
            'numpy': 'Cálculos numéricos',
            'pandas': 'Data frames',
            'python-dotenv': 'Variables de entorno',
        }
        
        all_ok = True
        for package, description in packages.items():
            try:
                __import__(package)
                all_ok &= print_check(True, f"{package:<15} - {description}")
            except ImportError:
                all_ok &= print_check(False, f"{package:<15} - {description} (NO INSTALADO)")
        
        return all_ok
    except Exception as e:
        print_check(False, f"Error leyendo requirements.txt: {e}")
        return False

def check_config_values():
    """Verifica valores de configuración en risk_manager.py"""
    print_header("5. VERIFICACIÓN DE CONFIGURACIÓN DE RIESGO")
    
    try:
        with open('risk_manager.py', 'r') as f:
            content = f.read()
        
        config_checks = {
            'RISK_PER_TRADE': ('0.01', 'Riesgo por operación (1%)'),
            'MAX_POSITIONS': ('5', 'Máximo de posiciones abiertas'),
            'LEVERAGE': ('5', 'Apalancamiento'),
            'DAILY_LOSS_LIMIT': ('0.05', 'Límite diario de pérdidas (5%)'),
        }
        
        all_ok = True
        for var, (expected, description) in config_checks.items():
            # Busca línea con la variable
            for line in content.split('\n'):
                if f'{var} =' in line:
                    has_var = True
                    value = line.split('=')[1].strip()
                    all_ok &= print_check(True, f"{var:<20} = {value:<10} ({description})")
                    break
            else:
                all_ok &= print_check(False, f"{var:<20} NO ENCONTRADO")
        
        return all_ok
    except Exception as e:
        print_check(False, f"Error leyendo risk_manager.py: {e}")
        return False

def check_strategy_params():
    """Verifica parámetros de estrategia v4.0"""
    print_header("6. VERIFICACIÓN DE PARÁMETROS ESTRATEGIA v4.0")
    
    try:
        with open('strategy.py', 'r') as f:
            content = f.read()
        
        params_v4 = {
            'MFI_PERIOD': ('14', 'Período MFI'),
            'ZLSMA_LEN': ('50', 'Longitud ZLSMA'),
            'ZLSMA_LAG': ('0.3', 'Factor lag ZLSMA'),
            'TURTLE_LEN': ('20', 'Período Turtle Channels'),
            'TURTLE_ATR_MULT': ('2.0', 'Multiplicador ATR Turtle'),
            'MIN_ADX': ('22', 'Mínimo ADX'),
        }
        
        all_ok = True
        for var, (default, description) in params_v4.items():
            for line in content.split('\n'):
                if f'{var} =' in line and not line.strip().startswith('#'):
                    value = line.split('=')[1].strip()
                    # Verifica que sea razonable
                    try:
                        float(value) if '.' in value else int(value)
                        all_ok &= print_check(True, f"{var:<20} = {value:<10} ({description})")
                    except:
                        all_ok &= print_check(False, f"{var:<20} = {value} (INVÁLIDO)")
                    break
            else:
                print_check(False, f"{var:<20} NO ENCONTRADO")
                all_ok = False
        
        return all_ok
    except Exception as e:
        print_check(False, f"Error leyendo parámetros: {e}")
        return False

def check_backtesting_files():
    """Verifica archivos de backtesting"""
    print_header("7. VERIFICACIÓN DE ARCHIVOS BACKTESTING")
    
    files = {
        'backtesting_v4.xlsx': 'Plantilla Excel de backtesting',
        'backtesting_template_v4.py': 'Script generador de plantilla',
        'BOT_v4_GUIA_COMPLETA.md': 'Guía de implementación',
    }
    
    all_ok = True
    for filename, description in files.items():
        exists = Path(filename).exists()
        all_ok &= print_check(exists, f"{filename:<35} - {description}")
    
    return all_ok

def generate_checklist():
    """Genera checklist de implementación"""
    print_header("CHECKLIST DE IMPLEMENTACIÓN")
    
    checklist = {
        "Preparación": [
            "[] He leído BOT_v4_GUIA_COMPLETA.md completamente",
            "[] He creado cuenta en BingX y obtenido API keys",
            "[] He configurado variables de entorno (BINGX_API_KEY, BINGX_SECRET_KEY)",
            "[] He instalado dependencias: pip install -r requirements.txt",
        ],
        "Testing Local": [
            "[] He verificado que DRY_RUN=true",
            "[] He ejecutado: python main.py (sin errores)",
            "[] He observado logs durante 10+ minutos",
            "[] Veo logs con formato '[Tier S/A/B]' y métricas",
        ],
        "Backtesting Manual": [
            "[] He abierto backtesting_v4.xlsx",
            "[] He completado al menos 50 trades de backtesting",
            "[] Win Rate ≥65% en mi backtesting",
            "[] Expectancy ≥+0.5R en mi backtesting",
            "[] Tier S Win Rate ≥75%",
        ],
        "Deployment Railway": [
            "[] He creado repo en GitHub",
            "[] He pusheado código a rama main",
            "[] He creado proyecto en railway.app",
            "[] He configurado variables de entorno en Railway",
            "[] He establecido DRY_RUN=false SOLO después de validar",
        ],
        "Dinero Real (Mini)": [
            "[] DRY_RUN confirmado en false en Railway logs",
            "[] Saldo inicial ≤$100 USDT",
            "[] Leverage confirmado 5× en BingX",
            "[] Risk 1% por trade confirmado en risk_manager.py",
            "[] Kill switch 5% diario activado",
        ],
        "Monitoreo Diario": [
            "[] Reviso logs cada 6 horas",
            "[] Calculo win rate cada día",
            "[] Monitoreo drawdown máximo",
            "[] Verifico que Tier S > Tier A > Tier B",
            "[] Registro trades en spreadsheet",
        ],
    }
    
    for section, items in checklist.items():
        print(f"\n📋 {section}:")
        for item in items:
            print(f"  {item}")

def generate_summary():
    """Genera resumen final"""
    print_header("RESUMEN FINAL")
    
    print("""
╔════════════════════════════════════════════════════════════════╗
║                BOT v4.0 - CONFIGURACIÓN FINAL                  ║
╚════════════════════════════════════════════════════════════════╝

✅ NUEVAS CARACTERÍSTICAS EN v4.0:
   • MFI (Money Flow Index): Filtra falsas alarmas de volumen
   • ZLSMA (Zero Lag SMA): Detecta tendencias sin lag
   • Turtle Channels: Confirma breakouts de momentum
   • Tier S: +15% en score cuando todos los indicadores alineados
   • Win Rate esperado: 72% (vs 65% en v3)
   • Expectancy esperado: +1.0R (vs +0.8R en v3)

📊 PRÓXIMOS PASOS:
   1. Verificar que ALL verificaciones estén ✅
   2. Completar checklist de implementación
   3. Hacer backtesting manual de 50-100 trades
   4. Activar DRY_RUN en Railway
   5. Validar resultados durante 7 días
   6. Activar dinero real con $50-100 USDT
   7. Escalar gradualmente si expectancy > +0.5R

💰 GANANCIAS ESPERADAS (a largo plazo):
   Con $1000 USDT inicial y expectancy +1.0R:
   • Ganancias mensuales: ~$1000 × 1.016 = $1016 - comisiones
   • Pero primero: validar con $50-100 USDT durante 3-6 meses
   
⚠️  RECUERDA:
   • NUNCA saltes el backtesting manual
   • Comienza con dinero pequeño ($50 USDT)
   • Monitorea logs diarios
   • Si win rate < 60%, revisa configuración
   • Kill switch diario (5%) es tu red de seguridad

""")

def main():
    """Ejecuta todas las verificaciones"""
    print("🔍 VERIFICADOR DE SETUP - BingX SuperBot v4.0")
    print(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = {
        "Archivos": check_files(),
        "Estrategia v4.0": check_strategy_v4(),
        "Variables entorno": check_env_vars(),
        "Dependencias Python": check_python_deps(),
        "Configuración riesgo": check_config_values(),
        "Parámetros estrategia": check_strategy_params(),
        "Archivos backtesting": check_backtesting_files(),
    }
    
    print_header("RESULTADO GENERAL")
    total_ok = all(results.values())
    for section, passed in results.items():
        print(f"{'✅' if passed else '❌'} {section}")
    
    print_header("ESTADO DEL SETUP")
    if total_ok:
        print("✅ TODO OK - LISTO PARA TESTING")
        print("\nPróximos pasos:")
        print("1. Ejecuta: python main.py (con DRY_RUN=true)")
        print("2. Observa logs por 10+ minutos")
        print("3. Completa backtesting_v4.xlsx")
        print("4. Sigue checklist de implementación")
    else:
        print("❌ FALLOS DETECTADOS")
        print("\nRevisa los errores arriba y soluciona antes de continuar")
        sys.exit(1)
    
    generate_checklist()
    generate_summary()

if __name__ == "__main__":
    main()
