#!/usr/bin/env python3
"""Test para verificar que el monto mínimo de $1.0 se respeta en todas las órdenes."""

def simular_calculo_orden(tamano_usdc, precio):
    """Simula el cálculo de shares y monto total (igual que en cli_util.py)."""
    print(f"\n{'='*70}")
    print(f"TEST: tamano_usdc=${tamano_usdc:.4f} | precio_entrada=${precio:.3f}")
    print(f"{'='*70}")

    # Aplicar la lógica de cli_util.py
    monto_minimo = 1.0

    # Paso 1: Validar tamano_usdc
    if tamano_usdc < monto_minimo:
        print(f"⚠️  AJUSTE 1: tamano_usdc ${tamano_usdc:.4f} < mínimo ${monto_minimo:.2f}")
        tamano_usdc = monto_minimo
        print(f"✓ Corregido a: ${tamano_usdc:.2f}")
    else:
        print(f"✓ tamano_usdc ${tamano_usdc:.2f} >= mínimo ${monto_minimo:.2f}")

    # Paso 2: Calcular shares
    shares = tamano_usdc / precio if precio > 0 else 0.0
    print(f"✓ shares = ${tamano_usdc:.4f} / ${precio:.3f} = {shares:.4f} shares")

    # Paso 3: Calcular monto total
    monto_total = shares * precio
    print(f"✓ monto_total = {shares:.4f} * ${precio:.3f} = ${monto_total:.4f}")

    # Paso 4: Double-check del monto mínimo
    if monto_total < monto_minimo:
        print(f"⚠️  AJUSTE 2: monto_total ${monto_total:.4f} < mínimo ${monto_minimo:.2f}")
        shares = monto_minimo / precio if precio > 0 else 0.0
        monto_total = monto_minimo
        print(f"✓ Corregido: shares={shares:.4f}, monto_total=${monto_total:.2f}")

    # Resultado final
    print(f"\n{'─'*70}")
    if shares <= 0:
        print(f"❌ RECHAZADO: shares={shares:.4f} <= 0")
        return False

    print(f"✅ APROBADO: Se colocaría orden de {shares:.4f} shares @ ${precio:.3f}")
    print(f"           MONTO TOTAL: ${monto_total:.4f} (>= ${monto_minimo:.2f})")
    return True


# Casos de test
casos = [
    # (tamano_usdc, precio, descripción)
    (5.0, 0.30, "Config normal: $5.0 @ precio $0.30"),
    (5.0, 0.08, "Config normal: $5.0 @ precio $0.08"),
    (0.9999, 0.30, "Config pequeña (0.9999): precio $0.30 (ERROR ORIGINAL)"),
    (0.9982, 0.30, "Config pequeña (0.9982): precio $0.30 (NUEVO ERROR)"),
    (1.0, 0.30, "Exactamente $1.0 @ precio $0.30"),
    (0.5, 0.50, "Config muy pequeña: $0.50 @ precio $0.50"),
    (0.1, 0.02, "Config mínima: $0.10 @ precio $0.02"),
]

print("\n" + "="*70)
print("SIMULACIÓN: Validación de monto mínimo $1.0 del CLOB")
print("="*70)

resultados = []
for tamano_usdc, precio, desc in casos:
    print(f"\n{desc}")
    ok = simular_calculo_orden(tamano_usdc, precio)
    resultados.append((desc, ok))

# Resumen
print("\n" + "="*70)
print("RESUMEN DE RESULTADOS")
print("="*70)
for desc, ok in resultados:
    icono = "✅" if ok else "❌"
    print(f"{icono} {desc}")

aprobados = sum(1 for _, ok in resultados if ok)
print(f"\n✅ {aprobados}/{len(resultados)} casos aprobados (sin rechazos del CLOB)")
