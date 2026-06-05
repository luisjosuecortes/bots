import urllib.request
import json
import time
from datetime import datetime, timezone
from collections import defaultdict

print("🚀 Iniciando análisis granular de datos históricos de Binance (1 hora, 30 días)...")

symbol = "BTCUSDT"
interval = "1m"
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
            current_start = data[-1][0] + 60000
            time.sleep(0.1)
    except Exception:
        break

print(f"✅ Descarga completada. Total de velas: {len(all_candles)}")

hours_data = {}
for candle in all_candles:
    open_time_ms = candle[0]
    open_price = float(candle[1])
    low_price = float(candle[3])
    close_price = float(candle[4])
    
    hour_key = (open_time_ms // (60 * 60 * 1000)) * (60 * 60 * 1000)
    
    if hour_key not in hours_data:
        hours_data[hour_key] = {'open': open_price, 'low': low_price, 'close': close_price}
    else:
        if low_price < hours_data[hour_key]['low']:
            hours_data[hour_key]['low'] = low_price
        hours_data[hour_key]['close'] = close_price

buckets = defaultdict(lambda: {'total': 0, 'up_wins': 0})

for hour_key, data in hours_data.items():
    open_p = data['open']
    low_p = data['low']
    close_p = data['close']
    
    caida = (open_p - low_p) / open_p
    caida_pct = caida * 100
    
    if caida_pct <= 0.1:
        bucket = "0.001" # Representa <= 0.1%
    elif caida_pct <= 0.2:
        bucket = "0.002" # Representa <= 0.2%
    else:
        bucket = ">0.002"
        
    buckets[bucket]['total'] += 1
    if close_p >= open_p:
        buckets[bucket]['up_wins'] += 1

print("\n" + "="*60)
print("📊 MAPA DE PROBABILIDADES HISTÓRICAS (1 HORA)")
print("="*60)

mapa_para_bot = {}
for bucket in ["0.001", "0.002"]:
    if bucket in buckets and buckets[bucket]['total'] >= 15:
        prob = (buckets[bucket]['up_wins'] / buckets[bucket]['total'])
        mapa_para_bot[float(bucket)] = round(prob, 3)
        print(f"Caída <= {float(bucket)*100:.1f}%: Muestras={buckets[bucket]['total']}, Prob. UP = {prob*100:.1f}%")

print("\n" + "="*60)
print("📋 COPIA ESTE DICCIONARIO EN TU BOT:")
print("MAPA_PROBABILIDADES = {")
for k, v in sorted(mapa_para_bot.items()):
    print(f"    {k}: {v},  # Caída <= {k*100:.1f}% -> {v*100:.1f}% prob. UP")
print("}")
print("="*60)