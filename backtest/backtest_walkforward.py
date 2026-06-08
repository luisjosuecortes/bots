"""Backtest WALK-FORWARD del modelo de probabilidad temporal.

Pregunta que responde: cuando el modelo dice "P(UP)=53%", ¿cuántas veces cierra
UP DE VERDAD fuera de la muestra con la que se calibró? Y lo compara contra la
tasa base del periodo para exponer si el "edge" es ventaja real o solo deriva.

Metodología (idéntica al bot en vivo):
  - Misma fuente (Coinbase BTC/USD 1m), mismos buckets de caída, mismo Wilson LB,
    mismos checkpoints y la MISMA función `probabilidad_temporal` para entrar.
  - Walk-forward rodante: para cada día de test la tabla se calibra SOLO con los
    `TRAIN_DAYS` previos (como hace el bot al re-calibrar cada 24h con 30 días),
    nunca con datos del futuro. Así no hay look-ahead.
  - Réplica de la entrada: una señal por ventana, en el primer checkpoint donde
    prob >= MIN_PROB, respetando el guardado de "no entrar en el último minuto".

No simula P&L con precio de Polymarket (no hay histórico del order book), pero la
TASA DE ACIERTO realizada ES el precio de equilibrio: si las señales aciertan w%,
solo ganas dinero comprando UP por debajo de $w. El bot paga hasta prob-EV_min.
"""
import sys
import os
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analisis_historico_modulo import (
    descargar_velas, wilson_lower_bound, _drop_bucket, _SUB_CONFIG,
    probabilidad_temporal)

# --- Parámetros (OPTIMIZADOS con análisis histórico) ---
TRAIN_DAYS = 21            # ventana de calibración rodante
TEST_DAYS = 14            # días evaluados fuera de muestra
MIN_PROB = 0.60           # OPTIMIZADO: mejor calibración, menos gap
EV_MIN = 0.15             # OPTIMIZADO: 3x más alto para evitar selección adversa
SOURCE = "coinbase"       # misma fuente que la calibración del bot
DAY_MS = 86400000


def agrupar(candles, window_ms):
    """Agrupa velas 1m por ventana padre y ordena cada ventana en el tiempo."""
    ventanas = defaultdict(list)
    for c in candles:
        t = c[0]
        wk = (t // window_ms) * window_ms
        ventanas[wk].append((t, float(c[1]), float(c[3]), float(c[4])))  # t, open, low, close
    for wk in ventanas:
        ventanas[wk].sort(key=lambda x: x[0])
    return ventanas


def build_tabla(ventanas, interval, lo_ms, hi_ms):
    """Construye la tabla temporal usando SOLO ventanas con open en [lo_ms, hi_ms).

    Replica exactamente `ejecutar_analisis_temporal`: buckets de caída por
    checkpoint, mínimo 15 muestras y Wilson LB(1 cola, 95%) > 0.5.
    """
    sub_int, paso, checkpoints, win_min = _SUB_CONFIG[interval]
    window_ms = win_min * 60000
    sub_ms = paso * 60000
    n_esperadas = window_ms // sub_ms

    buckets = defaultdict(lambda: {"total": 0, "up": 0})
    for wk, subs in ventanas.items():
        if wk < lo_ms or wk >= hi_ms:
            continue
        if len(subs) < n_esperadas:
            continue
        open_p = subs[0][1]
        close_p = subs[-1][3]
        if open_p == 0:
            continue
        cierre_up = close_p >= open_p
        running_low = open_p
        for i, (_, _, low_p, _) in enumerate(subs):
            if low_p < running_low:
                running_low = low_p
            elapsed = (i + 1) * paso
            if elapsed not in checkpoints:
                continue
            caida_pct = (open_p - running_low) / open_p * 100
            b = _drop_bucket(interval, caida_pct)
            if b is None:
                continue
            k = (elapsed, b)
            buckets[k]["total"] += 1
            if cierre_up:
                buckets[k]["up"] += 1

    tabla = {}
    for (elapsed, b), s in buckets.items():
        if s["total"] >= 15:
            lb = wilson_lower_bound(s["up"], s["total"])
            if lb > 0.5:
                tabla.setdefault(elapsed, {})[b] = round(lb, 3)
    return tabla


def simular_ventana(subs, interval, tabla):
    """Replica la entrada del bot en una ventana. Devuelve (prob, gano) o None.

    Una señal por ventana: primer checkpoint con prob>=MIN_PROB, sin entrar en el
    último minuto (guardado: 5m no entra a falta de <=1 min; 15m a falta de <=2).
    """
    _, paso, checkpoints, win_min = _SUB_CONFIG[interval]
    guard = 1 if win_min == 5 else 2
    open_p = subs[0][1]
    close_p = subs[-1][3]
    if open_p == 0:
        return None
    gano = close_p >= open_p
    running_low = open_p
    for i, (_, _, low_p, _) in enumerate(subs):
        if low_p < running_low:
            running_low = low_p
        elapsed = (i + 1) * paso
        if elapsed not in checkpoints:
            continue
        if (win_min - elapsed) <= guard:        # no entrar en el último minuto
            continue
        caida_pct = (open_p - running_low) / open_p * 100
        prob = probabilidad_temporal(tabla, interval, elapsed, caida_pct)
        if prob >= MIN_PROB:
            return (prob, gano)
    return None


def backtest(interval, candles):
    _, paso, _, win_min = _SUB_CONFIG[interval]
    window_ms = win_min * 60000
    n_esperadas = window_ms // (paso * 60000)
    ventanas = agrupar(candles, window_ms)

    t_min = min(ventanas)
    t_max = max(ventanas)
    test_start = t_min + TRAIN_DAYS * DAY_MS

    señales = []          # (prob_modelo, gano)
    base_total = 0        # todas las ventanas completas del periodo de test
    base_up = 0

    # Rodamos día a día: tabla calibrada con los TRAIN_DAYS previos a cada día.
    d = test_start
    while d < t_max:
        tabla = build_tabla(ventanas, interval, d - TRAIN_DAYS * DAY_MS, d)
        for wk, subs in ventanas.items():
            if wk < d or wk >= d + DAY_MS:
                continue
            if len(subs) < n_esperadas:
                continue
            base_total += 1
            if subs[-1][3] >= subs[0][1]:
                base_up += 1
            r = simular_ventana(subs, interval, tabla)
            if r is not None:
                señales.append(r)
        d += DAY_MS

    return señales, base_total, base_up


def reporte(interval, señales, base_total, base_up):
    print(f"\n{'='*64}")
    print(f"  {interval}  ·  train {TRAIN_DAYS}d rodante · test {TEST_DAYS}d · MIN_PROB {MIN_PROB}")
    print(f"{'='*64}")

    base_rate = base_up / base_total if base_total else 0
    print(f"Tasa base UP (todas las {base_total} ventanas de test): {base_rate*100:.1f}%")

    if not señales:
        print("Sin señales en el periodo de test.")
        return

    n = len(señales)
    wins = sum(1 for _, g in señales if g)
    win_rate = wins / n
    avg_prob = sum(p for p, _ in señales) / n

    print(f"Señales: {n}  ({n/base_total*100:.1f}% de las ventanas)")
    print(f"  Prob media que PROMETE el modelo : {avg_prob*100:.1f}%")
    print(f"  Tasa de acierto REAL out-of-sample: {win_rate*100:.1f}%  ({wins}/{n})")
    print(f"  Edge condicional vs tasa base     : {(win_rate-base_rate)*100:+.1f} pts")
    print(f"  Brecha de calibración (real-prom) : {(win_rate-avg_prob)*100:+.1f} pts")

    # Precio de equilibrio: solo ganas comprando UP por debajo de la tasa real.
    print(f"  ► Precio de equilibrio: solo ganas si compras UP < ${win_rate:.3f}")
    print(f"    (el bot paga hasta prob-EV = ${avg_prob-EV_MIN:.3f}; "
          f"{'OK margen' if (avg_prob-EV_MIN) < win_rate else 'PIERDE: paga de más'})")

    # Calibración por nivel de probabilidad del modelo.
    print("  Calibración por prob del modelo:")
    por_prob = defaultdict(lambda: {"n": 0, "w": 0})
    for p, g in señales:
        por_prob[round(p, 2)]["n"] += 1
        por_prob[round(p, 2)]["w"] += 1 if g else 0
    for p in sorted(por_prob):
        s = por_prob[p]
        print(f"    modelo {p*100:.0f}% → real {s['w']/s['n']*100:5.1f}%  (n={s['n']})")


def main():
    total = TRAIN_DAYS + TEST_DAYS + 1
    print(f"Descargando {total} días de velas 1m de {SOURCE} (BTC-USD)…", flush=True)
    candles = descargar_velas(interval="1m", days=total, source=SOURCE)
    print(f"  {len(candles)} velas 1m descargadas.", flush=True)

    for interval in ("5m", "15m"):
        señales, base_total, base_up = backtest(interval, candles)
        reporte(interval, señales, base_total, base_up)

    print(f"\n{'='*64}")
    print("Interpretación:")
    print("  · Edge condicional ~0 pts  → el 'edge' es solo deriva, no señal.")
    print("  · Brecha de calibración muy negativa → el modelo SOBREESTIMA P(UP).")
    print("  · Si real < precio que paga el bot → pierde por diseño.")
    print(f"{'='*64}")


if __name__ == "__main__":
    main()
