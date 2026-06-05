import asyncio
import aiohttp
import csv
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analisis_historico_modulo import ejecutar_analisis_temporal, probabilidad_temporal
from cli_util import (init, aviso, ventana, senal, resumen_final,
                      parse_mercado, token_up, simular_fill_clob, verificar_oraculo,
                      revalidar_fill, tendencia_pct, registrar_resultado_real,
                      resultado_resuelto, FeedChainlink, a_et)

TF = "5m"
SOURCE = "coinbase"          # solo para el filtro de régimen (tendencia 60m); el precio en
                             # vivo y el strike vienen del feed Chainlink (igual que Polymarket)
FUENTE = "Chainlink BTC/USD (feed en vivo de Polymarket)"
TABLA = {}
MIN_LIQUIDEZ = 5000.0
TAMANO_ORDEN = 100.0       # unidades de UP que simulamos comprar (para medir slippage real del libro)
FEE = 0.01                 # colchón de comisiones (el slippage ya lo modela el VWAP del order book)
MARGEN_EV_MINIMO = 0.05
MIN_PROB = 0.52            # convicción mínima: la prob (Wilson LB) debe superar esto. Knob frecuencia↔convicción
RETARDO_FILL_S = 0.5       # latencia simulada decisión→fill: re-lee el libro antes de "ejecutar" (orden límite)
REGIMEN_LOOKBACK_MIN = 60  # ventana para medir el régimen (tendencia reciente)
REGIMEN_MIN_PCT = -2.0     # solo pausa en free-fall REAL (<-2%/60m). Datos 30d: caídas -0.6..-2%
                           # ganan ~70%, así que el umbral es seguro de cola ante un bear sostenido
                           # fuera de muestra, no un filtro de edge in-sample.
DIAS_HISTORICOS = 30
REFRESH_HORAS = 24
INTERVALO_CHEQUEO = 30
VENTANA_MINUTOS = 5
MAX_INTENTOS_RESOLUCION = 20   # reintentos para leer la liquidación real (Polymarket tarda unos s)


def obtener_inicio_ventana():
    ahora = datetime.now(timezone.utc)
    minutos = (ahora.minute // VENTANA_MINUTOS) * VENTANA_MINUTOS
    return ahora.replace(minute=minutos, second=0, microsecond=0)


def generar_slug():
    inicio = obtener_inicio_ventana()
    return f"btc-updown-5m-{int(inicio.timestamp())}"


async def obtener_mercado(session):
    """Devuelve datos del mercado simulando el fill REAL de una orden de TAMANO_ORDEN.

    Caminamos el order book del CLOB para obtener el precio promedio de ejecución
    (VWAP), que modela el slippage real. Devuelve dict o None si no hay mercado.
    """
    try:
        slug = generar_slug()
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        async with session.get(url) as resp:
            data = await resp.json()
        if not data:
            return None
        event = data[0]
        oraculo_ok, oraculo_msg = verificar_oraculo(event, TF)
        _, volumen, liquidez = parse_mercado(event)
        tid = token_up(event)
        vwap, llenado, completo, niveles, mejor_ask = await simular_fill_clob(
            session, tid, TAMANO_ORDEN)
        return {"vwap": vwap, "llenado": llenado, "completo": completo,
                "niveles": niveles, "mejor_ask": mejor_ask, "token_id": tid,
                "volumen": volumen, "liquidez": liquidez,
                "oraculo_ok": oraculo_ok, "oraculo_msg": oraculo_msg}
    except Exception:
        return None


async def main():
    global TABLA

    init(TF, "calibrando 30 días (probabilidad por minuto)…")
    TABLA = ejecutar_analisis_temporal(interval="5m", days=DIAS_HISTORICOS, source=SOURCE)

    CSV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "senales_5m.csv")

    init(TF, f"filtros: liquidez≥${MIN_LIQUIDEZ:,.0f} · orden {TAMANO_ORDEN:.0f}u · EV≥{MARGEN_EV_MINIMO*100:.0f}% · fee {FEE:.2f} · ventana {VENTANA_MINUTOS}m")
    init(TF, f"fuente de precio: {FUENTE} — misma que Polymarket")

    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(['Fecha/Hora', 'Ventana', 'Min transcurrido', 'Apertura', 'Minimo', 'Caida %',
                                    'Prob', 'Ask UP', 'Volumen', 'Liquidez', 'EV', 'Accion', 'Resultado'])
        with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow([f"=== SESION: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} ==="])

    async with aiohttp.ClientSession() as session:
        # Feed Chainlink BTC/USD en vivo (EXACTAMENTE la fuente de Polymarket).
        feed = FeedChainlink()
        await feed.conectar(session)
        if await feed.esperar_datos():
            init(TF, f"feed Chainlink conectado · BTC ${feed.precio_actual():,.2f}")
        else:
            aviso(TF, "feed Chainlink sin datos aún; reintentando en segundo plano")

        apertura = None
        minimo = None
        ventana_actual = None
        inicio_ventana = None
        senal_ant = False
        senal_precio_up = 0.0
        senal_slug = None            # slug del mercado donde entramos (para leer su liquidación real)
        senal_ya_registrada = False
        pendientes = []              # señales esperando la resolución REAL de Polymarket
        wins = 0
        losses = 0
        pnl_total = 0.0
        ultimo_refresh = datetime.now(timezone.utc)

        while True:
            try:
                ahora = datetime.now(timezone.utc)

                if (ahora - ultimo_refresh).total_seconds() >= REFRESH_HORAS * 3600:
                    init(TF, "re-calibrando…")
                    # En un executor para NO bloquear el event loop (mantiene vivo el feed).
                    TABLA = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: ejecutar_analisis_temporal(
                            interval="5m", days=DIAS_HISTORICOS, source=SOURCE))
                    ultimo_refresh = ahora

                inicio_ventana = obtener_inicio_ventana()
                inicio_str = inicio_ventana.strftime("%Y-%m-%d %H:%M:%S")
                fin_ventana = inicio_ventana + timedelta(minutes=VENTANA_MINUTOS)

                # --- Resolución EXACTA: leer la liquidación real de Polymarket ---
                # Para cada señal cuya ventana ya cerró, consultamos el outcome real
                # (['1','0']=UP, ['0','1']=DOWN). Reintentamos porque la liquidación
                # tarda unos segundos tras el cierre.
                aun_pendientes = []
                for p in pendientes:
                    if ahora < p["fin"]:
                        aun_pendientes.append(p)
                        continue
                    res = await resultado_resuelto(session, p["slug"])
                    if res is None:
                        p["intentos"] += 1
                        if p["intentos"] <= MAX_INTENTOS_RESOLUCION:
                            aun_pendientes.append(p)
                        else:
                            aviso(TF, f"sin liquidación tras {p['intentos']} intentos: {p['slug']}")
                        continue
                    gano = (res == p["lado"])
                    wins, losses, pnl_total, etiqueta = registrar_resultado_real(
                        TF, gano, p["lado"], p["entrada"], wins, losses, pnl_total,
                        detalle=f"({p['ventana']} · resolvió {res})")
                    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
                        csv.writer(f).writerow([''] * 12 + [etiqueta])
                pendientes = aun_pendientes

                # Precio en vivo = último tick de Chainlink (= "precio actual" de Polymarket).
                precio_actual = feed.precio_actual()
                if precio_actual is None:
                    await asyncio.sleep(INTERVALO_CHEQUEO)
                    continue

                ts_inicio_ms = int(inicio_ventana.timestamp() * 1000)

                if ventana_actual != inicio_str:
                    # Nueva ventana: si la anterior tuvo señal, queda PENDIENTE de
                    # leer su resolución real de Polymarket (arriba). No inferimos el
                    # resultado con velas; lo tomamos de la liquidación oficial.
                    if senal_ant and senal_slug:
                        pendientes.append({"slug": senal_slug, "lado": "UP",
                                           "entrada": senal_precio_up, "ventana": ventana_actual,
                                           "fin": inicio_ventana, "intentos": 0})

                    # PRECIO A SUPERAR (strike) EXACTO: primer tick de Chainlink en/ tras
                    # la frontera de la ventana — idéntico al que muestra/usa Polymarket.
                    strike = feed.strike_en(ts_inicio_ms)
                    apertura = strike if strike else precio_actual
                    minimo = feed.minimo_desde(ts_inicio_ms) or apertura
                    ventana_actual = inicio_str
                    senal_ant = False
                    senal_slug = None
                    senal_ya_registrada = False

                    ini_et = a_et(inicio_ventana).strftime('%H:%M')
                    fin_et = a_et(fin_ventana).strftime('%H:%M')
                    ventana(TF, f"{ini_et}-{fin_et} ET · precio a superar ${apertura:,.2f} · actual ${precio_actual:,.2f}")
                else:
                    # Strike/mínimo siempre desde el feed (per-segundo, exacto).
                    strike = feed.strike_en(ts_inicio_ms)
                    if strike:
                        apertura = strike
                    mn = feed.minimo_desde(ts_inicio_ms)
                    minimo = mn if mn is not None else min(minimo, precio_actual)

                caida = (apertura - minimo) / apertura
                caida_pct = caida * 100
                elapsed_min = (ahora - inicio_ventana).total_seconds() / 60

                minutos_restantes = VENTANA_MINUTOS - (ahora.minute % VENTANA_MINUTOS)
                if minutos_restantes <= 1:
                    await asyncio.sleep(INTERVALO_CHEQUEO)
                    continue

                prob = probabilidad_temporal(TABLA, "5m", elapsed_min, caida_pct)

                if prob >= MIN_PROB and not senal_ya_registrada:
                    tend = await tendencia_pct(session, REGIMEN_LOOKBACK_MIN, SOURCE)
                    if tend < REGIMEN_MIN_PCT:
                        aviso(TF, f"régimen bajista ({tend:+.2f}% en {REGIMEN_LOOKBACK_MIN}m) · sin operar")
                        senal_ya_registrada = True
                        await asyncio.sleep(INTERVALO_CHEQUEO)
                        continue
                    mercado = await obtener_mercado(session)
                    if mercado:
                        if not mercado["oraculo_ok"]:
                            aviso(TF, mercado["oraculo_msg"])
                            senal_ya_registrada = True
                            await asyncio.sleep(INTERVALO_CHEQUEO)
                            continue
                        vwap = mercado["vwap"]
                        completo = mercado["completo"]
                        mejor_ask = mercado["mejor_ask"]
                        niveles = mercado["niveles"]
                        liquidez = mercado["liquidez"]
                        volumen = mercado["volumen"]
                        if vwap >= 0.01 and liquidez >= MIN_LIQUIDEZ and completo:
                            precio_entrada = vwap + FEE
                            slippage = vwap - mejor_ask
                            ev = prob - precio_entrada
                            if ev >= MARGEN_EV_MINIMO:
                                # Re-validación pre-fill (orden límite): tras la latencia
                                # decisión→fill, re-lee el libro. Solo "ejecuta" si el precio
                                # fresco sigue dando EV≥margen; si se movió en contra, no llena.
                                precio_limite = prob - MARGEN_EV_MINIMO
                                rev = await revalidar_fill(session, mercado["token_id"], TAMANO_ORDEN,
                                                           precio_limite, fee=FEE, retardo_s=RETARDO_FILL_S)
                                senal_ya_registrada = True  # un intento por ventana
                                if not rev["llenado_real"]:
                                    aviso(TF, f"sin fill (+{int(elapsed_min)}min) · {rev['motivo']}")
                                else:
                                    precio_entrada = rev["precio_entrada"]
                                    ev = prob - precio_entrada
                                    slippage = rev["vwap"] - rev["mejor_ask"]
                                    senal_ant = True
                                    senal_precio_up = precio_entrada
                                    senal_slug = generar_slug()  # mercado de esta ventana, para leer su liquidación real
                                    senal(TF, f"SEÑAL · +{int(elapsed_min)}min · caída {caida_pct:.2f}% · prob {prob*100:.0f}% · fill ${rev['vwap']:.3f} (mejor ${rev['mejor_ask']:.2f} +slip ${slippage:.3f}/{rev['niveles']}niv +fee {FEE:.2f}) · EV +{ev*100:.1f}% · liq ${liquidez:,.0f}")
                                    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
                                        csv.writer(f).writerow([ahora.strftime("%Y-%m-%d %H:%M:%S"), inicio_str, f"{int(elapsed_min)}",
                                            f"{apertura:,.2f}", f"{minimo:,.2f}", f"{caida_pct:.2f}%",
                                            f"{prob*100:.1f}%", f"{precio_entrada:.3f}", f"{volumen:,.0f}", f"{liquidez:,.0f}",
                                            f"+{ev*100:.1f}%", "COMPRAR UP", ""])

                await asyncio.sleep(INTERVALO_CHEQUEO)

            except KeyboardInterrupt:
                resumen_final(TF, wins, losses, pnl_total)
                break
            except Exception as e:
                aviso(TF, f"error: {e}")
                await asyncio.sleep(INTERVALO_CHEQUEO)


if __name__ == "__main__":
    asyncio.run(main())
