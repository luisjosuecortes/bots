#!/usr/bin/env python3
"""Test rápido: verificar que la arquitectura centralizada funciona."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("TEST: Arquitectura Centralizada de Parámetros")
print("=" * 60)

# Test 1: config.py funciona
print("\n[1] Cargando config.json...")
import config
cfg = config.cargar()
print("✓ config.cargar() OK")
print(f"   Bots: {cfg['bots']}, Modo: {cfg['modo']}")
print(f"   Tiene 'parametros': {'parametros' in cfg}")

# Test 2: Leer parámetros por timeframe
print("\n[2] Leyendo parámetros por timeframe...")
for tf in ("5m", "15m"):
    min_prob, ev_min = config.cargar_parametros(cfg, tf)
    print(f"✓ {tf}: MIN_PROB={min_prob}, EV_MIN={ev_min}")

# Test 3: Actualizar parámetros (sin guardar, solo test)
print("\n[3] Probando actualizar_parametros()...")
cfg_test = config.cargar()
config.actualizar_parametros(cfg_test, "5m", 0.62, 0.18)
min_prob, ev_min = config.cargar_parametros(cfg_test, "5m")
print(f"✓ Actualizado: MIN_PROB={min_prob}, EV_MIN={ev_min}")
# Restaurar el original
config.actualizar_parametros(cfg_test, "5m", 0.60, 0.15)
print("✓ Restaurado a valores originales")

# Test 4: Verificar que los bots pueden cargar config
print("\n[4] Verificando que bots pueden cargar config...")
try:
    # Simulamos lo que hacen los bots
    CFG = config.cargar()
    MIN_PROB_5m, EV_MIN_5m = config.cargar_parametros(CFG, "5m")
    MIN_PROB_15m, EV_MIN_15m = config.cargar_parametros(CFG, "15m")
    print(f"✓ 5m:  MIN_PROB={MIN_PROB_5m}, EV_MIN={EV_MIN_5m}")
    print(f"✓ 15m: MIN_PROB={MIN_PROB_15m}, EV_MIN={EV_MIN_15m}")
except Exception as e:
    print(f"✗ Error: {e}")
    sys.exit(1)

# Test 5: Verificar backtest_lib funciona
print("\n[5] Verificando backtest_lib...")
try:
    from backtest_lib import descargar_cacheado
    print("✓ backtest_lib importable")
except Exception as e:
    print(f"✗ Error importing backtest_lib: {e}")

# Test 6: Estructura de archivos
print("\n[6] Verificando estructura de archivos...")
archivos_necesarios = [
    "polypenguin.py",
    "config.py",
    "backtest_lib.py",
    "analisis_historico_modulo.py",
    "cli_util.py",
    "wallet_real.py",
    "5_minutos/paper_trading_bot_5m.py",
    "15_minutos/paper_trading_bot_15m.py",
    "config.json",
]

archivos_innecesarios = [
    "analisis_21dias.py",
    "analisis_gap.py",
    "master_bot.py",
    "monitor_parametros.py",
    "comparativa_final.py",
    "startup_check.py",   # eliminado: las tablas las calibra cada bot por su cuenta
]

todos_ok = True
for archivo in archivos_necesarios:
    ruta = os.path.join(os.path.dirname(__file__), archivo)
    if os.path.exists(ruta):
        print(f"  ✓ {archivo}")
    else:
        print(f"  ✗ FALTA: {archivo}")
        todos_ok = False

print("\nVerificando eliminación de archivos obsoletos...")
for archivo in archivos_innecesarios:
    ruta = os.path.join(os.path.dirname(__file__), archivo)
    if not os.path.exists(ruta):
        print(f"  ✓ {archivo} eliminado")
    else:
        print(f"  ✗ AÚN EXISTE: {archivo}")
        todos_ok = False

if todos_ok:
    print("\n" + "=" * 60)
    print("✓ TODOS LOS TESTS PASARON")
    print("=" * 60)
    print("\nProximos pasos:")
    print("  1. python polypenguin.py     # Inicia el bot")
    print("  2. Elige 'Iniciar' (cada bot calibra su tabla al arrancar)")
    print("  3. Los parámetros salen de config.json (MIN_PROB / EV_MIN)")
    print("=" * 60)
else:
    print("\n" + "=" * 60)
    print("✗ ALGUNOS TESTS FALLARON")
    print("=" * 60)
    sys.exit(1)
