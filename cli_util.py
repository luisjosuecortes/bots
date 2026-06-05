"""Salida CLI con iconos (en español) y parseo de mercados de Polymarket."""
import json
import asyncio
from datetime import datetime, timezone, timedelta


def _p(icono, tf, msg):
    print(f"{icono} [{tf:>3}] {msg}", flush=True)


def init(tf, msg):
    _p("⚙️ ", tf, msg)


def aviso(tf, msg):
    _p("⚠️ ", tf, msg)


def ventana(tf, msg):
    _p("🕒", tf, msg)


def senal(tf, msg):
    _p("🔴", tf, msg)


def resultado(tf, gano, msg):
    _p("🟢" if gano else "🔴", tf, msg)


def resumen_final(tf, wins, losses, pnl_total):
    total = wins + losses
    if total == 0:
        init(tf, "detenido (sin señales)")
        return
    tasa = wins / total * 100
    icono = "🟢" if pnl_total >= 0 else "🔴"
    estado = "GANANCIA" if pnl_total >= 0 else "PÉRDIDA"
    _p(icono, tf, f"RESUMEN  {total} señales · {wins}W/{losses}L ({tasa:.0f}%) · {estado} neta {pnl_total:+.2f} u")


def registrar_resultado(tf, apertura_ant, cierre_ant, senal_ant, precio_up, wins, losses, pnl_total):
    """Resuelve la señal de la ventana anterior y actualiza contadores y P&L.

    P&L en "unidades" de Polymarket: comprar UP a `precio_up`; si gana paga 1.0
    (beneficio 1-precio_up), si pierde se pierde lo pagado (-precio_up).
    Devuelve (wins, losses, pnl_total, etiqueta) donde etiqueta es "WIN"/"LOSS"/None.
    """
    if not (senal_ant and apertura_ant and cierre_ant):
        return wins, losses, pnl_total, None
    gano = cierre_ant >= apertura_ant
    if gano:
        wins += 1
        pnl = 1.0 - precio_up
        etiqueta = "WIN"
        texto = "GANÓ"
    else:
        losses += 1
        pnl = -precio_up
        etiqueta = "LOSS"
        texto = "PERDIÓ"
    pnl_total += pnl
    total = wins + losses
    tasa = wins / total * 100 if total else 0
    resultado(tf, gano, f"{texto}  ${apertura_ant:,.0f}→${cierre_ant:,.0f} · "
                        f"aciertos {wins}/{total} ({tasa:.0f}%) · P&L {pnl:+.2f} (acum {pnl_total:+.2f})")
    return wins, losses, pnl_total, etiqueta


def _to_float(*valores):
    for v in valores:
        if v:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def parse_mercado(event):
    """Extrae (precio_up, volumen, liquidez) de un evento de Polymarket.

    Lee los campos a NIVEL DE MERCADO (volumeNum / liquidityNum), que son los reales.
    El volumen/liquidez a nivel de evento suele venir casi vacio para mercados
    de corta duracion y NO debe usarse como fuente primaria.
    """
    markets = event.get("markets") or []
    if not markets:
        return 0.0, 0.0, 0.0
    m = markets[0]
    try:
        precios = json.loads(m.get("outcomePrices", "[]"))
    except (TypeError, ValueError):
        precios = []
    precio_up = float(precios[0]) if precios else 0.0
    volumen = _to_float(m.get("volumeNum"), m.get("volume24hr"), event.get("volume24hr"))
    liquidez = _to_float(m.get("liquidityNum"), m.get("liquidity"), event.get("liquidity"))
    return precio_up, volumen, liquidez


def token_up(event):
    """Devuelve el clobTokenId del outcome 'UP' (el primero), o None."""
    markets = event.get("markets") or []
    if not markets:
        return None
    try:
        toks = json.loads(markets[0].get("clobTokenIds", "[]"))
    except (TypeError, ValueError):
        toks = []
    return toks[0] if toks else None


# Oráculo de resolución esperado por timeframe (lo verificamos contra la descripción
# real del mercado en cada ventana, por si Polymarket lo cambia silenciosamente).
_ORACULO_ESPERADO = {
    "5m": "chainlink",
    "15m": "chainlink",
    "1h": "binance",
}


def verificar_oraculo(event, tf):
    """Comprueba que la fuente de resolución del mercado sigue siendo la esperada.

    Polymarket puede cambiar el oráculo al crear nuevos contratos (p. ej. pasar de
    Chainlink a Pyth) sin avisar. Si eso ocurre, nuestra calibración (Coinbase/Binance)
    dejaría de coincidir con la resolución y apostaríamos a ciegas.

    Devuelve (ok, mensaje):
      - ok=True  y mensaje=None        → el oráculo esperado SÍ aparece en la descripción.
      - ok=False y mensaje=str         → no aparece el esperado o se detecta otro oráculo
                                          conocido (Pyth/Coinbase/Binance/Kraken…).
    """
    esperado = _ORACULO_ESPERADO.get(tf)
    if not esperado:
        return True, None
    markets = event.get("markets") or []
    texto = ""
    if markets:
        m = markets[0]
        texto = f"{m.get('description', '')} {m.get('resolutionSource', '')}"
    texto += f" {event.get('description', '')} {event.get('resolutionSource', '')}"
    texto = texto.lower()
    if not texto.strip():
        return True, None  # sin descripción disponible: no podemos afirmar nada

    if esperado in texto:
        return True, None

    otros = [o for o in ("chainlink", "pyth", "binance", "coinbase", "kraken", "uniswap")
             if o != esperado and o in texto]
    if otros:
        return False, (f"⚠️  ORÁCULO CAMBIÓ: se esperaba '{esperado}' pero el mercado "
                       f"menciona {', '.join(otros)}. La calibración podría NO coincidir "
                       f"con la resolución — REVISA antes de confiar en las señales.")
    return False, (f"⚠️  ORÁCULO: no se encontró '{esperado}' en la descripción del mercado. "
                   f"Verifica manualmente la fuente de resolución.")


_GRAN_COINBASE = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


async def _binance_klines(session, interval, limit=None, start_ms=None):
    """Velas de Binance normalizadas a [t_ms, open, high, low, close], ascendente."""
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={interval}"
    if start_ms is not None:
        url += f"&startTime={int(start_ms)}"
    url += f"&limit={int(limit) if limit else 1000}"
    async with session.get(url) as resp:
        data = await resp.json()
    return [[c[0], float(c[1]), float(c[2]), float(c[3]), float(c[4])] for c in data]


async def _coinbase_klines(session, interval, limit=None, start_dt=None, end_dt=None):
    """Velas de Coinbase (BTC-USD) normalizadas a [t_ms, open, high, low, close], ascendente."""
    gran = _GRAN_COINBASE.get(interval, 60)
    url = f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity={gran}"
    if start_dt is not None:
        end_dt = end_dt or datetime.now(timezone.utc)
        url += f"&start={start_dt.isoformat()}&end={end_dt.isoformat()}"
    async with session.get(url) as resp:
        data = await resp.json()
    # Coinbase: [time_s, low, high, open, close, volume], más reciente primero.
    norm = [[c[0] * 1000, c[3], c[2], c[1], c[4]] for c in data]
    norm.sort(key=lambda x: x[0])
    if limit and not start_dt:
        norm = norm[-int(limit):]
    return norm


async def precio_vivo(session, source="binance"):
    """Precio actual de BTC en la fuente indicada. Devuelve float o None."""
    try:
        if source == "coinbase":
            url = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
            async with session.get(url) as resp:
                return float((await resp.json())["price"])
        url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
        async with session.get(url) as resp:
            return float((await resp.json())["price"])
    except Exception:
        return None


async def precio_frontera(session, frontera_dt, source="binance"):
    """Precio EXACTO de BTC en el instante 'frontera_dt' (inicio/fin de ventana).

    Determinista: usa la APERTURA de la vela de 1m que empieza en esa frontera. No
    depende de a qué segundo concreto consulte cada bot, así todos obtienen el MISMO
    precio de cierre para la misma frontera (corrige el desfase del ticker en vivo).
    Usa la misma fuente que la resolución del mercado. Devuelve float o None.
    """
    try:
        if source == "coinbase":
            fin = frontera_dt + timedelta(minutes=1)
            velas = await _coinbase_klines(session, "1m", start_dt=frontera_dt, end_dt=fin)
            for v in velas:
                if v[0] == int(frontera_dt.timestamp() * 1000):
                    return v[1]
            return velas[0][1] if velas else None
        velas = await _binance_klines(session, "1m", limit=1, start_ms=int(frontera_dt.timestamp() * 1000))
        return velas[0][1] if velas else None
    except Exception:
        return None


async def apertura_y_minimo(session, inicio_ventana, source="binance"):
    """(apertura, minimo) reales de la ventana usando velas de 1m desde su inicio.

    Evita inicializar el minimo a la apertura cuando el bot arranca a mitad de ventana
    (lo que generaba una caída 0.00% falsa). Usa la fuente de resolución del mercado.
    """
    try:
        if source == "coinbase":
            velas = await _coinbase_klines(session, "1m", start_dt=inicio_ventana)
        else:
            velas = await _binance_klines(session, "1m", limit=120,
                                          start_ms=int(inicio_ventana.timestamp() * 1000))
        if velas:
            apertura = velas[0][1]
            minimo = min(v[3] for v in velas)
            return apertura, minimo
    except Exception:
        pass
    return None, None


async def tendencia_pct(session, minutos, source="binance"):
    """Cambio % de BTC en los últimos 'minutos' (régimen de mercado).

    Negativo fuerte = caída libre, donde la reversión a la media de micro-caídas
    deja de funcionar. Devuelve el % de cambio (último close vs primer open) o 0.0.
    """
    try:
        if source == "coinbase":
            ini = datetime.now(timezone.utc) - timedelta(minutes=int(minutos))
            velas = await _coinbase_klines(session, "1m", start_dt=ini)
        else:
            velas = await _binance_klines(session, "1m", limit=int(minutos))
        if velas and len(velas) >= 2:
            apertura = velas[0][1]
            cierre = velas[-1][4]
            if apertura:
                return (cierre - apertura) / apertura * 100
    except Exception:
        pass
    return 0.0


async def mejor_ask_clob(session, token_id):
    """Precio EJECUTABLE para COMPRAR UP: mejor (menor) ask del order book del CLOB.

    Devuelve (precio_ask, tamano_disponible) o (0.0, 0.0) si no hay libro.
    Es más realista que outcomePrices (que es un punto medio): es lo que de verdad
    pagarías al entrar al mercado.
    """
    if not token_id:
        return 0.0, 0.0
    try:
        url = f"https://clob.polymarket.com/book?token_id={token_id}"
        async with session.get(url) as resp:
            book = await resp.json()
        asks = book.get("asks") or []
        if not asks:
            return 0.0, 0.0
        mejor = min(asks, key=lambda a: float(a["price"]))
        return float(mejor["price"]), float(mejor.get("size", 0.0))
    except Exception:
        return 0.0, 0.0


async def simular_fill_clob(session, token_id, tamano_orden):
    """Simula el fill REAL de una orden de mercado de COMPRA de 'tamano_orden' unidades
    de UP, caminando el order book del CLOB nivel a nivel (de menor a mayor precio).

    Devuelve (precio_promedio, tamano_llenado, llenado_completo, niveles, mejor_ask):
      - precio_promedio : VWAP de ejecución (lo que pagarías de media por unidad).
      - tamano_llenado  : cuántas unidades se pudieron llenar (≤ tamano_orden).
      - llenado_completo: True si se llenó toda la orden.
      - niveles         : cuántos niveles del libro tuvo que barrer.
      - mejor_ask       : precio del primer nivel (referencia/punto de partida).

    Esto modela el slippage real: una orden grande "come" varios niveles y paga un
    precio promedio peor que el mejor ask. Todo es de SOLO LECTURA (gratis).
    """
    if not token_id or tamano_orden <= 0:
        return 0.0, 0.0, False, 0, 0.0
    try:
        url = f"https://clob.polymarket.com/book?token_id={token_id}"
        async with session.get(url) as resp:
            book = await resp.json()
        asks = book.get("asks") or []
        if not asks:
            return 0.0, 0.0, False, 0, 0.0

        niveles_ord = sorted(
            ((float(a["price"]), float(a.get("size", 0.0))) for a in asks),
            key=lambda x: x[0],
        )
        mejor_ask = niveles_ord[0][0]

        restante = float(tamano_orden)
        costo = 0.0
        llenado = 0.0
        niveles_usados = 0
        for precio, size in niveles_ord:
            if restante <= 0:
                break
            toma = min(restante, size)
            costo += toma * precio
            llenado += toma
            restante -= toma
            niveles_usados += 1

        if llenado <= 0:
            return 0.0, 0.0, False, 0, mejor_ask
        precio_promedio = costo / llenado
        llenado_completo = restante <= 1e-9
        return precio_promedio, llenado, llenado_completo, niveles_usados, mejor_ask
    except Exception:
        return 0.0, 0.0, False, 0, 0.0


async def revalidar_fill(session, token_id, tamano_orden, precio_limite,
                         fee=0.0, retardo_s=0.5):
    """Re-valida el fill tras una latencia, como haría una ORDEN LÍMITE real.

    Entre que el bot DECIDE (lee precio+libro) y su orden LLEGA al CLOB pasan
    ~0.3-1s; en ese lapso el libro se mueve y el ask que vimos puede desaparecer
    (el "espejismo del ask"). Esto simula ese hueco: espera `retardo_s`, re-lee el
    libro y recomputa el VWAP fresco.

    Modela una orden LÍMITE a `precio_limite` (= tu peor precio aceptable, p. ej.
    prob - margen_ev): solo se considera LLENADA si el precio fresco con fee NO
    excede el límite. Si el libro se movió en tu contra, NO se llena (latencia =
    oportunidad perdida), pero nunca pagas de más.

    Devuelve dict:
      - llenado_real : True si la orden límite se habría ejecutado al precio fresco.
      - precio_entrada: VWAP fresco + fee (lo que realmente pagarías).
      - mejor_ask, niveles, vwap, completo: del libro fresco.
      - motivo : None si OK; texto si no se llenó (para loguear).
    """
    if retardo_s > 0:
        await asyncio.sleep(retardo_s)
    vwap, llenado, completo, niveles, mejor_ask = await simular_fill_clob(
        session, token_id, tamano_orden)
    precio_entrada = vwap + fee
    res = {"vwap": vwap, "completo": completo, "niveles": niveles,
           "mejor_ask": mejor_ask, "precio_entrada": precio_entrada,
           "llenado_real": False, "motivo": None}
    if vwap < 0.01 or not completo:
        res["motivo"] = "libro fresco sin profundidad para la orden"
        return res
    if precio_entrada > precio_limite + 1e-9:
        res["motivo"] = (f"el precio se movió: entrada ${precio_entrada:.3f} > "
                         f"límite ${precio_limite:.3f} (orden límite NO llena)")
        return res
    res["llenado_real"] = True
    return res
