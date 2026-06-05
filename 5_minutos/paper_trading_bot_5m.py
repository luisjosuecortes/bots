import asyncio
import aiohttp
import csv
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analisis_historico_modulo import ejecutar_analisis_temporal, probabilidad_temporal
from cli_util import (init, aviso, ventana, senal, registrar_resultado, resumen_final,
                      parse_mercado, token_up, simular_fill_clob, verificar_oraculo,
                      revalidar_fill, precio_frontera, tendencia_pct, precio_vivo, apertura_y_minimo)

TF = "5m"
SOURCE = "coinbase"          # Coinbase BTC/USD: par USD = el de la resolución (Chainlink BTC/USD)
FUENTE = "Chainlink BTC/USD ≈ Coinbase BTC/USD"
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
    init(TF, f"fuente de precio: Coinbase BTC/USD (par USD = la resolución {FUENTE})")

    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(['Fecha/Hora', 'Ventana', 'Min transcurrido', 'Apertura', 'Minimo', 'Caida %',
                                    'Prob', 'Ask UP', 'Volumen', 'Liquidez', 'EV', 'Accion', 'Resultado'])
        with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow([f"=== SESION: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} ==="])

    async with aiohttp.ClientSession() as session:
        apertura = None
        minimo = None
        ventana_actual = None
        inicio_ventana = None
        apertura_ant = None
        cierre_ant = None
        senal_ant = False
        senal_precio_up = 0.0
        senal_ya_registrada = False
        wins = 0
        losses = 0
        pnl_total = 0.0
        ultimo_refresh = datetime.now(timezone.utc)

        while True:
            try:
                ahora = datetime.now(timezone.utc)

                if (ahora - ultimo_refresh).total_seconds() >= REFRESH_HORAS * 3600:
                    init(TF, "re-calibrando…")
                    TABLA = ejecutar_analisis_temporal(interval="5m", days=DIAS_HISTORICOS, source=SOURCE)
                    ultimo_refresh = ahora

                inicio_ventana = obtener_inicio_ventana()
                inicio_str = inicio_ventana.strftime("%Y-%m-%d %H:%M:%S")
                fin_ventana = inicio_ventana + timedelta(minutes=VENTANA_MINUTOS)

                precio_actual = await precio_vivo(session, SOURCE)
                if precio_actual is None:
                    await asyncio.sleep(INTERVALO_CHEQUEO)
                    continue

                if ventana_actual != inicio_str:
                    # Cierre de la ventana anterior = precio EXACTO en la frontera
                    # (apertura de la nueva ventana), determinista e idéntico para
                    # todos los bots. Corrige el desfase del ticker en vivo.
                    precio_real, minimo_real = await apertura_y_minimo(session, inicio_ventana, SOURCE)
                    if precio_real is None:
                        precio_real = await precio_frontera(session, inicio_ventana, SOURCE)
                    cierre_ant = precio_real if precio_real else precio_actual

                    wins, losses, pnl_total, etiqueta = registrar_resultado(
                        TF, apertura, cierre_ant, senal_ant, senal_precio_up, wins, losses, pnl_total)
                    if etiqueta and senal_ant:
                        with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
                            csv.writer(f).writerow([''] * 12 + [etiqueta])

                    apertura_ant = apertura
                    apertura = precio_real if precio_real else precio_actual
                    minimo = min(minimo_real, precio_actual) if minimo_real else precio_actual
                    ventana_actual = inicio_str
                    senal_ant = False
                    senal_ya_registrada = False

                    extra = f" · cierre ant ${cierre_ant:,.2f}" if apertura_ant else ""
                    ventana(TF, f"{inicio_ventana.strftime('%H:%M')}→{fin_ventana.strftime('%H:%M')} · apertura ${apertura:,.2f}{extra}")
                else:
                    if precio_actual < minimo:
                        minimo = precio_actual

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
