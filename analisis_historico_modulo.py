import urllib.request
import json
import time
import math
from datetime import datetime, timezone, timedelta
from collections import defaultdict

_UA = {"User-Agent": "Mozilla/5.0"}
_GRAN_COINBASE = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


def wilson_lower_bound(wins, n, z=1.645):
    """Límite inferior del intervalo de confianza de Wilson para una proporción.

    z=1.645 = test de UNA cola al 95% (la pregunta es "¿la prob real supera el
    umbral?", solo nos importa el límite INFERIOR). Penaliza los buckets con
    pocas muestras: con n grande converge a wins/n, con n chico se aleja hacia 0.5
    o por debajo, excluyendo automáticamente las ventajas que son solo ruido.
    """
    if n <= 0:
        return 0.0
    p = wins / n
    den = 1.0 + z * z / n
    centro = p + z * z / (2 * n)
    margen = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (centro - margen) / den


def descargar_velas_binance(symbol="BTCUSDT", interval="1m", days=30):
    """Descarga datos históricos de Binance. Formato Binance: [openTime, open, high, low, close, ...]."""
    limit = 1000
    end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_time = end_time - (days * 24 * 60 * 60 * 1000)
    
    interval_ms = {"1m": 60000, "5m": 300000, "15m": 900000, "30m": 1800000, "1h": 3600000, "4h": 14400000}
    ms = interval_ms.get(interval, 60000)
    
    all_candles = []
    current_start = start_time
    
    while current_start < end_time:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}&startTime={current_start}"
        try:
            with urllib.request.urlopen(url) as response:
                data = json.loads(response.read().decode())
                if not data:
                    break
                all_candles.extend(data)
                current_start = data[-1][0] + ms
                time.sleep(0.1)
        except Exception as e:
            print(f"⚠️  binance: error descargando velas {interval} ({len(all_candles)} acumuladas): {e}", flush=True)
            break
    
    return all_candles


def descargar_velas_coinbase(interval="1m", days=30, product="BTC-USD"):
    """Descarga histórico de Coinbase (par USD, igual que la fuente de resolución de 5m/15m).

    Coinbase devuelve [time_s, low, high, open, close, volume] (máx 300, más reciente
    primero). Se normaliza al MISMO formato que Binance: [openTime_ms, open, high, low, close]
    y se ordena ascendente, para que el resto del análisis funcione sin cambios.
    """
    gran = _GRAN_COINBASE.get(interval, 60)
    paso = gran  # segundos por vela
    span = 300 * paso  # segundos por petición (300 velas)
    end = int(datetime.now(timezone.utc).timestamp())
    inicio_global = end - days * 24 * 3600

    normalizadas = []
    cur_end = end
    while cur_end > inicio_global:
        cur_start = max(inicio_global, cur_end - span)
        s_iso = datetime.fromtimestamp(cur_start, timezone.utc).isoformat()
        e_iso = datetime.fromtimestamp(cur_end, timezone.utc).isoformat()
        url = (f"https://api.exchange.coinbase.com/products/{product}/candles"
               f"?granularity={gran}&start={s_iso}&end={e_iso}")
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=20) as response:
                data = json.loads(response.read().decode())
        except Exception as e:
            print(f"⚠️  coinbase: reintentando tramo {interval} ({len(normalizadas)} acumuladas): {e}", flush=True)
            time.sleep(0.4)
            cur_end = cur_start
            continue
        if not data:
            cur_end = cur_start
            continue
        for c in data:  # [time_s, low, high, open, close, volume]
            normalizadas.append([c[0] * 1000, c[3], c[2], c[1], c[4]])
        cur_end = cur_start
        time.sleep(0.25)

    normalizadas.sort(key=lambda x: x[0])
    return normalizadas


def descargar_velas(interval="1m", days=30, source="binance"):
    """Dispatcher de fuente. Devuelve velas normalizadas [openTime_ms, open, high, low, close]."""
    if source == "coinbase":
        return descargar_velas_coinbase(interval=interval, days=days)
    return descargar_velas_binance(interval=interval, days=days)

def agrupar_por_ventana(all_candles, interval):
    """Agrupa las velas por su ventana de tiempo correspondiente."""
    if interval == "1m":
        window_ms = 60000
    elif interval == "5m":
        window_ms = 300000
    elif interval == "15m":
        window_ms = 900000
    elif interval == "30m":
        window_ms = 1800000
    elif interval == "1h":
        window_ms = 3600000
    elif interval == "4h":
        window_ms = 14400000
    else:
        window_ms = 60000
    
    windows_data = {}
    for candle in all_candles:
        open_time_ms = candle[0]
        open_price = float(candle[1])
        low_price = float(candle[3])
        close_price = float(candle[4])
        
        window_key = (open_time_ms // window_ms) * window_ms
        
        if window_key not in windows_data:
            windows_data[window_key] = {'open': open_price, 'low': low_price, 'close': close_price}
        else:
            if low_price < windows_data[window_key]['low']:
                windows_data[window_key]['low'] = low_price
            windows_data[window_key]['close'] = close_price
    
    return windows_data

def calcular_mapa_probabilidades(windows_data, interval):
    """Calcula el mapa de probabilidades basado en los datos históricos agrupados."""
    buckets = defaultdict(lambda: {'total': 0, 'up_wins': 0})
    
    for window_key, data in windows_data.items():
        open_p = data['open']
        low_p = data['low']
        close_p = data['close']
        
        if open_p == 0:
            continue
            
        caida = (open_p - low_p) / open_p
        caida_pct = caida * 100
        
        # Definir rangos según el intervalo
        if interval in ["1m", "5m"]:
            # Rangos de 0.05% para intervalos cortos
            if caida_pct <= 0.05:
                bucket = 0.0005
            elif caida_pct <= 0.10:
                bucket = 0.001
            elif caida_pct <= 0.15:
                bucket = 0.0015
            elif caida_pct <= 0.20:
                bucket = 0.002
            elif caida_pct <= 0.30:
                bucket = 0.003
            else:
                bucket = None
        elif interval in ["15m", "30m"]:
            # Rangos de 0.1% para intervalos medianos
            if caida_pct <= 0.1:
                bucket = 0.001
            elif caida_pct <= 0.2:
                bucket = 0.002
            elif caida_pct <= 0.3:
                bucket = 0.003
            elif caida_pct <= 0.5:
                bucket = 0.005
            elif caida_pct <= 1.0:
                bucket = 0.01
            else:
                bucket = None
        elif interval == "1h":
            # Rangos de 0.1% para 1 hora
            if caida_pct <= 0.1:
                bucket = 0.001
            elif caida_pct <= 0.2:
                bucket = 0.002
            elif caida_pct <= 0.5:
                bucket = 0.005
            elif caida_pct <= 1.0:
                bucket = 0.01
            else:
                bucket = None
        elif interval == "4h":
            # Rangos de 0.2% para 4 horas
            if caida_pct <= 0.2:
                bucket = 0.002
            elif caida_pct <= 0.5:
                bucket = 0.005
            elif caida_pct <= 1.0:
                bucket = 0.01
            elif caida_pct <= 2.0:
                bucket = 0.02
            else:
                bucket = None
        else:
            bucket = None
        
        if bucket is not None:
            buckets[bucket]['total'] += 1
            if close_p >= open_p:
                buckets[bucket]['up_wins'] += 1
    
    # Construir el mapa final. Se usa el LÍMITE INFERIOR de Wilson en vez de la
    # proporción cruda: solo se incluye un bucket si estamos 95% seguros (1 cola)
    # de que la ventaja real supera el 50%. Esto poda los edges que son ruido.
    min_muestras = 15
    mapa = {}
    for bucket, stats in sorted(buckets.items()):
        if stats['total'] >= min_muestras:
            lb = wilson_lower_bound(stats['up_wins'], stats['total'])
            if lb > 0.5:
                mapa[bucket] = round(lb, 3)

    return mapa

def ejecutar_analisis(interval="1m", days=30):
    """Función principal: descarga datos, analiza y devuelve el mapa de probabilidades."""
    candles = descargar_velas_binance(interval=interval, days=days)
    windows = agrupar_por_ventana(candles, interval)
    mapa = calcular_mapa_probabilidades(windows, interval)

    print(f"⚙️  calib {interval}: {len(candles)} velas · {len(windows)} ventanas · {len(mapa)} rangos con ventaja", flush=True)
    
    return mapa


# ---------------------------------------------------------------------------
# Análisis TEMPORAL: probabilidad condicionada al tiempo transcurrido.
#
# El mapa simple responde "dado que la caída MÁXIMA de toda la ventana fue <=X%,
# ¿qué % cerró UP?". En vivo eso es sesgo de look-ahead: al inicio de la ventana
# la caída acumulada es ~0 pero todavía no sabes cuánto caerá. El mapa temporal
# responde la pregunta correcta: "dado que a los E minutos la caída ACUMULADA
# hasta ahora es <=X%, ¿qué % cierra UP?".
# ---------------------------------------------------------------------------

# (sub_intervalo, paso_min, checkpoints_en_minutos, minutos_ventana)
_SUB_CONFIG = {
    "5m":  ("1m", 1, list(range(1, 5)),  5),
    "15m": ("1m", 1, list(range(1, 15)), 15),
    "1h":  ("5m", 5, list(range(5, 60, 5)), 60),
}


def _drop_bucket(interval, caida_pct):
    if interval in ("1m", "5m"):
        if caida_pct <= 0.05: return 0.0005
        if caida_pct <= 0.10: return 0.001
        if caida_pct <= 0.15: return 0.0015
        if caida_pct <= 0.20: return 0.002
        if caida_pct <= 0.30: return 0.003
        return None
    if interval in ("15m", "30m"):
        if caida_pct <= 0.1: return 0.001
        if caida_pct <= 0.2: return 0.002
        if caida_pct <= 0.3: return 0.003
        if caida_pct <= 0.5: return 0.005
        if caida_pct <= 1.0: return 0.01
        return None
    if interval == "1h":
        if caida_pct <= 0.1: return 0.001
        if caida_pct <= 0.2: return 0.002
        if caida_pct <= 0.5: return 0.005
        if caida_pct <= 1.0: return 0.01
        return None
    return None


def ejecutar_analisis_temporal(interval="5m", days=30, source="binance"):
    """Descarga sub-velas finas y construye la tabla de probabilidad condicionada al tiempo.

    `source` selecciona la fuente de precio: "binance" (BTC/USDT, para el bot de 1h) o
    "coinbase" (BTC/USD, para 5m/15m, que es el par que usa la resolución por Chainlink).

    Devuelve {elapsed_min: {drop_limit: prob}} solo con buckets que tengan muestras
    suficientes y ventaja estadística real (prob > 0.5).
    """
    sub_intervalo, paso, checkpoints, win_min = _SUB_CONFIG[interval]
    window_ms = win_min * 60000
    sub_ms = paso * 60000

    candles = descargar_velas(interval=sub_intervalo, days=days, source=source)

    # Agrupar sub-velas por ventana padre, en orden temporal.
    ventanas = defaultdict(list)
    for c in candles:
        open_time = c[0]
        wk = (open_time // window_ms) * window_ms
        ventanas[wk].append((open_time, float(c[1]), float(c[3]), float(c[4])))  # t, open, low, close

    # buckets[(elapsed, drop_limit)] = {'total':, 'up_wins':}
    buckets = defaultdict(lambda: {"total": 0, "up_wins": 0})
    completas = 0

    for wk, subs in ventanas.items():
        subs.sort(key=lambda x: x[0])
        n_esperadas = window_ms // sub_ms
        if len(subs) < n_esperadas:
            continue  # ventana incompleta
        completas += 1
        open_p = subs[0][1]
        close_p = subs[-1][3]
        if open_p == 0:
            continue
        cierre_up = close_p >= open_p

        running_low = open_p
        for i, (_, _, low_p, _) in enumerate(subs):
            if low_p < running_low:
                running_low = low_p
            elapsed = (i + 1) * paso  # minutos transcurridos al cerrar esta sub-vela
            if elapsed not in checkpoints:
                continue
            caida_pct = (open_p - running_low) / open_p * 100
            bucket = _drop_bucket(interval, caida_pct)
            if bucket is None:
                continue
            key = (elapsed, bucket)
            buckets[key]["total"] += 1
            if cierre_up:
                buckets[key]["up_wins"] += 1

    min_muestras = 15
    tabla = {}
    for (elapsed, bucket), stats in buckets.items():
        if stats["total"] >= min_muestras:
            # Wilson LB (1 cola, 95%): probabilidad CONSERVADORA. Se usa este valor
            # tanto para decidir si el bucket tiene ventaja real (LB>0.5) como para
            # el cálculo de EV en el bot, así el EV nunca sobreestima la ventaja.
            lb = wilson_lower_bound(stats["up_wins"], stats["total"])
            if lb > 0.5:
                tabla.setdefault(elapsed, {})[bucket] = round(lb, 3)

    n_rangos = sum(len(v) for v in tabla.values())
    print(f"⚙️  calib {interval}: {len(candles)} sub-velas · {completas} ventanas · "
          f"{len(tabla)} checkpoints · {n_rangos} rangos con ventaja", flush=True)
    return tabla


def probabilidad_temporal(tabla, interval, elapsed_min, caida_pct):
    """Busca P(UP) condicionada a (minutos transcurridos, caída acumulada).

    Ajusta `elapsed_min` a la rejilla de checkpoints del timeframe. Si todavía no
    se alcanza el primer checkpoint o no hay bucket con ventaja, devuelve 0.0.
    """
    if not tabla:
        return 0.0
    _, paso, checkpoints, _ = _SUB_CONFIG[interval]
    cp = (int(elapsed_min) // paso) * paso
    if cp < checkpoints[0]:
        return 0.0
    if cp > checkpoints[-1]:
        cp = checkpoints[-1]
    fila = tabla.get(cp)
    if not fila:
        return 0.0
    caida = round(caida_pct, 6)
    for limite, prob in sorted(fila.items()):
        if caida <= limite * 100:
            return prob
    return 0.0


if __name__ == "__main__":
    print("=" * 60)
    print("PRUEBA DEL MÓDULO DE ANÁLISIS TEMPORAL")
    print("=" * 60)
    for interval in ["5m", "15m", "1h"]:
        print(f"\n--- {interval} ---")
        tabla = ejecutar_analisis_temporal(interval=interval, days=30)
        for cp in sorted(tabla):
            print(f"  +{cp}min -> {tabla[cp]}")
