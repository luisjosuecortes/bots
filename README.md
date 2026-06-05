# 📊 Sistema de Paper Trading Cuantitativo - Polymarket BTC

## Resumen del Proyecto

Sistema automatizado de **Paper Trading** (simulación sin riesgo) para los mercados *Bitcoin Up or Down* de **5 y 15 minutos** de Polymarket. Usa análisis estadístico de datos históricos para calcular la probabilidad real de reversión a la media, la compara con el precio ejecutable real del order book de Polymarket y detecta oportunidades de **Valor Esperado (EV) positivo**.

Todas las fuentes en vivo son **exactamente las de Polymarket**: el precio (y el "precio a superar") vienen del **feed de Chainlink BTC/USD** y el resultado de cada operación se lee de la **liquidación real** del mercado. Así el paper trading es fiel a lo que ocurriría operando en serio.

**Estado actual**: Sistema completo, en fase de validación estadística.

> 📖 Para la explicación paso a paso en lenguaje claro, ver **`COMO_FUNCIONA.md`**.

---

## Arquitectura del Sistema

### Estructura de Archivos

```
/home/penguin/Documentos/poly/
├── analisis_historico_modulo.py          # Módulo compartido: calibración temporal (histórico USD)
├── cli_util.py                           # CLI con iconos, feed Chainlink, P&L y CLOB de Polymarket
├── master_bot.py                         # Orquestador que lanza los 2 bots simultáneamente
├── README.md                             # Este archivo
├── COMO_FUNCIONA.md                      # Explicación detallada en lenguaje claro
│
├── 15_minutos/
│   ├── paper_trading_bot_15m.py          # Bot de 15 minutos (auto-calibrable)
│   └── senales_15m.csv                   # Datos acumulados de señales
│
└── 5_minutos/
    ├── paper_trading_bot_5m.py           # Bot de 5 minutos (auto-calibrable)
    ├── analisis_historico_5m.py          # Script de análisis independiente
    └── senales_5m.csv                    # Datos acumulados de señales
```

### Flujo de Ejecución

1. **Inicio**: Se ejecuta `master_bot.py` o un bot individual.
2. **Auto-calibración**: El bot descarga 30 días de velas (Coinbase BTC/USD) y construye una **tabla de probabilidad condicionada al tiempo** (P de cierre UP según los minutos transcurridos y la caída acumulada hasta ese momento). Esto alimenta **solo el modelo de entrada**.
3. **Conexión al feed Chainlink**: Abre el WebSocket de Polymarket (`wss://ws-live-data.polymarket.com`) para recibir el precio BTC/USD por segundo — la **misma fuente** que Polymarket muestra y con la que resuelve.
4. **Monitoreo continuo**: Cada ciclo (30s en 5m, 1min en 15m), el bot:
   - Resuelve las señales pendientes leyendo la **liquidación real** de Polymarket.
   - Lee el **precio a superar** (strike) y el **precio actual** del feed Chainlink.
   - Calcula la caída acumulada y los minutos transcurridos; consulta la tabla temporal.
   - Simula el **fill real** (VWAP) de una orden de 100 unidades caminando el order book CLOB de "UP".
   - Aplica los filtros (liquidez, llenado completo, EV, régimen, oráculo, re-validación).
   - Si hay señal válida (una por ventana), la registra en el CSV.
5. **Re-calibración**: Cada 24 horas, en un hilo aparte (no corta el feed).

---

## Parámetros del Sistema

### Probabilidad Condicionada al Tiempo (corrige el sesgo de look-ahead)

No se usa un único umbral de caída por timeframe (eso medía la caída *máxima de toda la ventana* y aplicarlo en vivo era look-ahead). Se construye una tabla **P(UP | minutos transcurridos, caída acumulada)**.

Ejemplo real para 5m (caída acumulada ≤ 0.05%):

| Minutos transcurridos | Probabilidad UP |
| :--- | :--- |
| +1 min | **53.7%** |
| +2 min | 58.2% |
| +3 min | 62.8% |
| +4 min | 67.4% |

La ventaja real solo aparece avanzada la ventana, no al abrir. El bot no emite señal antes del primer checkpoint.

### Filtros de Seguridad (aplicados a ambos bots)

| Filtro | Valor | Justificación |
| :--- | :--- | :--- |
| **Liquidez mínima** | ≥ $5,000 USD | Profundidad del order book (`liquidityNum`). Métrica correcta para slippage en mercados cortos (el "volumen 24h" es diminuto porque el mercado se recrea cada ventana). |
| **Llenado completo de la orden** | Se debe poder llenar 100 unidades caminando el libro | Si no hay profundidad, no hay señal. |
| **Precio de entrada** | **VWAP del fill simulado** + colchón de fee (0.01) | Modela el slippage real (una orden grande "come" varios niveles). Solo lectura / gratis: no envía órdenes. |
| **Ventaja estadística (Wilson)** | Solo buckets con **Wilson LB (1 cola, 95%) > 0.5** | Poda automáticamente las ventajas que son ruido por pocas muestras. |
| **Convicción mínima** | `prob (Wilson LB) ≥ MIN_PROB` (0.52) | Piso por encima del corte de Wilson. Knob frecuencia↔convicción. |
| **EV neto mínimo** | ≥ 5% | `prob − precio_entrada`, con `prob` = Wilson LB conservador y `precio_entrada` = VWAP + fee. |
| **Filtro de régimen** | Sin operar solo en *free-fall* real (**−2%**: 5m/60m · 15m/90m) | Las caídas moderadas (−0.6…−2%) históricamente rebotan ~70%; el filtro solo pausa en *free-fall* genuino. |
| **Verificación de oráculo** | La descripción del mercado debe mencionar **Chainlink** | Si Polymarket cambia la fuente de resolución, el bot **pausa y alerta** en vez de operar a ciegas. |
| **Re-validación pre-fill (orden límite)** | Espera `RETARDO_FILL_S` (0.5s), re-lee el libro y solo "ejecuta" si mantiene `EV ≥ 5%` | Modela la latencia decisión→fill (el *espejismo del ask*) como una orden límite: si el libro se movió en contra, no llena. |

### Filtros Sniper (evitar operaciones a último segundo)

| Timeframe | Ventana de pausa |
| :--- | :--- |
| 15 Minutos | Últimos 2 minutos |
| 5 Minutos | Último minuto |

### Frecuencias de Monitoreo y Refresh

| Timeframe | Frecuencia de chequeo | Refresh de parámetros |
| :--- | :--- | :--- |
| 15 Minutos | Cada 1 minuto | Cada 24 horas |
| 5 Minutos | Cada 30 segundos | Cada 24 horas |

---

## Validación de Resultados (resolución EXACTA)

Cada bot valida el resultado de sus señales con la **liquidación oficial de Polymarket**, no infiriéndolo con velas:

1. Al cerrar la ventana, Polymarket marca el mercado `closed` y pone `outcomePrices` en `["1","0"]` (ganó **UP**) o `["0","1"]` (ganó **DOWN**).
2. El bot consulta ese resultado (reintentando unos segundos, porque la liquidación tarda un poco).
3. Si compramos UP y ganó UP → **WIN**; si no → **LOSS**.
4. Actualiza wins/losses y el P&L acumulado, y lo registra en el CSV.

Esto es 100% exacto: es literalmente lo que Polymarket pagó, sin aproximar Chainlink.

### Estructura del CSV

| Columna | Descripción |
| :--- | :--- |
| Fecha/Hora | Marca de tiempo de la detección de la señal (UTC) |
| Ventana | Inicio de la ventana |
| Min transcurrido | Minutos dentro de la ventana al detectar |
| Apertura | **Precio a superar** (Chainlink, en la frontera) |
| Minimo | Mínimo alcanzado hasta el momento de la señal |
| Caída % | Caída acumulada |
| Prob | Probabilidad UP condicionada al tiempo |
| Ask UP | Precio ejecutable real = VWAP del fill + fee |
| Volumen | Volumen del mercado |
| Liquidez | Profundidad del order book (filtro) |
| EV | Valor esperado neto |
| Acción | "COMPRAR UP" |
| **Resultado** | **"WIN" o "LOSS"** (se llena al resolver Polymarket) |

### Ejemplo de salida en terminal

```
⚙️  [ 5m] calibrando 30 días (probabilidad por minuto)…
⚙️  calib 5m: 43201 sub-velas · 8640 ventanas · 4 checkpoints · 4 rangos con ventaja
⚙️  [ 5m] feed Chainlink conectado · BTC $60,373.50
🕒 [ 5m] 16:00-16:05 ET · precio a superar $60,277.32 · actual $60,373.50
🔴 [ 5m] SEÑAL · +3min · caída 0.03% · prob 63% · fill $0.520 (mejor $0.52 +slip $0.000/1niv +fee 0.01) · EV +10.7% · liq $10,063
🟢 [ 5m] GANÓ UP (… · resolvió UP) · aciertos 1/1 (100%) · P&L +0.47 (acum +0.47)
```

Al presionar `Ctrl + C`, cada bot muestra un resumen:

```
🟢 [ 5m] RESUMEN  12 señales · 9W/3L (75%) · GANANCIA neta +2.40 u
```

---

## Fuentes de Datos

| Uso | Fuente | Detalle |
| :--- | :--- | :--- |
| **Precio en vivo + precio a superar** | **Chainlink BTC/USD** (WebSocket Polymarket) | `wss://ws-live-data.polymarket.com`, tópico `crypto_prices_chainlink`. **Misma fuente que la resolución.** Sin auth. |
| **Resolución / resultado** | **Polymarket** (Gamma API) | `outcomePrices` del mercado cerrado: `["1","0"]`=UP, `["0","1"]`=DOWN. |
| **Precio ejecutable (entrada)** | **Polymarket CLOB** | `clob.polymarket.com/book?token_id=...`, fill simulado por VWAP. Solo lectura. |
| **Histórico de calibración** | **Coinbase BTC/USD** | 30 días de velas 1m. Solo alimenta el modelo de entrada (par USD ≈ Chainlink dentro de ~0.03%). |
| **Filtro de régimen** | **Coinbase BTC/USD** | Tendencia 60m (5m) / 90m (15m). Filtro grueso de free-fall. |

> **¿Por qué Chainlink en vivo y no Coinbase?** La resolución de 5m/15m es Chainlink BTC/USD. Coinbase/Binance se desvían **$13–23** del valor real de Chainlink, suficiente para invertir el resultado en una vela de 5 min. El feed de Polymarket entrega el precio exacto de Chainlink, así que el "precio a superar", el precio actual y la resolución coinciden 1:1 con lo que ves en la web. Chainlink Data Streams no ofrece histórico gratuito, por eso la **calibración** (offline, no decide el resultado) usa Coinbase como mejor proxy USD.

### Slugs de mercados
- 5 Minutos: `btc-updown-5m-{timestamp_unix_inicio}`
- 15 Minutos: `btc-updown-15m-{timestamp_unix_inicio}`

### Hora en ET
Los mercados se nombran y muestran en hora del Este. El bot usa `America/New_York` (DST automático EDT/EST), por eso la ventana sale como `16:00-16:05 ET`, igual que Polymarket.

---

## Descubrimientos Clave del Análisis

### 1. Reversión a la media condicionada al tiempo
Existe un patrón de reversión, pero su fuerza **depende del momento de la ventana**: una caída pequeña al inicio aporta poca ventaja; la ventaja crece a medida que avanza la ventana (5m: 53.7% a +1min → 67.4% a +4min).

### 2. El look-ahead inflaba las probabilidades
El enfoque anterior (umbral sobre la caída máxima de toda la ventana) reportaba 71–87%, no alcanzables en vivo. La tabla temporal lo corrige.

### 3. El precio ejecutable importa (fill por profundidad)
`outcomePrices` es un punto medio; lo que pagas es peor. Se simula el fill de 100 unidades caminando el order book (VWAP) para modelar el slippage real. Si no hay profundidad, no hay señal.

### 4. La fuente de resolución debe ser exacta
Aproximar Chainlink con Coinbase/Binance metía un error de $13–23 que podía invertir el resultado. Ahora el precio en vivo viene del feed Chainlink y **el resultado se lee de la liquidación real**, eliminando ese error por completo.

### 5. La ventaja sobrevive fuera de muestra
Calibrando con los primeros ~20 días y midiendo en los últimos ~10 no vistos, el winrate se mantiene (5m ~61.9% · 15m ~61.8%). El Wilson LB mejora el winrate out-of-sample al eliminar los buckets que no replican.

### 6. El régimen afecta de forma asimétrica
Más volatilidad previa = más edge de reversión (no menos). Por eso el filtro de caída se relajó a −2% (solo pausa en free-fall genuino); las caídas moderadas rinden ~70%.

---

## Problemas Conocidos y Decisiones

### Resolución por velas → liquidación real (Corregido)
- **Antes**: el resultado se inferían comparando apertura/cierre de velas (Coinbase), con error vs Chainlink.
- **Ahora**: se lee `outcomePrices` del mercado cerrado en Polymarket. 100% exacto.

### Precio aproximado (Coinbase) → Chainlink en vivo (Corregido)
- **Antes**: el "precio a superar" y el precio en vivo usaban Coinbase (±$13–23 vs Chainlink).
- **Ahora**: feed Chainlink de Polymarket (misma fuente que la resolución).

### Hora en UTC → ET (Corregido)
- **Antes**: la hora se mostraba en UTC y el slug de 1h hardcodeaba −4h (se rompía en invierno).
- **Ahora**: `America/New_York` con DST automático; las ventanas se muestran en ET como Polymarket.

### Bots retirados (1h y 4h)
- **4h**: Polymarket no publica ese mercado.
- **1h**: retirado para concentrar el sistema en 5m/15m (resolución Chainlink, feed en vivo).

### Filtro por liquidez, bug de $0.00 y de punto flotante
- Liquidez (`liquidityNum`) en vez de `volume24hr`; rechazo de precios < $0.01; `round(caida, 6)` antes de comparar buckets. Todos corregidos.

---

## Cómo Ejecutar el Sistema

### Opción 1: Bot Maestro (Recomendado)
```bash
python3 -u /home/penguin/Documentos/poly/master_bot.py
```
Lanza los 2 bots simultáneamente y muestra su salida en tiempo real.

### Opción 2: Bots Individuales
```bash
python3 -u /home/penguin/Documentos/poly/15_minutos/paper_trading_bot_15m.py
python3 -u /home/penguin/Documentos/poly/5_minutos/paper_trading_bot_5m.py
```

### Para detener
`Ctrl + C` (muestra el resumen de P&L de cada bot).

### Para revisar datos acumulados
```bash
cat /home/penguin/Documentos/poly/15_minutos/senales_15m.csv
cat /home/penguin/Documentos/poly/5_minutos/senales_5m.csv
```

---

## Tiempos de Validación Recomendados

| Timeframe | Tiempo mínimo | Señales esperadas |
| :--- | :--- | :--- |
| 5 Minutos | 6-8 horas | ~40-56 señales |
| 15 Minutos | 12-16 horas | ~20-30 señales |

---

## Notas Técnicas

### Dependencias
- Python 3.9+ (usa `zoneinfo`).
- `aiohttp` (requests asíncronos + WebSocket).
- Resto: librería estándar.

### APIs Utilizadas (todas públicas, sin autenticación)
- Polymarket WebSocket de precios Chainlink (`ws-live-data.polymarket.com`).
- Polymarket Gamma API (eventos, liquidación) y CLOB (order book).
- Coinbase Exchange API (histórico de calibración).

### Persistencia
- Los CSVs se abren en modo `append`: los datos se acumulan entre ejecuciones.

### Auto-calibración
- 30 días de histórico al iniciar; recálculo cada 24 horas en un hilo aparte (no corta el feed).
- Sin parámetros mágicos hardcodeados salvo los filtros de seguridad.

---

## Historial de Desarrollo

- **2026-06-03**: Sistema inicial (5m, 15m, 1h) con calibración temporal, fill por VWAP, filtro por liquidez.
- **2026-06-05**: Exactitud total con Polymarket.
  - Precio en vivo y "precio a superar" desde el **feed de Chainlink** de Polymarket (antes Coinbase).
  - Resolución y P&L desde la **liquidación real** de Polymarket (antes inferida con velas).
  - Hora mostrada en **ET** (`America/New_York`, DST automático).
  - Retiro del bot de **1 hora**; recalibración en hilo aparte para no cortar el feed.

---

## Contacto y Contexto

Proyecto de trading cuantitativo para mercados de predicción. La estrategia es **reversión a la media** (mean reversion) sobre micro-caídas. El bot detecta cuando la probabilidad implícita del mercado es menor que la probabilidad real histórica (EV+) y registra esas señales, validándolas contra las **fuentes exactas de Polymarket**.
