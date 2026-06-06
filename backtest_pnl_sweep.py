"""Sweep de EV_MIN y MIN_PROB para encontrar la mejor combinación de parámetros.

Prueba todas las combinaciones y reporta cuál maximiza P&L evitando la selección adversa.
"""
import asyncio
import sys
import os
import aiohttp
import json as _j
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_lib import descargar_cacheado, recolectar_señales, DAY_MS

TRAIN_DAYS = 21
TEST_DAYS_PNL = 6
SOURCE = "coinbase"
FEE = 0.01
CONCURRENCIA = 8
VENTANA_S = {"5m": 300, "15m": 900}

# Parámetros a barrer
MIN_PROBS = [0.55, 0.57, 0.60, 0.62]
EV_MINS = [0.05, 0.10, 0.15, 0.20, 0.25]

UA = {"User-Agent": "Mozilla/5.0"}


async def get_json(session, url):
    async with session.get(url, headers=UA, timeout=aiohttp.ClientTimeout(total=25)) as r:
        return await r.json()


async def datos_mercado(session, sem, interval, ts, elapsed):
    """Devuelve (precio_up_en_señal, resolucion) o (None, None) si no hay datos."""
    win_s = VENTANA_S[interval]
    slug = f"btc-updown-{interval}-{ts}"
    async with sem:
        try:
            ev = await get_json(session, f"https://gamma-api.polymarket.com/events?slug={slug}")
        except Exception:
            return None, None
        if not ev:
            return None, None
        m = (ev[0].get("markets") or [{}])[0]
        if not m.get("closed"):
            return None, None
        try:
            precios = _j.loads(m.get("outcomePrices", "[]"))
            toks = _j.loads(m.get("clobTokenIds", "[]"))
        except (TypeError, ValueError):
            return None, None
        if len(precios) < 2 or not toks:
            return None, None
        up, down = float(precios[0]), float(precios[1])
        if up >= 0.99 and down <= 0.01:
            res = "UP"
        elif down >= 0.99 and up <= 0.01:
            res = "DOWN"
        else:
            return None, None
        tid = toks[0]
        try:
            ph = await get_json(
                session,
                f"https://clob.polymarket.com/prices-history?market={tid}"
                f"&startTs={ts}&endTs={ts + win_s}&fidelity=1")
        except Exception:
            return None, None
    hist = ph.get("history") if isinstance(ph, dict) else None
    if not hist:
        return None, None
    objetivo = ts + elapsed * 60 + 30
    precio = None
    for pt in hist:
        if pt["t"] <= objetivo:
            precio = pt["p"]
        else:
            break
    if precio is None:
        precio = hist[0]["p"]
    return float(precio), res


async def correr(interval, candles, min_prob, ev_min):
    """Evalúa una combinación de parámetros."""
    señales, base_total, base_up = recolectar_señales(
        interval, candles, TRAIN_DAYS, days_full_test := 14,
        min_prob=min_prob, min_caida=0.0)

    # Acotar a los últimos TEST_DAYS_PNL días
    if señales:
        ts_max = max(s["ts"] for s in señales)
        corte = ts_max - TEST_DAYS_PNL * 86400
        señales = [s for s in señales if s["ts"] >= corte]

    if not señales:
        return None

    sem = asyncio.Semaphore(CONCURRENCIA)
    async with aiohttp.ClientSession() as session:
        tareas = [datos_mercado(session, sem, interval, s["ts"], s["elapsed"]) for s in señales]
        datos = await asyncio.gather(*tareas)

    con_precio = 0
    operadas = []
    for s, (precio, res) in zip(señales, datos):
        if precio is None or res is None:
            continue
        con_precio += 1
        fill = precio + FEE
        ev = s["prob"] - fill
        gano = (res == "UP")
        if ev < ev_min:
            continue
        operadas.append((fill, gano))

    if not operadas:
        return None

    n = len(operadas)
    wins = sum(1 for _, g in operadas if g)
    pnl = sum((1 - f) if g else (-f) for f, g in operadas)
    invertido = sum(f for f, _ in operadas)

    return {
        "n_trades": n,
        "win_rate": wins / n,
        "pnl_total": pnl,
        "roi": pnl / invertido if invertido > 0 else 0,
        "pnl_por_trade": pnl / n
    }


async def main():
    total = TRAIN_DAYS + 14 + 1
    print(f"Descargando/cacheando {total} días de velas 1m ({SOURCE})…", flush=True)
    candles = descargar_cacheado("1m", total, SOURCE)
    print(f"  {len(candles)} velas.\n", flush=True)

    for interval in ("5m", "15m"):
        print(f"\n{'='*90}")
        print(f"  {interval.upper()}  ·  Barriendo MIN_PROB x EV_MIN (últimos {TEST_DAYS_PNL}d)")
        print(f"{'='*90}")
        print(f"{'MIN_PROB':>8} {'EV_MIN':>7} | {'Trades':>6} {'Win%':>6} {'P&L/share':>11} "
              f"{'ROI':>7} {'P&L/trade':>11} | {'Veredicto':>10}")
        print("  " + "-" * 85)

        for min_prob in MIN_PROBS:
            for ev_min in EV_MINS:
                result = await correr(interval, candles, min_prob, ev_min)
                if result is None:
                    print(f"  {min_prob:>8.2f} {ev_min:>7.2f} | {'—':>6} {'—':>6} {'—':>11} "
                          f"{'—':>7} {'—':>11} | {'sin datos':>10}")
                else:
                    pnl = result["pnl_total"]
                    roi = result["roi"] * 100
                    win_pct = result["win_rate"] * 100
                    veredicto = "✓ RENTABLE" if pnl > 0 else "❌ PIERDE"
                    print(f"  {min_prob:>8.2f} {ev_min:>7.2f} | {result['n_trades']:>6} "
                          f"{win_pct:>6.1f} {pnl:>+11.2f} {roi:>+7.1f}% {result['pnl_por_trade']:>+11.4f} "
                          f"| {veredicto:>10}")


if __name__ == "__main__":
    asyncio.run(main())
