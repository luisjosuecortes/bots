# 🤖 Cómo Funciona el Bot — Explicación Detallada

Este documento explica, paso a paso y en lenguaje claro, **qué hace el bot**, **de dónde saca los datos**, **cómo decide que hay una señal** y **cómo esa señal se traduce en ganar (o perder) dinero**.

---

## 1. La Idea Central (en una frase)

> El bot busca momentos en los que el mercado de Polymarket **paga de más** por apostar a que Bitcoin sube, comparado con la probabilidad **real** de que suba, calculada a partir del historial de precios de Binance.

Cuando lo que el mercado te paga es mejor que el riesgo real que corres, existe **valor esperado positivo (EV+)**. El bot apuesta solo en esos momentos. A largo plazo, apostar siempre que hay EV+ es lo que genera ganancia.

---

## 2. El Concepto de Reversión a la Media

La estrategia se basa en un patrón estadístico llamado **reversión a la media**:

- Bitcoin abre una ventana de tiempo (5, 15 o 60 minutos) a un precio de apertura.
- Si durante la ventana el precio **baja muy poquito** respecto a la apertura, históricamente tiende a **recuperarse y cerrar por encima** de la apertura.
- Es decir: una caída **pequeña** es señal de que probablemente **rebote hacia arriba**.

⚠️ Pero hay un matiz crucial: esto **solo es cierto si la caída es muy pequeña**. Si la caída es grande, ya no rebota — al contrario, suele seguir cayendo. Por eso el bot mide con mucho cuidado **cuánto** ha caído.

---

## 3. Los 3 Bots (timeframes)

El sistema corre **3 bots en paralelo**, uno por cada duración de mercado que existe en Polymarket:

| Bot | Ventana | Frecuencia de chequeo |
| :--- | :--- | :--- |
| 5m  | 5 minutos  | cada 30 segundos |
| 15m | 15 minutos | cada 1 minuto |
| 1h  | 1 hora     | cada 1 minuto |

Todos funcionan igual; solo cambia la duración de la ventana y cada cuánto miran el precio. El `master_bot.py` los lanza juntos y muestra todo en una sola terminal.

> Nota: existía un bot de 4 horas, pero **se retiró** porque Polymarket no publica ese mercado (no se puede operar ahí).

---

## 4. Las 4 Fuentes de Datos

El bot combina dos plataformas:

### A) Binance — el precio real de Bitcoin
- **Precio en vivo**: `api.binance.com/.../ticker/price?symbol=BTCUSDT`
- **Historial (velas)**: `api.binance.com/.../klines` — descarga 30 días de velas de 1 minuto (o 5 minutos para el bot de 1h) para calibrar las probabilidades.
- Binance es además la **fuente oficial de resolución** de los mercados de Polymarket: quien gana la apuesta se decide según el precio de Binance.

### B) Coinbase / Binance — el precio real de Bitcoin según el par correcto
Cada bot usa **la misma fuente que usa Polymarket para resolver su mercado**:
- **Bot de 1h** → **Binance BTC/USDT** (su mercado se resuelve con la vela 1H de Binance).
- **Bots de 5m y 15m** → **Coinbase BTC/USD** (su mercado se resuelve con Chainlink BTC/**USD**, un par USD).

De aquí sale: el precio en vivo, el histórico de 30 días para calibrar, el mínimo de la ventana y el precio de cierre exacto en la frontera.

### C) Polymarket — dónde se apuesta
- **API de eventos (Gamma)**: `gamma-api.polymarket.com/events?slug=...` — da la **liquidez**, el **volumen** y los identificadores de las acciones (tokens).
- **API del order book (CLOB)**: `clob.polymarket.com/book?token_id=...` — da **todos los niveles de venta (asks)**. El bot **simula el fill real** de una orden de 100 unidades caminando esos niveles del más barato al más caro, calculando el **precio promedio ponderado (VWAP)**: lo que **de verdad pagarías** considerando el slippage. Es de **solo lectura (gratis)**, no envía órdenes.

### Por qué importa el par (USD vs USDT) — corregido
La resolución de 5m/15m es **Chainlink BTC/USD** (par USD). Binance BTC/**USDT** está sesgado **+0.15%** respecto al par USD, y ese sesgo es **mayor** que el umbral de caída de 5m (0.05%), así que usar Binance ahí daba predicciones equivocadas. Ahora 5m/15m usan **Coinbase BTC/USD** (mismo par USD que la resolución; las fuentes USD coinciden dentro de ~0.03%). Chainlink Data Streams no es de acceso libre, y Coinbase es la mejor aproximación gratuita.

### Hora de cierre y precio de cierre exacto
- Los mercados resuelven **en la frontera exacta** de la ventana (no cierran antes).
- El precio de cierre se toma de la **vela en la frontera** (determinista) de la fuente correcta, no del precio en vivo. Así todos los bots usan exactamente el mismo número y coincide con lo que usa Polymarket.

---

## 5. Cómo Calibra el Bot (la "inteligencia")

Antes de operar, cada bot descarga **30 días de historial** de Binance y construye una **tabla de probabilidad condicionada al tiempo**.

### ¿Qué significa "condicionada al tiempo"?

Esta es la parte más importante y la que hace que el bot sea honesto consigo mismo.

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

> **¿Por qué Wilson y no la proporción simple?** Con pocas muestras, `wins/total` es ruidoso (15 aciertos de 25 podría ser "suerte"). El límite inferior de Wilson penaliza la incertidumbre: con muchas muestras ≈ la proporción real; con pocas, cae por debajo de 0.5 y el bucket se descarta solo. Verificado fuera de muestra: este filtro **sube** el acierto real (15m 60.3%→61.8%, 1h 71.0%→75.2%) porque elimina justo los rangos que no se repiten.

La tabla se **recalibra automáticamente cada 24 horas**.

---

## 6. El Ciclo de Monitoreo (qué hace en cada chequeo)

En cada vuelta (cada 30s–1min), el bot ejecuta estos pasos:

1. **Identifica la ventana actual** (ej. 17:45→17:50) y su precio de apertura real, tomado de Binance.
2. **Calcula el mínimo real** alcanzado en lo que va de la ventana (usando las velas de 1m, no solo el precio actual). → *Esto evita falsas caídas de 0.00% si el bot arranca a mitad de ventana.*
3. **Mide la caída acumulada**: `(apertura − mínimo) / apertura`.
4. **Mide los minutos transcurridos** desde que abrió la ventana.
5. **Consulta la tabla temporal**: dado ese minuto y esa caída, ¿cuál es la probabilidad real de cerrar UP? (`prob`).
6. **Simula el fill ejecutable** de "UP": camina el order book del CLOB para una orden de 100 unidades y obtiene el precio promedio real (`fill` = VWAP).
7. **Aplica los filtros** (sección 7).
8. Si **todo pasa**, emite **una señal** (solo una por ventana) y la guarda en el CSV.

---

## 7. Los Filtros de Seguridad (cuándo SÍ hay señal)

Para que el bot dispare una señal 🔴, deben cumplirse **todas** estas condiciones:

| Filtro | Condición | Por qué |
| :--- | :--- | :--- |
| **Probabilidad con ventaja (Wilson)** | `prob (Wilson LB) > 0.5` en la tabla | Solo hay señal si el límite inferior de Wilson supera el azar: ventaja estadística **robusta**, no ruido. |
| **Convicción mínima** | `prob ≥ MIN_PROB` (0.52) | Piso por encima del corte de Wilson para no operar señales apenas sobre el azar aunque el precio sea barato. Es ajustable: subirlo = menos señales, más convicción. |
| **Una sola por ventana** | señal aún no registrada | Evita disparar 10 veces en la misma ventana. |
| **Precio mínimo válido** | `fill ≥ $0.01` | Descarta mercados sin datos reales (precio $0.00). |
| **Liquidez mínima** | `liquidez ≥ $5,000` | Garantiza que el mercado tiene profundidad para entrar/salir sin slippage extremo. |
| **Orden completamente llenada** | se puede llenar las 100 unidades caminando el libro | Si el order book no tiene profundidad para tu orden completa, no hay señal: evita operar donde solo cabe una migaja al precio mostrado. |
| **Fill por profundidad (VWAP)** | `precio_entrada = VWAP + 0.01` | El precio de entrada es el **promedio ponderado** de barrer el libro (modela el slippage real: una orden grande come varios niveles), más un pequeño colchón de fee. |
| **Valor esperado mínimo** | `EV ≥ 5%` | El margen tiene que cubrir comisiones y el slippage ya modelado, y aún dejar ganancia. |
| **Régimen de mercado** | No operar en *free-fall* real | Solo si BTC viene en caída libre genuina (**<−2%** en el lookback: 5m/60m, 15m/90m, 1h/360m) el bot se abstiene. Las caídas moderadas (−0.6…−2%) históricamente **rebotan ~70%**, así que no se filtran; el umbral −2% es un seguro de cola ante un bear sostenido fuera de muestra. |
| **Verificación de oráculo** | La descripción del mercado debe mencionar el oráculo esperado | Antes de operar, el bot lee la descripción real del mercado y comprueba la fuente de resolución (5m/15m→Chainlink, 1h→Binance). Si Polymarket la cambió (p. ej. a Pyth), **pausa y alerta** en vez de apostar con una calibración que ya no coincide. |
| **Re-validación pre-fill (orden límite)** | Tras detectar la señal, espera `RETARDO_FILL_S` (0.5s), **re-lee el libro** y solo "ejecuta" si el precio fresco sigue dando `EV ≥ 5%` | Simula la latencia decisión→fill (el *espejismo del ask*): entre que el bot decide y su orden llega al CLOB, el libro se mueve. Modela una **orden límite** al peor precio aceptable (`prob − margen`): si el ask que vimos desapareció, la orden **no llena** (oportunidad perdida) en vez de pagar de más. Nunca registra un fill peor del que una orden límite real conseguiría. |

El corazón del filtro es el **EV**:

```
EV = prob − precio_entrada       (precio_entrada = VWAP del fill + fee)
```

- `prob`          = probabilidad real de ganar (de la tabla histórica), ej. 0.63.
- `precio_entrada` = lo que de verdad pagarías por "UP" tras el slippage, ej. 0.52.
- `EV`            = 0.63 − 0.52 = **+0.11 → +11%**.

Si el EV es ≥ 5%, significa que **el mercado te está pagando más de lo que vale el riesgo real**. Esa es la señal de oro.

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

El valor esperado por apuesta es:

```
Ganancia esperada = prob × (1 − precio_entrada) − (1 − prob) × precio_entrada = prob − precio_entrada = EV
```

Si **siempre** apuestas con EV ≥ 5%, en promedio ganas 5 centavos por cada dólar arriesgado, **aunque pierdas algunas apuestas individuales**. La estadística juega a tu favor con el volumen de operaciones.

### Cómo lo verifica el bot (paper trading)

El bot **no usa dinero real** (es simulación/"paper trading"). Cuando se abre la **siguiente** ventana, mira el cierre de la anterior:

1. Compara el **cierre** con la **apertura** de la ventana donde hubo señal.
2. Si cierre ≥ apertura → **WIN** 🟢; si no → **LOSS** 🔴.
3. Suma el P&L (`+1−precio_entrada` o `−precio_entrada`) al acumulado.
4. Lo registra en el CSV y lo muestra en pantalla.

---

## 9. Qué Verás en la Terminal

```
⚙️  [ 5m] calibrando 30 días (probabilidad por minuto)…
⚙️  calib 5m: 43201 sub-velas · 8640 ventanas · 4 checkpoints · 4 rangos con ventaja
⚙️  [ 5m] filtros: liquidez≥$5,000 · orden 100u · EV≥5% · fee 0.01 · ventana 5m
🕒 [ 5m] 17:35→17:40 · apertura $63,084.00 · cierre ant $63,001
🔴 [ 5m] SEÑAL · +3min · caída 0.03% · prob 63% · fill $0.520 (mejor $0.52 +slip $0.000/1niv +fee 0.01) · EV +10.7% · liq $10,063
🟢 [ 5m] GANÓ  $63,084→$63,120 · aciertos 1/1 (100%) · P&L +0.47 (acum +0.47)
```

| Icono | Significado |
| :--- | :--- |
| ⚙️ | Calibración / configuración |
| 🕒 | Se abrió una nueva ventana |
| 🔴 (SEÑAL) | ¡Oportunidad de compra detectada! |
| 🟢 | La señal anterior **ganó** |
| 🔴 (resultado) | La señal anterior **perdió** |

Al detener con `Ctrl+C`, cada bot muestra un resumen:

```
🟢 [ 5m] RESUMEN  12 señales · 9W/3L (75%) · GANANCIA neta +2.40 u
```

---

## 10. El Registro CSV

Cada señal y su resultado se guardan en `senales_5m.csv`, `senales_15m.csv`, `senales_1h.csv`, con estas columnas:

| Columna | Qué guarda |
| :--- | :--- |
| Fecha/Hora | Momento exacto de la señal |
| Ventana | Inicio de la ventana |
| Min transcurrido | Minutos dentro de la ventana al detectar |
| Apertura | Precio BTC al abrir la ventana |
| Minimo | Mínimo alcanzado hasta ese momento |
| Caída % | Caída acumulada |
| Prob | Probabilidad real (tabla temporal) |
| Precio entrada | Precio ejecutable real pagado (VWAP del fill + fee) |
| Volumen | Volumen del mercado |
| Liquidez | Profundidad del order book |
| EV | Valor esperado de la operación |
| Acción | "COMPRAR UP" |
| Resultado | "WIN" o "LOSS" (se llena al cerrar la ventana) |

Los datos se **acumulan** entre ejecuciones (modo *append*), así que puedes dejar el bot corriendo días y analizar la tasa de acierto real después.

---

## 11. Resumen del Flujo Completo

```
   ┌─────────────────────────────────────────────────────────┐
   │ 1. CALIBRAR: 30 días de Binance → tabla P(UP|minuto,caída)│
   └─────────────────────────────────────────────────────────┘
                              │
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │ 2. CADA 30s–1min:                                        │
   │    · precio BTC en vivo (Binance)                        │
   │    · caída acumulada + minutos transcurridos             │
   │    · prob = tabla temporal                               │
   │    · fill = precio promedio real simulando la orden       │
   │           (VWAP caminando el order book CLOB)             │
   └─────────────────────────────────────────────────────────┘
                              │
                              ▼
        ¿prob>0  Y  liquidez≥$5k  Y  orden llena  Y  EV≥5%?
                       │  sí                 │  no
                       ▼                     ▼
            🔴 SEÑAL: COMPRAR UP        seguir monitoreando
                       │
                       ▼
   ┌─────────────────────────────────────────────────────────┐
   │ 3. AL CERRAR LA VENTANA: cierre ≥ apertura ?             │
   │    sí → WIN (+1−precio)      no → LOSS (−precio)         │
   │    actualizar P&L y guardar resultado en CSV            │
   └─────────────────────────────────────────────────────────┘
```

---

## 12. Conceptos Clave en una Tabla

| Término | Significado simple |
| :--- | :--- |
| **Reversión a la media** | Una caída pequeña suele rebotar hacia arriba. |
| **Probabilidad (prob)** | Qué tan seguido, en el historial, cerró arriba en esa misma situación. |
| **Fill / VWAP** | Precio promedio real que pagas al barrer el order book con tu orden (incluye slippage). |
| **EV (valor esperado)** | `prob − precio_entrada`. Si es positivo, el mercado te paga de más → conviene apostar. |
| **Liquidez** | Dinero disponible en el mercado; evita que tu orden mueva mucho el precio. |
| **Look-ahead bias** | El error de usar información del futuro; el bot lo evita con la tabla temporal. |
| **Paper trading** | Simulación sin dinero real para validar la estrategia primero. |

---

> **Filosofía del sistema:** no se trata de adivinar el futuro, sino de apostar **únicamente** cuando las matemáticas están a tu favor (EV positivo) y dejar que la **ley de los grandes números** convierta esa ventaja en ganancia con el tiempo.
