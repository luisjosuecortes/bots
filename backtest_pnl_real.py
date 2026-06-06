"""Backtest de P&L REAL con precios históricos de Polymarket (#1).

Cierra la única pregunta que el backtest por velas no puede responder: ¿el precio
al que el mercado vende UP te deja capturar el edge, o la selección adversa (entrar
solo cuando el mercado lo regala barato) se come la ventaja?

Para cada señal del modelo (walk-forward, sin look-ahead):
  1. Trae el precio de UP en el momento de la señal (CLOB prices-history).
  2. Aplica el filtro EV>=EV_MIN del bot (entra solo si prob-(precio+fee) >= EV_MIN).
  3. Liquida con el resultado REAL de Polymarket (outcomePrices), no por velas.
  4. P&L por share: gana 1-precio_fill ; pierde -precio_fill.

Caveat honesto: prices-history da el precio (mid/último), NO la profundidad del
order book, así que NO modela slippage. El fill real sería algo peor; el FEE lo
amortigua en parte. Es un backtest OPTIMISTA en el precio de entrada.
"""
import asyncio
import sys
import os
import aiohttp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_lib import descargar_cacheado, recolectar_señales, DAY_MS

TRAIN_DAYS = 21
TEST_DAYS_PNL = 6          # días de test para P&L (acotado: 1 req HTTP por señal)
SOURCE = "coinbase"
MIN_PROB = 0.60            # OPTIMIZADO: mejor calibración que 0.55
EV_MIN = 0.15              # OPTIMIZADO: 3x más alto que baseline para evitar selección adversa
FEE = 0.01
CONCURRENCIA = 8
VENTANA_S = {"5m": 300, "15m": 900}

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
        import json as _j
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
    # Precio de UP en el momento de la señal: último punto con t <= ts+elapsed*60 (+30s slack).
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


async def correr(interval, candles):
    señales, base_total, base_up = recolectar_señales(
        interval, candles, TRAIN_DAYS, days_full_test := 14,
        min_prob=MIN_PROB, min_caida=0.0)
    # Acotar a los últimos TEST_DAYS_PNL días para limitar peticiones HTTP.
    if señales:
        ts_max = max(s["ts"] for s in señales)
        corte = ts_max - TEST_DAYS_PNL * 86400
        señales = [s for s in señales if s["ts"] >= corte]

    print(f"\n{'='*70}")
    print(f"  {interval}  ·  MIN_PROB {MIN_PROB} · EV_MIN {EV_MIN} · fee {FEE} · "
          f"últimos {TEST_DAYS_PNL}d")
    print(f"{'='*70}")
    print(f"  Señales del modelo a evaluar: {len(señales)} (trayendo precios…)", flush=True)

    sem = asyncio.Semaphore(CONCURRENCIA)
    async with aiohttp.ClientSession() as session:
        tareas = [datos_mercado(session, sem, interval, s["ts"], s["elapsed"]) for s in señales]
        datos = await asyncio.gather(*tareas)

    con_precio = 0          # señales con datos de Polymarket
    operadas = []           # (precio_fill, gano_real, prob)
    saltadas = []           # (precio_fill, gano_real) de las descartadas por EV
    for s, (precio, res) in zip(señales, datos):
        if precio is None or res is None:
            continue
        con_precio += 1
        fill = precio + FEE
        ev = s["prob"] - fill
        gano = (res == "UP")
        if ev < EV_MIN:
            saltadas.append((fill, gano))
            continue
        operadas.append((fill, gano, s["prob"]))

    print(f"  Con datos de Polymarket: {con_precio}/{len(señales)}")
    print(f"  Pasaron filtro EV (operadas): {len(operadas)}  ·  "
          f"descartadas por precio: {len(saltadas)}")
    if saltadas:
        sw = sum(1 for _, g in saltadas if g)
        sp = sum(f for f, _ in saltadas) / len(saltadas)
        print(f"  [control] DESCARTADAS por EV: win real {sw/len(saltadas)*100:.1f}% "
              f"· precio medio ${sp:.3f}  ← las que NO operas")

    if not operadas:
        print("  Sin trades tras el filtro EV.")
        return

    n = len(operadas)
    wins = sum(1 for _, g, _ in operadas if g)
    pnl = sum((1 - f) if g else (-f) for f, g, _ in operadas)
    invertido = sum(f for f, _, _ in operadas)
    fill_medio = invertido / n
    prob_media = sum(p for _, _, p in operadas) / n

    print(f"  {'-'*60}")
    print(f"  TRADES: {n}")
    print(f"  Win-rate real (liquidación Polymarket): {wins/n*100:.1f}%  ({wins}/{n})")
    print(f"  Prob media del modelo en esos trades  : {prob_media*100:.1f}%")
    print(f"  Precio fill medio (UP+fee)            : ${fill_medio:.3f}")
    print(f"  P&L total (por share)                 : {pnl:+.2f}")
    print(f"  ROI sobre lo invertido                : {pnl/invertido*100:+.1f}%")
    print(f"  P&L medio por trade                   : {pnl/n:+.4f}")
    # Selección adversa: ¿los trades baratos que pasan EV ganan menos que el universo?
    print(f"  ► Si win-rate real ({wins/n*100:.1f}%) > fill medio ({fill_medio*100:.1f}%) → "
          f"{'RENTABLE' if wins/n > fill_medio else 'PIERDE'}")


async def main():
    total = TRAIN_DAYS + 14 + 1
    print(f"Descargando/cacheando {total} días de velas 1m ({SOURCE})…", flush=True)
    candles = descargar_cacheado("1m", total, SOURCE)
    print(f"  {len(candles)} velas.", flush=True)
    for interval in ("5m", "15m"):
        await correr(interval, candles)


if __name__ == "__main__":
    asyncio.run(main())
