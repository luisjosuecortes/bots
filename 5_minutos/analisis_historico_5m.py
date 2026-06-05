import urllib.request
import json
import time
from datetime import datetime, timezone
from collections import defaultdict

print("🚀 Iniciando análisis granular de datos históricos de Binance (5 minutos, 30 días)...")

symbol = "BTCUSDT"
interval = "5m"
limit = 1000
days = 30
end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
start_time = end_time - (days * 24 * 60 * 60 * 1000)

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
            current_start = data[-1][0] + 300000  # Avanzar 5 minutos
            time.sleep(0.1)
    except Exception:
        break

print(f"✅ Descarga completada. Total de velas de 5m: {len(all_candles)}")

# Agrupar por ventana de 5 minutos
windows_data = {}
for candle in all_candles:
    open_time_ms = candle[0]
    open_price = float(candle[1])
    low_price = float(candle[3])
    close_price = float(candle[4])
    
    window_key = (open_time_ms // (5 * 60 * 1000)) * (5 * 60 * 1000)
    
    if window_key not in windows_data:
        windows_data[window_key] = {'open': open_price, 'low': low_price, 'close': close_price}
    else:
        if low_price < windows_data[window_key]['low']:
            windows_data[window_key]['low'] = low_price
        windows_data[window_key]['close'] = close_price

# Análisis granular por rangos de caída (más ajustados para 5 minutos)
buckets = defaultdict(lambda: {'total': 0, 'up_wins': 0})

for window_key, data in windows_data.items():
    open_p = data['open']
    low_p = data['low']
    close_p = data['close']
    
    caida = (open_p - low_p) / open_p
    caida_pct = caida * 100
    
    # Rangos de 0.05% en 0.05% para 5 minutos
    if caida_pct <= 0.05:
        bucket = "0.0005"
    elif caida_pct <= 0.10:
        bucket = "0.001"
    elif caida_pct <= 0.15:
        bucket = "0.0015"
    elif caida_pct <= 0.20:
        bucket = "0.002"
    elif caida_pct <= 0.30:
        bucket = "0.003"
    else:
        bucket = ">0.003"
        
    buckets[bucket]['total'] += 1
    if close_p >= open_p:
        buckets[bucket]['up_wins'] += 1

print("\n" + "="*65)
print("📊 MAPA DE PROBABILIDADES HISTÓRICAS (5 MINUTOS)")
print("="*65)
print(f"{'Rango de Caída':<18} | {'Muestras':<10} | {'Victorias UP':<12} | {'Probabilidad UP':<15}")
print("-" * 65)

mapa_para_bot = {}
bucket_order = ["0.0005", "0.001", "0.0015", "0.002", "0.003", ">0.003"]
bucket_labels = {
    "0.0005": "0.00% - 0.05%",
    "0.001": "0.05% - 0.10%",
    "0.0015": "0.10% - 0.15%",
    "0.002": "0.15% - 0.20%",
    "0.003": "0.20% - 0.30%",
    ">0.003": "> 0.30%"
}

for bucket in bucket_order:
    label = bucket_labels[bucket]
    if bucket in buckets and buckets[bucket]['total'] >= 20:
        prob = (buckets[bucket]['up_wins'] / buckets[bucket]['total'])
        if bucket != ">0.003":
            mapa_para_bot[float(bucket)] = round(prob, 3)
        print(f"{label:<18} | {buckets[bucket]['total']:<10} | {buckets[bucket]['up_wins']:<12} | {prob*100:.1f}%")
    elif bucket in buckets:
        print(f"{label:<18} | {buckets[bucket]['total']:<10} | {buckets[bucket]['up_wins']:<12} | (Muestra baja)")

print("\n" + "="*65)
print("📋 COPIA ESTE DICCIONARIO EN TU BOT DE 5 MINUTOS:")
print("MAPA_PROBABILIDADES = {")
for k, v in sorted(mapa_para_bot.items()):
    pct_label = f"{k*100:.2f}%"
    print(f"    {k}: {v},  # Caída <= {pct_label} -> {v*100:.1f}% prob. UP")
print("}")
print("="*65)
print("\n📌 PARÁMETROS RECOMENDADOS PARA EL BOT DE 5 MINUTOS:")
print("   - Frecuencia de chequeo: Cada 30 segundos")
print("   - Filtro de liquidez: >= $5,000 USD volumen 24h")
print("   - Margen de EV Neto mínimo: >= 5%")
print("   - Tiempo de validación: 6-8 horas (para ~40 señales estadísticamente válidas)")
print("="*65)