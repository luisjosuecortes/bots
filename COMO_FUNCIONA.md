# 🤖 Cómo Funciona el Bot — Explicación Detallada

Este documento explica, paso a paso y en lenguaje claro, **qué hace el bot**, **de dónde saca los datos**, **cómo decide que hay una señal** y **cómo esa señal se traduce en ganar (o perder) dinero** en el paper trading.

---

## 1. La Idea Central (en una frase)

> El bot busca momentos en los que el mercado de Polymarket **paga de más** por apostar a que Bitcoin sube, comparado con la probabilidad **real** de que suba, calculada a partir del historial de precios.

Cuando lo que el mercado te paga es mejor que el riesgo real que corres, existe **valor esperado positivo (EV+)**. El bot apuesta solo en esos momentos. A largo plazo, apostar siempre que hay EV+ es lo que genera ganancia.

---

## 2. El Concepto de Reversión a la Media

La estrategia se basa en un patrón estadístico llamado **reversión a la media**:

- Bitcoin abre una ventana de tiempo (5 o 15 minutos) a un precio de apertura (el **"precio a superar"**).
- Si durante la ventana el precio **baja muy poquito** respecto a la apertura, históricamente tiende a **recuperarse y cerrar por encima** de la apertura.
- Es decir: una caída **pequeña** es señal de que probablemente **rebote hacia arriba**.

⚠️ Pero hay un matiz crucial: esto **solo es cierto si la caída es muy pequeña**. Si la caída es grande, ya no rebota — al contrario, suele seguir cayendo. Por eso el bot mide con mucho cuidado **cuánto** ha caído.

---

## 3. Los 2 Bots (timeframes)

El sistema corre **2 bots en paralelo**, uno por cada duración de mercado de BTC que existe en Polymarket con resolución por Chainlink:

| Bot | Ventana | Frecuencia de chequeo |
| :--- | :--- | :--- |
| 5m  | 5 minutos  | cada 30 segundos |
| 15m | 15 minutos | cada 1 minuto |

Ambos funcionan igual; solo cambia la duración de la ventana y cada cuánto miran el precio. El `master_bot.py` los lanza juntos y muestra todo en una sola terminal.

> Nota: existían bots de 1 hora y de 4 horas, pero **se retiraron**. El de 4h porque Polymarket no publica ese mercado; el de 1h para concentrar el sistema en los mercados de 5m/15m que se resuelven con Chainlink (el mismo feed que ahora consumimos en vivo).

---

## 4. Las Fuentes de Datos (todas EXACTAS, las de Polymarket)

El sistema usa **exactamente las mismas fuentes que Polymarket**, para que el paper trading sea fiel a lo que pasaría en real.

### A) Chainlink BTC/USD — el precio en vivo y el "precio a superar"
- Polymarket resuelve los mercados de 5m/15m con el **data stream de Chainlink BTC/USD** (ni Binance ni Coinbase).
- El bot se conecta al **WebSocket oficial de Polymarket**: `wss://ws-live-data.polymarket.com`, tópico `crypto_prices_chainlink`, filtro `{"symbol":"btc/usd"}`. **No requiere autenticación.**
- De ese feed salen, idénticos a lo que muestra la web de Polymarket:
  - **Precio actual** = último tick de Chainlink.
  - **Precio a superar (strike)** = el **primer tick en/ tras la frontera** de la ventana (ej. el primer precio a las 16:00:00 ET para la ventana 16:00–16:05).
  - **Mínimo de la ventana** = el menor tick desde que abrió (datos por segundo).
- El feed corre en una tarea de fondo con **reconexión automática** y un buffer de ~36 minutos.

> **Por qué importa:** Coinbase/Binance se desvían **$13–23** del valor real de Chainlink. En una vela de 5 minutos eso es suficiente para que el "precio a superar" no cuadre y hasta para cambiar quién gana. Por eso ahora se usa Chainlink directo.

### B) Coinbase — SOLO para calibrar el modelo histórico
- Chainlink Data Streams **no ofrece histórico gratuito**, así que para construir la tabla de probabilidades (sección 5) se descargan **30 días de velas de Coinbase BTC/USD** (mismo par USD que Chainlink, coinciden dentro de ~0.03%).
- Esto alimenta **solo el modelo de entrada** (cuándo disparar). **No** decide el precio a superar, ni el precio en vivo, ni el resultado — todo eso es exacto vía Chainlink/Polymarket.

### C) Polymarket — dónde se apuesta y cómo se resuelve
- **API de eventos (Gamma)**: `gamma-api.polymarket.com/events?slug=...` — da la **liquidez**, el **volumen**, los **tokens** (`clobTokenIds`) y, al cerrar, el **resultado real de la liquidación**.
- **API del order book (CLOB)**: `clob.polymarket.com/book?token_id=...` — da **todos los niveles de venta (asks)**. El bot **simula el fill real** de una orden de 100 unidades caminando esos niveles del más barato al más caro y calcula el **precio promedio ponderado (VWAP)**: lo que **de verdad pagarías** con slippage. Es de **solo lectura (gratis)**, no envía órdenes.

### Hora en ET (como Polymarket)
Polymarket nombra y muestra sus mercados en **hora del Este (ET)**. El bot usa la zona `America/New_York`, que aplica automáticamente el cambio EDT(-4)/EST(-5). Por eso la ventana se muestra como `16:00-16:05 ET`, igual que en la web.

---

## 5. Cómo Calibra el Bot (la "inteligencia")

Antes de operar, cada bot descarga **30 días de historial** (Coinbase, par USD) y construye una **tabla de probabilidad condicionada al tiempo**.

### ¿Qué significa "condicionada al tiempo"?

Una versión ingenua diría: *"si la caída total de la ventana fue ≤0.05%, cerró arriba el 71% de las veces"*. **Eso es trampa** (se llama *look-ahead bias*), porque al **principio** de la ventana tú **todavía no sabes** cuánto va a caer en total. Estarías usando información del futuro.

La tabla real responde la pregunta correcta:

> *"Han pasado **E minutos** desde que abrió la ventana, y hasta **ahora** la caída acumulada es **≤X%**. Históricamente, ¿qué porcentaje de veces cerró arriba?"*

### Ejemplo real (bot de 5m, caída ≤0.05%)

| Minutos transcurridos | Probabilidad de cerrar UP |
| :--- | :--- |
| +1 min | 53.7% |
| +2 min | 58.2% |
| +3 min | 62.8% |
| +4 min | 67.4% |

Conclusión: la ventaja **no existe al abrir la ventana**, va **creciendo** conforme avanza el tiempo y el precio se mantiene sin caer. Por eso el bot **no emite señales al inicio** de la ventana.

Cómo se construye la tabla internamente:
1. Agrupa todas las velas de 1m de los últimos 30 días por la ventana a la que pertenecen.
2. Para cada ventana recorre minuto a minuto, llevando el **mínimo acumulado** hasta ese instante.
3. En cada "checkpoint" (minuto 1, 2, 3...) anota: *(minuto, rango de caída) → ¿cerró arriba sí/no?*.
4. Solo guarda los rangos con **al menos 15 muestras** y cuyo **límite inferior de Wilson (95%, 1 cola) supere 50%** — no basta con que la proporción cruda gane: hay que estar 95% seguros de que la ventaja real es mayor que el azar. Ese límite inferior (conservador) es la `prob` que usa el bot.

> **¿Por qué Wilson y no la proporción simple?** Con pocas muestras, `wins/total` es ruidoso. El límite inferior de Wilson penaliza la incertidumbre: con muchas muestras ≈ la proporción real; con pocas, cae por debajo de 0.5 y el bucket se descarta solo.

La tabla se **recalibra automáticamente cada 24 horas** (en un hilo aparte, sin cortar el feed Chainlink).

---

## 6. El Ciclo de Monitoreo (qué hace en cada chequeo)

En cada vuelta (cada 30s–1min), el bot ejecuta estos pasos:

1. **Resuelve señales pendientes**: para cada señal cuya ventana ya cerró, lee el **resultado real de Polymarket** (sección 8) y actualiza el P&L.
2. **Identifica la ventana actual** (ej. 16:00→16:05 ET) y su **precio a superar** (primer tick de Chainlink en la frontera).
3. **Calcula el mínimo real** alcanzado en lo que va de la ventana (con los ticks por segundo del feed).
4. **Mide la caída acumulada**: `(precio_a_superar − mínimo) / precio_a_superar`.
5. **Mide los minutos transcurridos** desde que abrió la ventana.
6. **Consulta la tabla temporal**: dado ese minuto y esa caída, ¿cuál es la probabilidad real de cerrar UP? (`prob`).
7. **Simula el fill ejecutable** de "UP": camina el order book del CLOB para una orden de 100 unidades y obtiene el precio promedio real (`fill` = VWAP).
8. **Aplica los filtros** (sección 7).
9. Si **todo pasa**, emite **una señal** (solo una por ventana), la guarda en el CSV y queda **pendiente de resolución**.

---

## 7. Los Filtros de Seguridad (cuándo SÍ hay señal)

Para que el bot dispare una señal 🔴, deben cumplirse **todas** estas condiciones:

| Filtro | Condición | Por qué |
| :--- | :--- | :--- |
| **Probabilidad con ventaja (Wilson)** | `prob (Wilson LB) > 0.5` en la tabla | Solo hay señal si el límite inferior de Wilson supera el azar: ventaja estadística **robusta**, no ruido. |
| **Convicción mínima** | `prob ≥ MIN_PROB` (0.52) | Piso por encima del corte de Wilson para no operar señales apenas sobre el azar aunque el precio sea barato. Ajustable. |
| **Una sola por ventana** | señal aún no registrada | Evita disparar varias veces en la misma ventana. |
| **Precio mínimo válido** | `fill ≥ $0.01` | Descarta mercados sin datos reales (precio $0.00). |
| **Liquidez mínima** | `liquidez ≥ $5,000` | Garantiza profundidad para entrar/salir sin slippage extremo. |
| **Orden completamente llenada** | se pueden llenar las 100 unidades caminando el libro | Si el order book no tiene profundidad para tu orden completa, no hay señal. |
| **Fill por profundidad (VWAP)** | `precio_entrada = VWAP + 0.01` | El precio de entrada es el **promedio ponderado** de barrer el libro (modela el slippage real), más un colchón de fee. |
| **Valor esperado mínimo** | `EV ≥ 5%` | El margen tiene que cubrir comisiones y el slippage ya modelado, y aún dejar ganancia. |
| **Régimen de mercado** | No operar en *free-fall* real | Solo si BTC viene en caída libre genuina (**<−2%** en el lookback: 5m/60m, 15m/90m) el bot se abstiene. Las caídas moderadas (−0.6…−2%) históricamente **rebotan ~70%**. |
| **Verificación de oráculo** | La descripción del mercado debe mencionar **Chainlink** | Antes de operar, el bot lee la descripción real; si Polymarket cambió la fuente (p. ej. a Pyth), **pausa y alerta** en vez de apostar con un feed que ya no coincide. |
| **Re-validación pre-fill (orden límite)** | Tras detectar la señal, espera `RETARDO_FILL_S` (0.5s), **re-lee el libro** y solo "ejecuta" si el precio fresco sigue dando `EV ≥ 5%` | Simula la latencia decisión→fill (el *espejismo del ask*). Modela una **orden límite** al peor precio aceptable (`prob − margen`): si el libro se movió en tu contra, la orden **no llena** en vez de pagar de más. |

El corazón del filtro es el **EV**:

```
EV = prob − precio_entrada       (precio_entrada = VWAP del fill + fee)
```

- `prob`           = probabilidad real de ganar (de la tabla histórica), ej. 0.63.
- `precio_entrada` = lo que de verdad pagarías por "UP" tras el slippage, ej. 0.52.
- `EV`             = 0.63 − 0.52 = **+0.11 → +11%**.

Si el EV es ≥ 5%, **el mercado te está pagando más de lo que vale el riesgo real**. Esa es la señal de oro.

---

## 8. Cómo se Gana (o Pierde) Dinero — el P&L

### La mecánica de la apuesta

En Polymarket compras una acción "UP" a un precio entre $0 y $1:
- Si **aciertas** (Bitcoin cierra ≥ apertura), tu acción vale **$1.00**.
- Si **fallas** (Bitcoin cierra < apertura), tu acción vale **$0.00**.

### El cálculo de ganancia/pérdida (por unidad)

Si compraste "UP" a un `precio_entrada` (el fill real, VWAP + fee):

| Resultado | Fórmula | Ejemplo (precio = $0.52) |
| :--- | :--- | :--- |
| **GANÓ** 🟢 | `+ (1.00 − precio_entrada)` | +$0.48 |
| **PERDIÓ** 🔴 | `− precio_entrada` | −$0.52 |

Por eso conviene comprar **barato** (precio bajo) con **alta probabilidad** (prob alta): ganas mucho cuando aciertas y arriesgas poco.

### Por qué el EV+ gana a largo plazo

```
Ganancia esperada = prob × (1 − precio_entrada) − (1 − prob) × precio_entrada = prob − precio_entrada = EV
```

Si **siempre** apuestas con EV ≥ 5%, en promedio ganas 5 centavos por cada dólar arriesgado, **aunque pierdas algunas apuestas individuales**.

### Cómo se valida (paper trading, resolución EXACTA)

El bot **no usa dinero real**. La diferencia clave frente a versiones anteriores: el resultado ya **no se infiere comparando velas**, sino que se **lee la liquidación oficial de Polymarket**:

1. Cuando la ventana cierra, Polymarket marca el mercado como `closed` y pone `outcomePrices` en `["1","0"]` (ganó **UP**) o `["0","1"]` (ganó **DOWN**).
2. El bot consulta ese resultado real (reintentando unos segundos, porque la liquidación tarda un poco).
3. Si nuestra compra fue UP y ganó UP → **WIN** 🟢; si no → **LOSS** 🔴.
4. Suma el P&L (`+1−precio_entrada` o `−precio_entrada`) al acumulado, lo registra en el CSV y lo muestra.

> Esto es **100% exacto**: es literalmente lo que Polymarket pagó, sin aproximar el oráculo Chainlink.

---

## 9. Qué Verás en la Terminal

```
⚙️  [ 5m] calibrando 30 días (probabilidad por minuto)…
⚙️  calib 5m: 43201 sub-velas · 8640 ventanas · 4 checkpoints · 4 rangos con ventaja
⚙️  [ 5m] filtros: liquidez≥$5,000 · orden 100u · EV≥5% · fee 0.01 · ventana 5m
⚙️  [ 5m] feed Chainlink conectado · BTC $60,373.50
🕒 [ 5m] 16:00-16:05 ET · precio a superar $60,277.32 · actual $60,373.50
🔴 [ 5m] SEÑAL · +3min · caída 0.03% · prob 63% · fill $0.520 (mejor $0.52 +slip $0.000/1niv +fee 0.01) · EV +10.7% · liq $10,063
🟢 [ 5m] GANÓ UP (2026-06-05 16:00:00 · resolvió UP) · aciertos 1/1 (100%) · P&L +0.47 (acum +0.47)
```

| Icono | Significado |
| :--- | :--- |
| ⚙️ | Calibración / configuración / feed |
| 🕒 | Se abrió una nueva ventana (hora en ET) |
| 🔴 (SEÑAL) | ¡Oportunidad de compra detectada! |
| 🟢 | La señal anterior **ganó** (según la liquidación real) |
| 🔴 (resultado) | La señal anterior **perdió** |

Al detener con `Ctrl+C`, cada bot muestra un resumen:

```
🟢 [ 5m] RESUMEN  12 señales · 9W/3L (75%) · GANANCIA neta +2.40 u
```

---

## 10. El Registro CSV

Cada señal y su resultado se guardan en `senales_5m.csv` y `senales_15m.csv`, con estas columnas:

| Columna | Qué guarda |
| :--- | :--- |
| Fecha/Hora | Momento exacto de la señal (UTC) |
| Ventana | Inicio de la ventana |
| Min transcurrido | Minutos dentro de la ventana al detectar |
| Apertura | Precio a superar (Chainlink, en la frontera) |
| Minimo | Mínimo alcanzado hasta ese momento |
| Caída % | Caída acumulada |
| Prob | Probabilidad real (tabla temporal) |
| Ask UP | Precio ejecutable real pagado (VWAP del fill + fee) |
| Volumen | Volumen del mercado |
| Liquidez | Profundidad del order book |
| EV | Valor esperado de la operación |
| Acción | "COMPRAR UP" |
| Resultado | "WIN" o "LOSS" (se llena al resolver Polymarket) |

Los datos se **acumulan** entre ejecuciones (modo *append*), así que puedes dejar el bot corriendo días y analizar la tasa de acierto real después.

---

## 11. Resumen del Flujo Completo

```
   ┌─────────────────────────────────────────────────────────┐
   │ 1. CALIBRAR: 30 días (Coinbase) → tabla P(UP|minuto,caída)│
   │    + conectar feed Chainlink en vivo (WebSocket)         │
   └─────────────────────────────────────────────────────────┘
                              │
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │ 2. CADA 30s–1min:                                        │
   │    · resolver señales pendientes (liquidación Polymarket)│
   │    · precio a superar + actual (Chainlink en vivo)       │
   │    · caída acumulada + minutos transcurridos             │
   │    · prob = tabla temporal                               │
   │    · fill = VWAP caminando el order book CLOB            │
   └─────────────────────────────────────────────────────────┘
                              │
                              ▼
        ¿prob≥0.52  Y  liquidez≥$5k  Y  orden llena  Y  EV≥5%?
                       │  sí                 │  no
                       ▼                     ▼
            🔴 SEÑAL: COMPRAR UP        seguir monitoreando
                       │
                       ▼
   ┌─────────────────────────────────────────────────────────┐
   │ 3. AL RESOLVER POLYMARKET: outcomePrices = ["1","0"]?    │
   │    ganó UP → WIN (+1−precio)     ganó DOWN → LOSS (−precio)│
   │    actualizar P&L y guardar resultado en CSV            │
   └─────────────────────────────────────────────────────────┘
```

---

## 12. Conceptos Clave en una Tabla

| Término | Significado simple |
| :--- | :--- |
| **Reversión a la media** | Una caída pequeña suele rebotar hacia arriba. |
| **Precio a superar (strike)** | El precio de Chainlink al abrir la ventana; UP gana si el cierre lo iguala o supera. |
| **Probabilidad (prob)** | Qué tan seguido, en el historial, cerró arriba en esa misma situación. |
| **Fill / VWAP** | Precio promedio real que pagas al barrer el order book con tu orden (incluye slippage). |
| **EV (valor esperado)** | `prob − precio_entrada`. Si es positivo, el mercado te paga de más → conviene apostar. |
| **Liquidez** | Dinero disponible en el mercado; evita que tu orden mueva mucho el precio. |
| **Look-ahead bias** | El error de usar información del futuro; el bot lo evita con la tabla temporal. |
| **Paper trading** | Simulación sin dinero real para validar la estrategia primero. |

---

> **Filosofía del sistema:** no se trata de adivinar el futuro, sino de apostar **únicamente** cuando las matemáticas están a tu favor (EV positivo) y dejar que la **ley de los grandes números** convierta esa ventaja en ganancia con el tiempo. Y todo se mide contra las **fuentes exactas de Polymarket** (Chainlink + liquidación real), para que el paper trading sea fiel a lo que pasaría operando en serio.
