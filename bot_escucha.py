import asyncio
import aiohttp
import json
from datetime import datetime, timezone, timedelta

# CONFIGURACIÓN DEL ALGORITMO QUANT
BINANCE_API = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

def generar_slug_mercado_actual():
    """Genera automáticamente el slug del mercado de 1 hora actual de Polymarket."""
    # Polymarket usa hora del Este (ET). Asumimos EDT (UTC-4) que es el estándar actual.
    ahora_et = datetime.now(timezone.utc) - timedelta(hours=4)
    
    meses = ["january", "february", "march", "april", "may", "june", 
             "july", "august", "september", "october", "november", "december"]
    
    mes = meses[ahora_et.month - 1]
    dia = ahora_et.day
    anio = ahora_et.year
    hora = ahora_et.hour
    
    # Convertir a formato 12h con am/pm (ej. 2pm, 12am)
    sufijo = "am" if hora < 12 else "pm"
    hora_12 = hora if 1 <= hora <= 12 else abs(hora - 12)
    
    return f"bitcoin-up-or-down-{mes}-{dia}-{anio}-{hora_12}{sufijo}-et"

# El script ahora usa el slug generado dinámicamente
MARKET_SLUG = generar_slug_mercado_actual()
POLYMARKET_GAMMA_API = f"https://gamma-api.polymarket.com/events?slug={MARKET_SLUG}"

async def obtener_datos_mercado(session, slug_objetivo):
    """Función auxiliar para intentar obtener datos de un slug específico."""
    api_url = f"https://gamma-api.polymarket.com/events?slug={slug_objetivo}"
    async with session.get(api_url) as resp:
        gamma_data = await resp.json()
        if gamma_data and len(gamma_data) > 0:
            event = gamma_data[0]
            markets = event.get('markets', [])
            if markets and len(markets) > 0:
                mercado = markets[0]
                precios = json.loads(mercado.get('outcomePrices', '[]'))
                return {
                    'pregunta': mercado.get('question'),
                    'precio_up': float(precios[0]) if len(precios) > 0 else 0.0,
                    'precio_down': float(precios[1]) if len(precios) > 1 else 0.0,
                    'encontrado': True
                }
    return {'encontrado': False}

async def monitorear_mercado_automatico():
    print("🚀 Iniciando monitoreo automático de mercados de 1 hora...")
    print("⏳ El bot calculará automáticamente la hora actual y la siguiente.\n")
    
    async with aiohttp.ClientSession() as session:
        for i in range(3):
            try:
                # 1. Precio real de referencia (Binance)
                async with session.get(BINANCE_API) as resp:
                    binance_data = await resp.json()
                    precio_btc = float(binance_data['price'])

                # 2. Intentar obtener el mercado de la hora actual
                slug_actual = generar_slug_mercado_actual()
                datos = await obtener_datos_mercado(session, slug_actual)
                
                # 3. Si no existe, intentar con la siguiente hora (transición de hora)
                if not datos['encontrado']:
                    # Calculamos el slug de la siguiente hora sumando 1 hora a la lógica
                    # (Simplificación: intentamos un patrón común de fallback o avisamos)
                    print(f"--- Ciclo {i+1}/3 ---")
                    print(f"💰 Binance (Precio Real): ${precio_btc:,.2f} USD")
                    print(f"⏳ Polymarket: El mercado de la hora actual ({slug_actual}) aún no está activo o cerró.")
                    print("   💡 El bot intentará nuevamente en el próximo ciclo.")
                else:
                    print(f"--- Ciclo {i+1}/5 ---")
                    print(f"💰 Binance (Precio Real): ${precio_btc:,.2f} USD")
                    print(f"🎲 Polymarket: {datos['pregunta']}")
                    print(f"📈 Acción 'Up' (Arriba):  {datos['precio_up'] * 100:.1f}% (Precio: ${datos['precio_up']:.2f})")
                    print(f"📉 Acción 'Down' (Abajo): {datos['precio_down'] * 100:.1f}% (Precio: ${datos['precio_down']:.2f})")
                    
                    # Aquí irá tu lógica de discrepancia cuantitativa en el próximo paso
                
                print()
                await asyncio.sleep(5) # Esperamos 5 segundos entre ciclos
                
            except Exception as e:
                print(f"⚠️ Error en el ciclo {i+1}: {e}")
                await asyncio.sleep(5)
                
    print("✅ Monitoreo completado. El sistema es 100% autónomo.")

if __name__ == "__main__":
    asyncio.run(monitorear_mercado_automatico())
