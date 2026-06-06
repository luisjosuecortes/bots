"""Salida CLI con iconos (en español) y parseo de mercados de Polymarket."""
import json
import os
import asyncio
import collections
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

# Polymarket nombra sus mercados en hora del Este (ET). ZoneInfo maneja
# automáticamente el cambio EDT(-4)/EST(-5), así que NO hay que hardcodear -4h.
_ET = ZoneInfo("America/New_York")


def ahora_et():
    """Hora actual en zona horaria del Este (maneja horario de verano EDT/EST)."""
    return datetime.now(_ET)


def a_et(dt):
    """Convierte un datetime (UTC o aware) a hora del Este (ET), como Polymarket."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ET)


class FeedChainlink:
    """Feed en vivo del precio Chainlink BTC/USD vía el WebSocket de Polymarket.

    Es EXACTAMENTE la misma fuente que Polymarket usa para mostrar el "precio a
    superar"/"precio actual" y para RESOLVER los mercados de 5m/15m (Chainlink
    BTC/USD data stream), no Coinbase ni Binance. Mantiene una conexión de fondo
    con reconexión automática y un buffer de ~33 min de valores por segundo.

    Uso:
        feed = FeedChainlink()
        await feed.conectar(session)         # lanza la tarea de fondo
        await feed.esperar_datos()           # espera el primer precio
        feed.precio_actual()                 # último precio (= "precio actual")
        feed.strike_en(ts_ms)                # 1er precio >= ts_ms (= "precio a superar")
        feed.minimo_desde(ts_ms)             # mínimo desde un instante (para la caída)
    """

    URL = "wss://ws-live-data.polymarket.com"

    def __init__(self, maxlen=2200):
        self.latest_ts = None
        self.latest_val = None
        self.hist = collections.deque(maxlen=maxlen)  # (ts_ms, value) ascendente
        self._task = None

    async def conectar(self, session):
        self._task = asyncio.create_task(self._run(session))

    async def esperar_datos(self, timeout=10.0):
        """Espera hasta tener el primer precio (o agota el timeout)."""
        t = 0.0
        while self.latest_val is None and t < timeout:
            await asyncio.sleep(0.2)
            t += 0.2
        return self.latest_val is not None

    def _add(self, ts, val):
        try:
            ts = int(ts)
            val = float(val)
        except (TypeError, ValueError):
            return
        self.latest_ts, self.latest_val = ts, val
        self.hist.append((ts, val))

    async def _run(self, session):
        sub = {"action": "subscribe", "subscriptions": [
            {"topic": "crypto_prices_chainlink", "type": "*",
             "filters": '{"symbol":"btc/usd"}'}]}
        while True:
            try:
                async with session.ws_connect(self.URL, heartbeat=5) as ws:
                    await ws.send_json(sub)
                    async for msg in ws:
                        if msg.type != aiohttp_WSMsgType_TEXT() or not msg.data:
                            continue
                        try:
                            d = json.loads(msg.data)
                        except (ValueError, TypeError):
                            continue
                        pl = d.get("payload") or {}
                        if isinstance(pl.get("data"), list):      # backfill inicial
                            for pt in pl["data"]:
                                self._add(pt.get("timestamp"), pt.get("value"))
                        elif "value" in pl:                        # update en vivo
                            self._add(pl.get("timestamp"), pl.get("value"))
            except Exception:
                await asyncio.sleep(1.0)  # reconectar

    def precio_actual(self):
        return self.latest_val

    def strike_en(self, ts_ms):
        """Primer precio con timestamp >= ts_ms: el 'precio a superar' de la ventana.

        Igual que Polymarket: el primer tick de Chainlink en/ tras la frontera.
        Devuelve None si el buffer no cubre ese instante (p. ej. recién arrancó).
        """
        for t, v in self.hist:
            if t >= ts_ms:
                return v
        return None

    def minimo_desde(self, ts_ms):
        """Mínimo de Chainlink desde 'ts_ms' (caída real intra-ventana), o None."""
        vals = [v for t, v in self.hist if t >= ts_ms]
        return min(vals) if vals else None


def _aiohttp_wsmsgtype_text_cache():
    import aiohttp
    return aiohttp.WSMsgType.TEXT


_WS_TEXT = None


def aiohttp_WSMsgType_TEXT():
    global _WS_TEXT
    if _WS_TEXT is None:
        _WS_TEXT = _aiohttp_wsmsgtype_text_cache()
    return _WS_TEXT


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
    _p("🟢" if gano else "❌", tf, msg)


# Capital simulado por operación en paper trading: cada compra "gasta" este
# importe en USDC (compramos $1 de acciones UP al precio de entrada). Así cada
# apuesta arriesga lo mismo y el P&L sale directamente en dólares.
CAPITAL_SIMULADO = 1.0


def acciones_simuladas(precio_entrada, capital=CAPITAL_SIMULADO):
    """Acciones UP que compra un capital fijo al precio de entrada."""
    return capital / precio_entrada if precio_entrada > 0 else 0.0


def resumen_final(tf, wins, losses, pnl_total):
    total = wins + losses
    if total == 0:
        init(tf, "detenido (sin señales)")
        return
    tasa = wins / total * 100
    icono = "🟢" if pnl_total >= 0 else "🔴"
    estado = "GANANCIA" if pnl_total >= 0 else "PÉRDIDA"
    invertido = total * CAPITAL_SIMULADO
    roi = pnl_total / invertido * 100 if invertido else 0
    _p(icono, tf, f"RESUMEN  {total} señales · {wins}W/{losses}L ({tasa:.0f}%) · "
                  f"{estado} neta ${pnl_total:+.2f} sobre ${invertido:.2f} invertidos ({roi:+.1f}% ROI)")


def registrar_resultado_real(tf, gano, lado, precio_entrada, wins, losses,
                             pnl_total, detalle="", capital=CAPITAL_SIMULADO):
    """Actualiza contadores y P&L usando el resultado REAL de Polymarket.

    'gano' viene de la LIQUIDACIÓN real del mercado (no se infiere con velas).
    Simula una compra de `capital` USDC: compra `capital/entrada` acciones UP.
    Si gana, cada acción paga 1.0 (beneficio capital/entrada - capital); si
    pierde, se pierde lo invertido (-capital). Devuelve (wins, losses,
    pnl_total, etiqueta).
    """
    acciones = acciones_simuladas(precio_entrada, capital)
    if gano:
        wins += 1
        pnl = acciones * 1.0 - capital
        etiqueta = "WIN"
        texto = "GANÓ"
    else:
        losses += 1
        pnl = -capital
        etiqueta = "LOSS"
        texto = "PERDIÓ"
    pnl_total += pnl
    total = wins + losses
    tasa = wins / total * 100 if total else 0
    resultado(tf, gano, f"{texto} {lado} {detalle} · {acciones:.2f} acc @ ${precio_entrada:.3f} "
                        f"(${capital:.2f}) · aciertos {wins}/{total} "
                        f"({tasa:.0f}%) · P&L ${pnl:+.2f} (acum ${pnl_total:+.2f})")
    return wins, losses, pnl_total, etiqueta, pnl


def intentar_orden_real(tf, token_id, precio):
    """En modo REAL, coloca la compra real de UP. Best-effort: nunca rompe el bot.

    Solo opera si el entorno trae POLY_MODO=real (lo activa PolyPenguin al lanzar en
    modo real); ejecutado de otra forma, el bot se queda en paper. El tamaño de la
    orden sale de la config (wallet.tamano_usdc). Devuelve True si se colocó.
    """
    if os.environ.get("POLY_MODO") != "real":
        return False
    try:
        import config
        import wallet_real
    except ImportError:
        aviso(tf, "modo real: módulos de wallet no disponibles; sigo en paper")
        return False

    cfg = config.cargar()
    if not config.wallet_lista(cfg):
        aviso(tf, "modo real: wallet sin configurar (Ajustes › Wallet); sigo en paper")
        return False

    tamano_usdc = float(cfg["wallet"].get("tamano_usdc", 5.0))
    # Monto mínimo del CLOB: $1.0. La orden se envía POR IMPORTE en USDC (orden de
    # mercado FOK), así que basta con asegurar que el importe sea >= $1.0.
    monto_minimo = 1.0
    if tamano_usdc < monto_minimo:
        aviso(tf, f"tamaño de orden ${tamano_usdc:.4f} < mínimo del CLOB ${monto_minimo:.2f}; ajustando a ${monto_minimo:.2f}")
        tamano_usdc = monto_minimo
    tamano_usdc = round(tamano_usdc, 2)  # USDC con 2 decimales (lo exige el CLOB)
    if precio <= 0:
        return False
    shares_aprox = tamano_usdc / precio   # solo para mostrar en el log
    try:
        resp = wallet_real.comprar_up(cfg, token_id, precio, tamano_usdc)
        senal(tf, f"💰 ORDEN REAL · ~{shares_aprox:.1f} shares @ ${precio:.3f} (${tamano_usdc:.2f}) · {resp}")
        return True
    except wallet_real.WalletError as e:
        aviso(tf, f"modo real: no se colocó la orden ({e}); queda solo como señal")
        return False


async def resultado_resuelto(session, slug):
    """Resultado REAL con el que Polymarket liquidó un mercado ya cerrado.

    Lee outcomePrices del mercado una vez 'closed':
      ['1','0'] -> ganó 'UP';  ['0','1'] -> ganó 'DOWN'.
    Es la liquidación real de Polymarket (Chainlink/UMA), 100% exacta: no aproxima
    el oráculo con Coinbase/Binance. Devuelve 'UP', 'DOWN', o None si aún no ha
    cerrado/liquidado de forma concluyente.
    """
    try:
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        async with session.get(url) as resp:
            data = await resp.json()
        if not data:
            return None
        markets = data[0].get("markets") or []
        if not markets:
            return None
        m = markets[0]
        if not m.get("closed"):
            return None
        precios = json.loads(m.get("outcomePrices", "[]"))
        if len(precios) < 2:
            return None
        up, down = float(precios[0]), float(precios[1])
        if up >= 0.99 and down <= 0.01:
            return "UP"
        if down >= 0.99 and up <= 0.01:
            return "DOWN"
        return None  # cerrado pero aún no liquidado de forma concluyente
    except Exception:
        return None


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
}


def verificar_oraculo(event, tf):
    """Comprueba que la fuente de resolución del mercado sigue siendo la esperada.

    Polymarket puede cambiar el oráculo al crear nuevos contratos (p. ej. pasar de
    Chainlink a Pyth) sin avisar. Si eso ocurre, el feed Chainlink dejaría de
    coincidir con la resolución y operaríamos a ciegas.

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


_GRAN_COINBASE = {"1m": 60, "5m": 300, "15m": 900}


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
