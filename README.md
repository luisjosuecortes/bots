# 📊 Sistema de Paper Trading Cuantitativo - Polymarket BTC

## Resumen del Proyecto

Sistema automatizado de **Paper Trading** (simulación sin riesgo) para mercados de predicción de Bitcoin en Polymarket. El sistema utiliza análisis estadístico de datos históricos de Binance para calcular probabilidades reales de reversión a la media, y las compara con los precios de las acciones en Polymarket para detectar oportunidades de **Valor Esperado (EV) positivo**.

**Estado actual**: Sistema completo, probado y en fase de validación estadística.

---

## Arquitectura del Sistema

### Estructura de Archivos

```
/home/penguin/Documentos/poly/
├── analisis_historico_modulo.py          # Módulo compartido: análisis temporal de Binance
├── cli_util.py                           # Salida CLI con iconos, P&L y CLOB de Polymarket
├── master_bot.py                         # Orquestador que lanza los 3 bots simultáneamente
├── README.md                             # Este archivo de contexto
│
├── 1_hora/
│   ├── paper_trading_bot.py              # Bot de 1 hora (auto-calibrable)
│   ├── analisis_historico.py             # Script de análisis independiente
│   └── senales_1h.csv                    # Datos acumulados de señales
│
├── 15_minutos/
│   ├── paper_trading_bot_15m.py          # Bot de 15 minutos (auto-calibrable)
│   └── senales_15m.csv                  # Datos acumulados de señales
│
└── 5_minutos/
    ├── paper_trading_bot_5m.py           # Bot de 5 minutos (auto-calibrable)
    ├── analisis_historico_5m.py          # Script de análisis independiente
    └── senales_5m.csv                   # Datos acumulados de señales
```

### Flujo de Ejecución

1. **Inicio**: Se ejecuta `master_bot.py` o un bot individual.
2. **Auto-calibración**: El bot descarga 30 días de datos de Binance para su intervalo específico.
3. **Cálculo de parámetros**: Analiza los datos históricos y construye una **tabla de probabilidad condicionada al tiempo** (P de cierre UP según los minutos transcurridos y la caída acumulada hasta ese momento).
4. **Monitoreo continuo**: Cada ciclo (30s-1min según el timeframe), el bot:
   - Lee el precio actual de Binance.
   - Calcula la caída acumulada desde la apertura de la ventana y los minutos transcurridos.
   - Consulta la tabla temporal para obtener la probabilidad real condicionada al tiempo.
   - Consulta el **precio ejecutable real** simulando el fill de una orden de `TAMANO_ORDEN` (100 unidades) caminando la profundidad del order book CLOB de "UP" (VWAP), lo que modela el slippage real.
   - Aplica filtros de liquidez, llenado completo de la orden y EV.
   - Si hay señal válida (una sola por ventana), la registra en el CSV.
5. **Re-calibración**: Cada 24 horas, el bot actualiza automáticamente sus parámetros.

---

## Parámetros del Sistema

### Probabilidad Condicionada al Tiempo (corrige el sesgo de look-ahead)

El sistema **ya no** usa un único umbral de caída por timeframe. Eso medía la caída *máxima de toda la ventana* y aplicarlo en vivo era sesgo de look-ahead (al abrir la ventana la caída es ~0 pero aún no sabes cuánto caerá). Ahora se construye una tabla **P(UP | minutos transcurridos, caída acumulada)**.

Ejemplo real para 5m (caída acumulada ≤ 0.05%), que muestra por qué el viejo 71% estaba inflado:

| Minutos transcurridos | Probabilidad UP |
| :--- | :--- |
| +1 min | **53.7%** |
| +2 min | 58.2% |
| +3 min | 62.8% |
| +4 min | 67.4% |

La ventaja real solo aparece avanzada la ventana, no al abrir. El bot no emite señal antes del primer checkpoint.

### Filtros de Seguridad (Aplicados a todos los bots)

| Filtro | Valor | Justificación |
| :--- | :--- | :--- |
| **Liquidez mínima** | ≥ $5,000 USD | Profundidad del order book (`liquidityNum` de Polymarket). Métrica correcta para evitar slippage en mercados de corta duración, donde el "volumen 24h" es estructuralmente diminuto porque el mercado se recrea cada ventana. |
| **Llenado completo de la orden** | Se debe poder llenar `TAMANO_ORDEN` (100 unidades) | Se simula barrer el order book del CLOB de menor a mayor precio. Si no hay profundidad para llenar las 100 unidades, no hay señal: evita operar donde solo puedes comprar una cantidad ínfima al precio mostrado. |
| **Precio de entrada** | **VWAP del fill simulado** + colchón de fee | Se camina el order book real acumulando tamaño hasta llenar la orden y se calcula el **precio promedio ponderado (VWAP)** — esto modela el slippage real (una orden grande "come" varios niveles). Se suma un pequeño colchón `FEE` (0.01) por comisiones. Es **solo lectura / gratis**: no envía órdenes. |
| **Ventaja estadística (Wilson)** | Solo buckets con **límite inferior de Wilson (1 cola, 95%) > 0.5** | No basta con que la proporción cruda `wins/total` supere 50%: se exige que el **límite inferior** del intervalo de confianza lo supere. Esto poda automáticamente las ventajas que son ruido por pocas muestras. Validado out-of-sample: subió el winrate de 15m 60.3%→61.8% y de 1h 71.0%→75.2% al eliminar exactamente los buckets que no replican. |
| **Convicción mínima** | `prob (Wilson LB) ≥ MIN_PROB` (0.52) | Piso de convicción por encima del corte de Wilson. Evita operar señales apenas sobre el azar aunque el precio sea barato. Es un knob: subirlo opera menos pero con más convicción. |
| **EV Neto mínimo** | ≥ 5% | Se calcula como `prob − precio_entrada`, donde `prob` es el **Wilson LB conservador** y `precio_entrada` es el VWAP del fill **con** colchón de fee. Así el EV nunca sobreestima ni la ventaja ni el precio. |
| **Filtro de régimen** | Sin operar solo en *free-fall* real | La reversión de micro-caídas falla en una caída sostenida y agresiva. Umbral **−2%** (5m/60m · 15m/90m · 1h/360m). Se relajó desde −0.6%/−0.8% tras comprobar empíricamente que las caídas moderadas (−0.6…−2%) históricamente rebotan ~70%; el filtro solo pausa en *free-fall* genuino como seguro de cola ante un cambio de régimen fuera de muestra. |
| **Verificación de oráculo** | La descripción del mercado debe mencionar el oráculo esperado (5m/15m → Chainlink · 1h → Binance) | Polymarket puede cambiar la fuente de resolución al crear nuevos contratos. En cada intento de señal se lee la descripción real del mercado; si el oráculo esperado no aparece (o aparece otro como Pyth), el bot **pausa y alerta** en vez de apostar a ciegas con una calibración que ya no coincide. |
| **Re-validación pre-fill (orden límite)** | Tras detectar la señal, espera `RETARDO_FILL_S` (0.5s), re-lee el order book y solo "ejecuta" si el precio fresco mantiene `EV ≥ 5%` | Modela la latencia decisión→fill (el *espejismo del ask*): el ask que viste puede desaparecer antes de que tu orden llegue al CLOB. Simula una **orden límite** al peor precio aceptable (`prob − margen`): si el libro se movió en tu contra, la orden **no llena** (oportunidad perdida) en vez de pagar de más. El precio registrado es el del fill fresco, nunca uno mejor del que conseguirías en real. |

### Filtros Sniper (Evitar operaciones a último segundo)

| Timeframe | Ventana de pausa | Justificación |
| :--- | :--- | :--- |
| 1 Hora | Últimos 5 minutos | Evita ruido de cierre |
| 15 Minutos | Últimos 2 minutos | Evita ruido de cierre |
| 5 Minutos | Último minuto | Evita ruido de cierre |

### Frecuencias de Monitoreo y Refresh

| Timeframe | Frecuencia de chequeo | Refresh de parámetros |
| :--- | :--- | :--- |
| 1 Hora | Cada 1 minuto | Cada 24 horas |
| 15 Minutos | Cada 1 minuto | Cada 24 horas |
| 5 Minutos | Cada 30 segundos | Cada 24 horas |

---

## Validación de Resultados en Tiempo Real

### Cómo funciona la validación

Cada bot ahora valida automáticamente el resultado de las señales anteriores:

1. **Al inicio de cada nueva ventana**, el bot verifica el resultado de la ventana anterior.
2. **Si hubo una señal**, compara el precio de cierre (el precio de Binance al momento exacto de cierre) con el precio de apertura.
3. **Si el cierre ≥ apertura**, la señal fue **GANADORA** ✅.
4. **Si el cierre < apertura**, la señal fue **PERDEDORA** ❌.
5. **Actualiza los contadores** de wins/losses y calcula la tasa de acierto acumulada.
6. **Imprime el resultado** en la terminal con la tasa de acierto actualizada.
7. **Registra el resultado** en el CSV en la columna "Resultado".

### Estructura del CSV (Actualizada)

| Columna | Descripción |
| :--- | :--- |
| Fecha/Hora | Marca de tiempo exacta de la detección de la señal |
| Ventana | Inicio de la ventana de tiempo (ej: "2026-06-03 22:30:00") |
| Min transcurrido | Minutos transcurridos dentro de la ventana al detectar la señal |
| Apertura | Precio de BTC al inicio de la ventana |
| Minimo | Precio mínimo alcanzado hasta el momento de la señal |
| Caída % | Caída acumulada desde la apertura hasta ese momento |
| Prob | Probabilidad UP condicionada al tiempo (tabla temporal) |
| Precio entrada | Precio ejecutable real = **VWAP del fill simulado** (order book CLOB) + fee |
| Volumen | Volumen del mercado en Polymarket (`mercado.volumeNum`) |
| Liquidez | Profundidad del order book (`mercado.liquidityNum`) — sobre esta se aplica el filtro |
| EV | Valor Esperado neto (Prob − precio entrada) |
| Acción | "COMPRAR UP" (señal de compra) |
| **Resultado** | **"WIN" o "LOSS"** (se llena al cierre de la ventana) |

### Ejemplo de salida en terminal (CLI con iconos)

```
⚙️  [ 5m] calibrando 30 días (probabilidad por minuto)…
⚙️  calib 5m: 43201 sub-velas · 8640 ventanas · 4 checkpoints · 4 rangos con ventaja
⚙️  [ 5m] filtros: liquidez≥$5,000 · orden 100u · EV≥5% · fee 0.01 · ventana 5m
🕒 [ 5m] 17:35→17:40 · apertura $63,084.00 · cierre ant $63,001
🔴 [ 5m] SEÑAL · +3min · caída 0.03% · prob 63% · fill $0.520 (mejor $0.52 +slip $0.000/1niv +fee 0.01) · EV +10.7% · liq $10,063
🟢 [ 5m] GANÓ  $63,084→$63,120 · aciertos 1/1 (100%) · P&L +0.47 (acum +0.47)
```

- 🔴 señal (compra) · 🟢 ganó / 🔴 perdió al cerrar la ventana.
- Solo se emite **una señal por ventana**.

### Resumen final al detener el bot

Al presionar `Ctrl + C`, cada bot muestra un resumen con P&L neto (🟢 ganancia / 🔴 pérdida):

```
🟢 [ 5m] RESUMEN  12 señales · 9W/3L (75%) · GANANCIA neta +2.40 u
```

El P&L se calcula en "unidades": comprar UP al precio de entrada paga 1.0 si gana (beneficio `1 − precio`) y pierde el precio pagado si pierde.

---

## Fuentes de Datos

Cada bot usa la **misma fuente de precio que la resolución de su mercado** (esto es clave para fidelidad):

| Timeframe | Fuente de resolución de Polymarket | Fuente de precio del bot | ¿Coinciden? |
| :--- | :--- | :--- | :--- |
| **1 Hora** | Binance BTC/**USDT**, vela 1H (`close ≥ open`) | Binance BTC/USDT | ✅ Exacto (misma) |
| **5 Minutos** | **Chainlink BTC/USD** data stream | **Coinbase BTC/USD** | ✅ Mismo par USD (≈0.03%) |
| **15 Minutos** | **Chainlink BTC/USD** data stream | **Coinbase BTC/USD** | ✅ Mismo par USD (≈0.03%) |

### ¿Por qué Coinbase para 5m/15m?
- La resolución de 5m/15m es **Chainlink BTC/USD**, que es un **agregado de mercados spot USD** (Coinbase, Kraken, Bitstamp). Esas fuentes USD coinciden entre sí dentro de **~0.03%**.
- Binance BTC/**USDT** está sesgado **+0.15%** respecto al par USD (medido en vivo: +0.159%). Como ese sesgo es **mayor** que el umbral de caída de 5m (0.05%), usar Binance para 5m/15m introducía un error estructural que invertía señales.
- **Chainlink Data Streams** no es de acceso libre (requiere credenciales con firma HMAC). **Coinbase BTC/USD** es la mejor aproximación gratuita: mismo par (USD), prácticamente sobre la mediana de las fuentes USD, y ofrece histórico de velas para calibrar.

### Endpoints
- **Binance** (bot 1h): `api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={i}` y `.../ticker/price`.
- **Coinbase** (bots 5m/15m): `api.exchange.coinbase.com/products/BTC-USD/candles?granularity={s}` y `.../ticker`.
- Sub-velas para la probabilidad por minuto: 1m (5m y 15m) y 5m (1h).

### Polymarket (Precios de acciones UP/DOWN)
- **API de eventos (gamma)**: `https://gamma-api.polymarket.com/events?slug={slug}` — da liquidez, volumen y `clobTokenIds`.
- **API del order book (CLOB)**: `https://clob.polymarket.com/book?token_id={tokenId}` — da todos los niveles de `asks`. Se simula el **fill real** caminando esos niveles (VWAP) para una orden de 100 unidades. **Solo lectura, gratis**: no se envían órdenes ni se necesita wallet.
- **Slugs de mercados**:
  - 1 Hora: `bitcoin-up-or-down-{mes}-{dia}-{año}-{hora}am/pm-et`
  - 5 Minutos: `btc-updown-5m-{timestamp_unix}`
  - 15 Minutos: `btc-updown-15m-{timestamp_unix}`

### Hora exacta de cierre
- Los mercados resuelven **en la frontera exacta** de la ventana (ej. 5m: …:00, :05, :10; 1h: en punto), confirmado por los campos `startTime`/`endDate` del evento. **No cierran antes de tiempo.** Lo que puede detenerse un poco antes es la *aceptación de órdenes nuevas*, no el precio de resolución.
- **Cierre determinista (corrige el bug de sincronización)**: el precio de cierre ya **no** se toma del *ticker* en vivo (que daba valores distintos en cada bot según el segundo de la consulta), sino de la **apertura de la vela de 1m en la frontera** (`precio_frontera`), en la fuente correspondiente. Así, todos los bots leen exactamente el mismo precio para la misma frontera. Verificado: a las 19:30 los bots de 15m y 5m leen idéntico $64,026.28 (Coinbase).

---

## Descubrimientos Clave del Análisis

### 1. Patrón de Reversión a la Media (condicionado al tiempo)
Existe un patrón de reversión, pero su fuerza **depende del momento de la ventana**. Una caída acumulada pequeña al inicio aporta poca ventaja; la ventaja crece a medida que avanza la ventana (ver tabla temporal de 5m: 53.7% a +1min → 67.4% a +4min).

### 2. El Look-ahead Inflaba las Probabilidades
El enfoque anterior (umbral sobre la caída máxima de toda la ventana) reportaba 71–87%, pero esos números no son alcanzables en vivo porque al abrir la ventana no se conoce la caída futura. La tabla temporal corrige esto.

### 3. El Precio Ejecutable Importa (fill simulado por profundidad)
`outcomePrices` es un punto medio; lo que pagas de verdad es peor. En vez de usar solo el mejor ask, se **simula el fill** de una orden de 100 unidades caminando el order book del CLOB nivel a nivel y se calcula el **precio promedio ponderado (VWAP)**. Esto modela el **slippage real**: una orden grande "come" varios niveles y paga más caro. Si el libro no tiene profundidad para llenar la orden, no hay señal. Es de solo lectura (gratis); cuando se pase a real, el mismo cálculo predice el fill efectivo.

### 4. 5 Minutos Genera Más Datos
Con 8,640 ventanas en 30 días, el bot de 5 minutos es el que más rápido acumula datos para validación.

### 5. La Ventaja Sobrevive Fuera de Muestra (validación out-of-sample)
Calibrando con los primeros ~20 días y midiendo en los últimos ~10 días que el modelo **nunca vio**, el winrate se mantiene: **5m 61.9% · 15m 61.8% · 1h 75.2%** sobre miles de ventanas no vistas. Bucket por bucket coincide (ej. 5m +4min: train 68.5% → test 71.6%). Esto descarta que la ventaja sea *data snooping*. Además, el límite inferior de **Wilson** mejora el winsrate fuera de muestra precisamente porque elimina los buckets marginales que no replican (15m 60.3%→61.8%, 1h 71.0%→75.2%).

> Nota sobre número de muestras: los buckets que **sí se operan** tienen entre ~170 y ~6,900 muestras (no 15). Los de pocas muestras son las caídas profundas (0.3–1%), que ya quedan excluidas porque su probabilidad < 0.5. El Wilson LB es además **auto-regulante**: si un bucket tiene pocas muestras, su límite inferior cae por debajo de 0.5 y se excluye solo — esto es más robusto que fijar un número de días arbitrario.

### 6. El Régimen Afecta de Forma Asimétrica (y NO como se esperaría)
Midiendo el winrate del bucket operado de 5m según el **régimen previo (60 min)**:

| Régimen previo (tendencia 60m) | n | Winrate |
| :--- | ---: | ---: |
| Rally fuerte (>+1.2%) | 112 | **0.768** |
| Sube (+0.6…+1.2%) | 550 | 0.718 |
| Plano (−0.6…+0.6%) | ~21k | ~0.60 |
| Baja (−1.2…−0.6%) | 621 | **0.702** |
| Baja fuerte (−2…−1.2%) | 77 | 0.688 |
| Crash (<−2%) | 5 | (muestras insuficientes) |

Por **volatilidad realizada previa**: vol baja 0.565 → media 0.626 → alta 0.699 → extrema 0.739 (monótono creciente).

**Conclusiones (contraintuitivas, respaldadas por datos):**
- La relación es en **U**: los movimientos fuertes en **cualquier** dirección dan más ventaja de reversión (mercado sobre-extendido que rebota). El centro plano es donde menos ventaja hay.
- **Más volatilidad = MÁS edge**, no menos (refuta la hipótesis de que alta vol sea contexto peligroso).
- El **rally alcista fuerte es el MEJOR régimen** (0.768). Por eso **no** conviene un filtro simétrico que pause en subidas: cortaría las mejores operaciones (la estrategia apuesta UP; una subida es viento a favor).
- El filtro de caída se **relajó de −0.6% a −2%** a raíz de estos datos: como las caídas −0.6…−2% rinden ~0.70, pausar ahí era contraproducente. Ahora el filtro solo se activa en *free-fall* genuino (<−2%), funcionando como seguro de cola ante un bear sostenido que no está en la muestra (n=5 ahí, sin evidencia a favor ni en contra).

---

## Problemas Conocidos y Soluciones

### Bug de Volumen Equivocado → Liquidez (Corregido)
- **Problema**: `obtener_mercado` leía `event.volume24hr` (≈$10–20, casi vacío) que al ser truthy ocultaba el volumen real del mercado. Además el "volumen 24h" no tiene sentido en mercados de 5/15 min que se recrean cada ventana. Resultado: el filtro de $5,000 rechazaba casi todas las señales de 5m y 15m.
- **Solución**: Se lee a nivel de **mercado** (`volumeNum`/`liquidityNum`) y el filtro ahora usa **liquidez** (profundidad del libro ≈ $9k–15k), que es la métrica correcta para slippage. Verificado contra la API: 5m liq≈$10k, 15m liq≈$13k, 1h liq≈$9k (todos pasan).
- **Estado**: Corregido en los 4 bots (vía `cli_util.parse_mercado`).

### Bug de Punto Flotante (Corregido)
- **Problema**: Los cálculos de caída pueden tener errores de precisión de punto flotante (ej: 0.00050000000001 en lugar de 0.0005).
- **Solución**: Se aplica `round(caida, 6)` antes de comparar con los límites del mapa de probabilidades.
- **Estado**: Corregido en los 4 bots.

### Mercado de 30 Minutos No Existe
- **Problema**: Polymarket no ofrece mercados de 30 minutos para BTC.
- **Solución**: Se eliminó el bot de 30 minutos. El sistema se quedó con 5m, 15m y 1h.

### Bug de Precio $0.00 de Polymarket (Corregido)
- **Problema**: Cuando Polymarket no devuelve datos reales para un mercado, el precio de la acción "UP" puede ser $0.00. El bot calculaba EV = Probabilidad - $0.00 = Probabilidad, generando falsas señales.
- **Solución**: Se agregó un filtro que rechaza señales si el precio de Polymarket es menor a $0.01.
- **Estado**: Corregido en los 3 bots.

### Mercado de 4 Horas - No Existe en Polymarket
- **Problema**: Polymarket **no publica** mercados BTC up/down de 4 horas (el slug devuelve 0 eventos).
- **Decisión**: Se **retiró** el bot de 4h del sistema. Solo quedan 5m, 15m y 1h, que sí tienen mercado real.
- **Estado**: Resuelto (bot eliminado).

### Bots con Mercado No Encontrado puntualmente
- **Problema**: Algunos timeframes pueden no tener mercado activo en Polymarket en un instante dado.
- **Solución**: El bot reporta "mercado no encontrado" y continúa monitoreando.
- **Estado**: Comportamiento esperado. No afecta la validación de la estrategia.

---

## Cómo Ejecutar el Sistema

### Opción 1: Bot Maestro (Recomendado)
```bash
python3 -u /home/penguin/Documentos/poly/master_bot.py
```
Lanza los 3 bots simultáneamente y muestra su salida en tiempo real en una sola terminal.

### Opción 2: Bots Individuales
```bash
# En terminales separadas:
python3 -u /home/penguin/Documentos/poly/1_hora/paper_trading_bot.py
python3 -u /home/penguin/Documentos/poly/15_minutos/paper_trading_bot_15m.py
python3 -u /home/penguin/Documentos/poly/5_minutos/paper_trading_bot_5m.py
```

### Para detener
Presionar `Ctrl + C` en la terminal del maestro (o en cada terminal individual).

### Para revisar datos acumulados
```bash
cat /home/penguin/Documentos/poly/1_hora/senales_1h.csv
cat /home/penguin/Documentos/poly/15_minutos/senales_15m.csv
cat /home/penguin/Documentos/poly/5_minutos/senales_5m.csv
```

---

## Tiempos de Validación Recomendados

| Timeframe | Tiempo mínimo | Señales esperadas |
| :--- | :--- | :--- |
| 5 Minutos | 6-8 horas | ~40-56 señales |
| 15 Minutos | 12-16 horas | ~20-30 señales |
| 1 Hora | 2-3 días | ~15-20 señales |

---

## Próximos Pasos

1. **Ejecutar validación**: Correr el sistema durante los tiempos recomendados.
2. **Analizar resultados**: Revisar los CSVs para calcular la tasa de acierto real.
3. **Comparar con histórica**: Si la tasa real se acerca a la probabilidad temporal estimada, la estrategia está validada.
4. **Ajustar parámetros**: Si la tasa es significativamente menor, ajustar los umbrales de caída o el margen de EV.
5. **Posible implementación con capital real**: Solo después de validación exitosa.

---

## Notas Técnicas

### Dependencias
- Python 3.x
- aiohttp (para requests asíncronos)
- No requiere dependencias adicionales (usa solo librerías estándar + aiohttp)

### APIs Utilizadas
- Binance API pública (sin autenticación requerida)
- Polymarket Gamma API pública (sin autenticación requerida)

### Persistencia de Datos
- Los CSVs se abren en modo `append` ('a'), por lo que los datos se acumulan entre ejecuciones.
- Los encabezados solo se escriben si el archivo no existe.
- Ejecutar el bot múltiples veces NO sobrescribe los datos anteriores.

### Auto-calibración
- Cada bot descarga 30 días de datos de Binance al iniciar.
- Los parámetros se recalculan automáticamente cada 24 horas.
- No hay parámetros estáticos ni hardcodeados (excepto los filtros de seguridad).

---

## Historial de Desarrollo

- **2026-06-03**: Creación del sistema completo.
  - Análisis histórico de Binance (30 días) para 5m, 15m, 1h.
  - Creación de bots de 5m, 15m, 1h con auto-calibración.
  - Creación del bot maestro.
  - Corrección del bug de punto flotante.
  - Eliminación del bot de 30 minutos (no existe en Polymarket).
  - Pruebas exitosas de todos los componentes.
- **Actualización**: correcciones de robustez y realismo.
  - Corrección del bug de volumen → filtro por **liquidez**.
  - Salida CLI en español con iconos y P&L (🔴 señal · 🟢/🔴 resultado).
  - Probabilidad **condicionada al tiempo** (corrige el look-ahead bias).
  - Precio de entrada = **fill simulado por profundidad del CLOB (VWAP)**, no el punto medio ni solo el mejor ask. Modela el slippage real; solo lectura.
  - Retiro del bot de 4 horas (sin mercado en Polymarket).

---

## Contacto y Contexto

Este sistema fue desarrollado como un proyecto de trading cuantitativo para mercados de predicción. La estrategia se basa en la **reversión a la media** (mean reversion), una de las estrategias estadísticamente más sólidas en mercados financieros de corto plazo.

**Principio fundamental**: El mercado de Polymarket a veces sobre-reacciona a pequeñas fluctuaciones de precio, creando oportunidades donde la probabilidad implícita del mercado es menor que la probabilidad real calculada históricamente. El bot detecta estas discrepancias y las registra como señales de compra de alto valor esperado.
