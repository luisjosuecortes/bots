"""Núcleo compartido de los backtests (walk-forward, sweep, P&L real).

Reúne la lógica de calibración y entrada del bot en un solo sitio para que los
tres backtests midan EXACTAMENTE lo mismo que el bot en vivo: mismos buckets de
caída, mismo Wilson LB, mismos checkpoints y la misma `probabilidad_temporal`.
"""
import sys
import os
import json
import hashlib
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analisis_historico_modulo import (
    descargar_velas, wilson_lower_bound, _drop_bucket, _SUB_CONFIG,
    probabilidad_temporal)

DAY_MS = 86400000
_CACHE_DIR = "/tmp/poly_backtest_cache"


def descargar_cacheado(interval, days, source):
    """Descarga velas una vez y las cachea en disco (acelera iterar backtests)."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    clave = hashlib.md5(f"{interval}-{days}-{source}".encode()).hexdigest()[:12]
    path = os.path.join(_CACHE_DIR, f"velas-{interval}-{days}d-{source}-{clave}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    velas = descargar_velas(interval=interval, days=days, source=source)
    with open(path, "w") as f:
        json.dump(velas, f)
    return velas


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
    """Tabla temporal usando SOLO ventanas con open en [lo_ms, hi_ms). Réplica exacta."""
    _, paso, checkpoints, win_min = _SUB_CONFIG[interval]
    window_ms = win_min * 60000
    n_esperadas = window_ms // (paso * 60000)

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


def señal_ventana(subs, interval, tabla, min_prob, min_caida=0.0):
    """Replica la entrada del bot. Devuelve (elapsed, prob, caida_pct, gano) o None.

    Una señal por ventana: primer checkpoint con prob>=min_prob y caída>=min_caida,
    sin entrar en el último minuto (guardado: 5m <=1 min; 15m <=2 min).
    `gano` aquí es por velas (close>=open); el P&L real usa la liquidación oficial.
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
        if (win_min - elapsed) <= guard:
            continue
        caida_pct = (open_p - running_low) / open_p * 100
        if caida_pct < min_caida:
            continue
        prob = probabilidad_temporal(tabla, interval, elapsed, caida_pct)
        if prob >= min_prob:
            return (elapsed, prob, caida_pct, gano)
    return None


def recolectar_señales(interval, candles, train_days, test_days,
                       min_prob, min_caida=0.0):
    """Walk-forward rodante. Devuelve (señales, base_total, base_up).

    Cada señal: dict {wk, ts, elapsed, prob, caida, gano_velas}. La tabla de cada
    día se calibra SOLO con los `train_days` previos (sin look-ahead).
    """
    _, paso, _, win_min = _SUB_CONFIG[interval]
    window_ms = win_min * 60000
    n_esperadas = window_ms // (paso * 60000)
    ventanas = agrupar(candles, window_ms)

    t_max = max(ventanas)
    test_start = min(ventanas) + train_days * DAY_MS

    señales = []
    base_total = base_up = 0
    d = test_start
    while d < t_max:
        tabla = build_tabla(ventanas, interval, d - train_days * DAY_MS, d)
        for wk, subs in ventanas.items():
            if wk < d or wk >= d + DAY_MS:
                continue
            if len(subs) < n_esperadas:
                continue
            base_total += 1
            if subs[-1][3] >= subs[0][1]:
                base_up += 1
            r = señal_ventana(subs, interval, tabla, min_prob, min_caida)
            if r is not None:
                elapsed, prob, caida, gano = r
                señales.append({"wk": wk, "ts": wk // 1000, "elapsed": elapsed,
                                "prob": prob, "caida": caida, "gano_velas": gano})
        d += DAY_MS
    return señales, base_total, base_up
