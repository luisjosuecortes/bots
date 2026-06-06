# 🐧 Cómo Funciona PolyPenguin — Explicación Detallada

Este documento explica, paso a paso y en lenguaje claro, **qué hace el bot**, **de dónde saca los datos**, **cómo decide que hay una señal** y **cómo esa señal se traduce en ganar (o perder) dinero**, tanto en *paper trading* (simulado) como en **modo real** (órdenes con tu wallet en Polymarket).

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

## 3. Arquitectura del Sistema

El sistema se controla desde una **CLI única**: `polypenguin.py`. Desde su menú lanzas los bots, eliges el modo (paper/real) y configuras la wallet.

```
config.json  ──────────────┐  (parámetros centralizados: MIN_PROB / EV_MIN,
                           │   bots a correr, modo, ajustes de wallet)
                           ▼
polypenguin.py (CLI menú: Iniciar · Ajustes · Salir)
    └─ lanza como subprocesos, con POLY_MODO=paper|real:
         5_minutos/paper_trading_bot_5m.py  ──┐
         15_minutos/paper_trading_bot_15m.py ─┤─ leen parámetros de config.json
                                              ├─ calibran su tabla (21d, refresh 24h)
                                              ├─ se conectan al feed Chainlink en vivo
                                              ├─ ejecutan el bucle de monitoreo
                                              ├─ (modo real) colocan la orden con la wallet
                                              └─ guardan señales/resultados en su CSV
```

> **Cambio respecto a versiones anteriores:** ya no existe `master_bot.py`. El lanzador es `polypenguin.py` (menú navegable con flechas) y **todos los parámetros viven en `config.json`** (single source of truth), no hardcodeados en cada bot.

### Los 2 bots (timeframes)

El sistema corre hasta **2 bots en paralelo**, uno por cada duración de mercado de BTC en Polymarket con resolución por Chainlink:

| Bot | Ventana | Frecuencia de chequeo |
| :--- | :--- | :--- |
| 5m  | 5 minutos  | cada 30 segundos |
| 15m | 15 minutos | cada 60 segundos |

En **Ajustes › Bots** puedes correr `ambos`, `solo 5m` o `solo 15m`. Ambos funcionan igual; solo cambia la duración de la ventana y cada cuánto miran el precio.

---

## 4. Las Fuentes de Datos (todas EXACTAS, las de Polymarket)

El sistema usa **exactamente las mismas fuentes que Polymarket**, para que tanto el paper trading como la operación real sean fieles a lo que pasa de verdad.

### A) Chainlink BTC/USD — el precio en vivo y el "precio a superar"
- Polymarket resuelve los mercados de 5m/15m con el **data stream de Chainlink BTC/USD** (ni Binance ni Coinbase).
- El bot se conecta al **WebSocket oficial de Polymarket**: `wss://ws-live-data.polymarket.com`, tópico `crypto_prices_chainlink`, filtro `{"symbol":"btc/usd"}`. **No requiere autenticación.**
- De ese feed salen, idénticos a lo que muestra la web de Polymarket:
  - **Precio actual** = último tick de Chainlink (`feed.precio_actual()`).
  - **Precio a superar (strike)** = el **primer tick en/ tras la frontera** de la ventana (`feed.strike_en(...)`).
  - **Mínimo de la ventana** = el menor tick desde que abrió (`feed.minimo_desde(...)`, datos por segundo).
- El feed corre en una tarea de fondo con **reconexión automática** y buffer interno.

> **Por qué importa:** Coinbase/Binance se desvían **$13–23** del valor real de Chainlink. En una vela de 5 minutos eso basta para que el "precio a superar" no cuadre y hasta para cambiar quién gana. Por eso se usa Chainlink directo.

### B) Coinbase — SOLO para calibrar y para el filtro de régimen
- Chainlink Data Streams **no ofrece histórico gratuito**, así que para construir la tabla de probabilidades (sección 5) se descargan **21 días de velas de Coinbase BTC/USD** (mismo par USD que Chainlink, coinciden dentro de ~0.03%).
- Coinbase también alimenta el **filtro de régimen** (tendencia de los últimos 60/90 min). **No** decide el precio a superar, ni el precio en vivo, ni el resultado — todo eso es exacto vía Chainlink/Polymarket.

### C) Polymarket — dónde se apuesta y cómo se resuelve
- **API de eventos (Gamma)**: `gamma-api.polymarket.com/events?slug=...` — da **liquidez**, **volumen**, los **tokens** (`clobTokenIds`) y, al cerrar, el **resultado real de la liquidación** (`outcomePrices`).
- **API del order book (CLOB)**: `clob.polymarket.com/book?token_id=...` — da todos los niveles de venta (asks). El bot **simula el fill real** de una orden de 100 unidades caminando esos niveles del más barato al más caro y calcula el **VWAP** (precio promedio ponderado): lo que **de verdad pagarías** con slippage. La lectura es **gratis**; en modo paper no envía nada.

### Hora en ET (como Polymarket)
Polymarket nombra y muestra sus mercados en **hora del Este (ET)**. El bot usa `America/New_York`, que aplica el cambio EDT(-4)/EST(-5) automáticamente. Por eso la ventana se ve como `16:00-16:05 ET`, igual que en la web.

---

## 5. Cómo Calibra el Bot (la "inteligencia")

Antes de operar, cada bot descarga **21 días de historial** (Coinbase, par USD) y construye una **tabla de probabilidad condicionada al tiempo**.

### ¿Qué significa "condicionada al tiempo"?

Una versión ingenua diría: *"si la caída total de la ventana fue ≤0.05%, cerró arriba el 71% de las veces"*. **Eso es trampa** (*look-ahead bias*), porque al **principio** de la ventana **todavía no sabes** cuánto va a caer en total.

La tabla real responde la pregunta correcta:

> *"Han pasado **E minutos** desde que abrió la ventana, y hasta **ahora** la caída acumulada es **≤X%**. Históricamente, ¿qué porcentaje de veces cerró arriba?"*

### Ejemplo (bot de 5m, caída ≤0.05%)

| Minutos transcurridos | Probabilidad de cerrar UP |
| :--- | :--- |
| +1 min | 53.7% |
| +2 min | 58.2% |
| +3 min | 62.8% |
| +4 min | 67.4% |

La ventaja **no existe al abrir la ventana**: va **creciendo** conforme avanza el tiempo y el precio se mantiene sin caer. Por eso el bot **no emite señales al inicio** (de hecho, si queda ≤1 minuto en la ventana tampoco opera).

Cómo se construye la tabla internamente:
1. Agrupa todas las velas de 1m de los últimos 21 días por la ventana a la que pertenecen.
2. Para cada ventana recorre minuto a minuto llevando el **mínimo acumulado** hasta ese instante.
3. En cada "checkpoint" (minuto 1, 2, 3...) anota: *(minuto, rango de caída) → ¿cerró arriba sí/no?*.
4. Solo guarda los rangos con **suficientes muestras** y cuyo **límite inferior de Wilson (95%) supere 50%** — no basta con que la proporción cruda gane: hay que estar 95% seguros de que la ventaja real es mayor que el azar. Ese límite inferior (conservador) es la `prob` que usa el bot.

> **¿Por qué Wilson y no la proporción simple?** Con pocas muestras `wins/total` es ruidoso. El límite inferior de Wilson penaliza la incertidumbre: con muchas muestras ≈ la proporción real; con pocas cae por debajo de 0.5 y el bucket se descarta solo.

La tabla se **recalibra automáticamente cada 24 horas** (`REFRESH_HORAS`), en un executor aparte para no cortar el feed Chainlink.

---

## 6. El Ciclo de Monitoreo (qué hace en cada chequeo)

En cada vuelta (cada 30s–60s), el bot ejecuta estos pasos:

1. **Recalibra la tabla** si han pasado 24h desde la última vez.
2. **Resuelve señales pendientes**: para cada señal cuya ventana ya cerró, lee el **resultado real de Polymarket** (sección 8) y actualiza el P&L.
3. **Identifica la ventana actual** (ej. 16:00→16:05 ET) y su **precio a superar** (`feed.strike_en`).
4. **Calcula el mínimo real** alcanzado en lo que va de ventana (`feed.minimo_desde`).
5. **Mide la caída acumulada**: `(apertura − mínimo) / apertura`.
6. **Mide los minutos transcurridos** desde que abrió la ventana (si quedan ≤1 min, se salta).
7. **Consulta la tabla temporal**: dado ese minuto y esa caída, ¿cuál es la `prob` de cerrar UP?
8. **Aplica los filtros** (sección 7).
9. Si **todo pasa**, emite **una señal** (solo una por ventana), la registra en el CSV y la deja **pendiente de resolución**. En modo real, además **coloca la orden** con la wallet.

---

## 7. Los Filtros de Seguridad (cuándo SÍ hay señal)

Para que el bot dispare una señal 🔴, deben cumplirse **todas** estas condiciones:

| Filtro | Condición | Por qué |
| :--- | :--- | :--- |
| **Probabilidad con ventaja (Wilson)** | la `prob` de la tabla ya pasó el corte de Wilson | Solo entran buckets con ventaja estadística **robusta**, no ruido. |
| **Convicción mínima** | `prob ≥ MIN_PROB` (config.json, hoy **0.60**) | Piso de convicción; ajustable por timeframe. |
| **Una sola por ventana** | la ventana aún no disparó | Evita disparar varias veces en la misma ventana. |
| **Régimen de mercado** | tendencia ≥ −2% en el lookback (5m→60m, 15m→90m) | Solo se abstiene en *free-fall* real. Las caídas moderadas (−0.6…−2%) históricamente **rebotan ~70%**. |
| **Verificación de oráculo** | la descripción del mercado menciona **Chainlink** | Si Polymarket cambió la fuente (p. ej. a Pyth), **pausa y alerta** en vez de apostar con un feed que ya no coincide. |
| **Precio mínimo válido** | `vwap ≥ $0.01` | Descarta mercados sin datos reales (precio $0.00). |
| **Liquidez mínima** | `liquidez ≥ $5,000` | Garantiza profundidad para entrar/salir sin slippage extremo. |
| **Orden completamente llenada** | se pueden llenar las 100 unidades caminando el libro | Si el order book no tiene profundidad para tu orden completa, no hay señal. |
| **Fill por profundidad (VWAP)** | `precio_entrada = VWAP + fee (0.01)` | El precio de entrada es el promedio ponderado de barrer el libro (slippage real) más un colchón de fee. |
| **Valor esperado mínimo** | `EV ≥ EV_MIN` (config.json, hoy **0.15**) | El margen tiene que cubrir comisiones y slippage y aún dejar ganancia. |
| **Re-validación pre-fill (orden límite)** | tras `RETARDO_FILL_S` (0.5s) **re-lee el libro** y solo "ejecuta" si el precio fresco aún da `EV ≥ EV_MIN` | Simula la latencia decisión→fill. Modela una **orden límite** al peor precio aceptable (`prob − EV_MIN`): si el libro se movió en tu contra, **no llena** en vez de pagar de más. |

El corazón del filtro es el **EV**:

```
EV = prob − precio_entrada       (precio_entrada = VWAP del fill + fee)
```

- `prob`           = probabilidad real de ganar (tabla histórica), ej. 0.63.
- `precio_entrada` = lo que de verdad pagarías por "UP" tras el slippage, ej. 0.48.
- `EV`             = 0.63 − 0.48 = **+0.15 → +15%**.

Si el EV es ≥ `EV_MIN`, **el mercado te está pagando más de lo que vale el riesgo real**. Esa es la señal de oro.

---

## 8. Cómo se Gana (o Pierde) Dinero — el P&L

### La mecánica de la apuesta

En Polymarket compras una acción "UP" a un precio entre $0 y $1:
- Si **aciertas** (Bitcoin cierra ≥ apertura), tu acción vale **$1.00**.
- Si **fallas** (Bitcoin cierra < apertura), tu acción vale **$0.00**.

### El cálculo de ganancia/pérdida (por unidad)

Si compraste "UP" a un `precio_entrada` (el fill real, VWAP + fee):

| Resultado | Fórmula | Ejemplo (precio = $0.48) |
| :--- | :--- | :--- |
| **GANÓ** 🟢 | `+ (1.00 − precio_entrada)` | +$0.52 |
| **PERDIÓ** 🔴 | `− precio_entrada` | −$0.48 |

Por eso conviene comprar **barato** (precio bajo) con **alta probabilidad**: ganas mucho cuando aciertas y arriesgas poco.

### Por qué el EV+ gana a largo plazo

```
Ganancia esperada = prob × (1 − precio_entrada) − (1 − prob) × precio_entrada = prob − precio_entrada = EV
```

Si **siempre** apuestas con EV ≥ `EV_MIN`, en promedio ganas ese margen por cada dólar arriesgado, **aunque pierdas algunas apuestas individuales**.

### Cómo se resuelve (resolución EXACTA, igual en paper y real)

El resultado **no se infiere** comparando velas: se **lee la liquidación oficial de Polymarket**:

1. Cuando la ventana cierra, Polymarket marca el mercado como `closed` y pone `outcomePrices` en `["1","0"]` (ganó **UP**) o `["0","1"]` (ganó **DOWN**).
2. El bot consulta ese resultado real (`resultado_resuelto`), reintentando hasta `MAX_INTENTOS_RESOLUCION` (20) porque la liquidación tarda unos segundos.
3. Si nuestra compra fue UP y ganó UP → **WIN** 🟢; si no → **LOSS** 🔴.
4. Suma el P&L (`+1−precio_entrada` o `−precio_entrada`) al acumulado, lo registra en el CSV y lo muestra.

> Esto es **100% exacto**: es literalmente lo que Polymarket pagó.

---

## 9. Modo Paper vs Modo Real

PolyPenguin opera en dos modos, elegibles en **Ajustes › Modo**:

| | **Paper (simulado)** | **Real (wallet)** |
| :--- | :--- | :--- |
| Dinero | Ninguno, todo simulado | USDC real en tu cuenta de Polymarket |
| Detección de señal | Idéntica | Idéntica |
| Al disparar | Solo registra la señal | Además coloca la orden vía `wallet_real.comprar_up` (`intentar_orden_real`) |
| Resolución / P&L | Liquidación real de Polymarket | Liquidación real de Polymarket |

En modo real, `polypenguin.py` exige **confirmación explícita** antes de lanzar y pasa `POLY_MODO=real` a los bots por variable de entorno; sin esa marca, el bot **se queda en paper** aunque haya wallet.

### La wallet (Ajustes › Wallet)
- **Clave privada**: se guarda en `.wallet_secreto` (permisos 600, ignorado por git) o en la variable `POLY_PK`. Nunca va a `config.json`.
- **Tipo de firma**: recomendado **3 = deposit wallet** (cuenta actual de polymarket.com). El `funder` (proxy/deposit wallet) se **deriva solo** desde la clave.
- **Tamaño de orden**: USDC por señal (mínimo del CLOB **$1.00**; la orden se envía por importe en USDC).
- **Tipo de orden**: `FOK` (todo o nada, al instante) o `GTC` (queda en el libro).
- **Saldo operable**: se lee con el **CLOB v2** (pUSD en tu deposit wallet). Polymarket agrupa el colateral en un contrato común, así que mirar `balanceOf` on-chain siempre da 0 — por eso se usa el CLOB.

⚠️ **Prueba siempre en modo paper primero.**

---

## 10. El Registro CSV

Cada señal y su resultado se guardan en `5_minutos/senales_5m.csv` y `15_minutos/senales_15m.csv`, con estas columnas:

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

Los datos se **acumulan** entre ejecuciones (modo *append*, con una marca de `SESION:` por arranque), así que puedes dejar el bot corriendo días y analizar la tasa de acierto real después.

---

## 11. Backtesting y Ajuste de Parámetros

Los parámetros `MIN_PROB` y `EV_MIN` (en `config.json`) se ajustan a mano o con los backtests, que miden el **P&L real** (precio de entrada por order book y liquidación oficial):

- `backtest_pnl_real.py` — P&L real contra Polymarket en días recientes.
- `backtest_pnl_sweep.py` / `backtest_sweep.py` — barrido de parámetros.
- `backtest_walkforward.py` — validación walk-forward (cada día calibra solo con datos previos → sin look-ahead).
- `backtest_lib.py` — lógica compartida.

> Según el análisis de 21 días: el 5m es rentable con `MIN_PROB=0.60` + `EV_MIN=0.15`; el 15m es marginal.

---

## 12. Qué Verás en la Terminal

```
⚙️  [ 5m] calibrando 30 días (probabilidad por minuto)…
⚙️  [ 5m] filtros: liquidez≥$5,000 · orden 100u · EV≥15% · fee 0.01 · ventana 5m
⚙️  [ 5m] feed Chainlink conectado · BTC $60,373.50
🕒 [ 5m] 16:00-16:05 ET · precio a superar $60,277.32 · actual $60,373.50
🔴 [ 5m] SEÑAL · +3min · caída 0.03% · prob 63% · fill $0.480 (mejor $0.48 +slip $0.000/1niv +fee 0.01) · EV +15.0% · liq $10,063
💰 [ 5m] ORDEN REAL · ~2.1 shares @ $0.490 ($1.00) · <resp del CLOB>     (solo en modo real)
🟢 [ 5m] GANÓ UP (2026-06-05 16:00:00 · resolvió UP) · aciertos 1/1 (100%) · P&L +0.51 (acum +0.51)
```

| Icono | Significado |
| :--- | :--- |
| ⚙️ | Calibración / configuración / feed |
| 🕒 | Se abrió una nueva ventana (hora en ET) |
| 🔴 (SEÑAL) | ¡Oportunidad de compra detectada! |
| 💰 | Orden real colocada en Polymarket (solo modo real) |
| 🟢 | La señal anterior **ganó** (según la liquidación real) |
| 🔴 (resultado) | La señal anterior **perdió** |

Al detener con `Ctrl+C`, cada bot muestra un resumen:

```
🟢 [ 5m] RESUMEN  12 señales · 9W/3L (75%) · GANANCIA neta +2.40 u
```

---

## 13. Resumen del Flujo Completo

```
   ┌─────────────────────────────────────────────────────────┐
   │ polypenguin.py → lee config.json (MIN_PROB/EV_MIN, modo) │
   │ lanza bot 5m y/o 15m con POLY_MODO=paper|real           │
   └─────────────────────────────────────────────────────────┘
                              │
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │ 1. CALIBRAR: 21 días (Coinbase) → tabla P(UP|minuto,caída)│
   │    + conectar feed Chainlink en vivo (WebSocket)         │
   └─────────────────────────────────────────────────────────┘
                              │
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │ 2. CADA 30s–60s:                                         │
   │    · resolver señales pendientes (liquidación Polymarket)│
   │    · precio a superar + actual (Chainlink en vivo)       │
   │    · caída acumulada + minutos transcurridos             │
   │    · prob = tabla temporal                               │
   │    · fill = VWAP caminando el order book CLOB            │
   └─────────────────────────────────────────────────────────┘
                              │
                              ▼
   ¿prob≥MIN_PROB Y régimen OK Y oráculo=Chainlink Y liq≥$5k
    Y orden llena Y EV≥EV_MIN Y re-validación pre-fill OK?
                       │  sí                 │  no
                       ▼                     ▼
       🔴 SEÑAL: COMPRAR UP            seguir monitoreando
       (modo real → 💰 orden)
                       │
                       ▼
   ┌─────────────────────────────────────────────────────────┐
   │ 3. AL RESOLVER POLYMARKET: outcomePrices = ["1","0"]?    │
   │    ganó UP → WIN (+1−precio)   ganó DOWN → LOSS (−precio)│
   │    actualizar P&L y guardar resultado en CSV            │
   └─────────────────────────────────────────────────────────┘
```

---

## 14. Conceptos Clave en una Tabla

| Término | Significado simple |
| :--- | :--- |
| **Reversión a la media** | Una caída pequeña suele rebotar hacia arriba. |
| **Precio a superar (strike)** | El precio de Chainlink al abrir la ventana; UP gana si el cierre lo iguala o supera. |
| **Probabilidad (prob)** | Qué tan seguido, en el historial, cerró arriba en esa misma situación. |
| **Fill / VWAP** | Precio promedio real que pagas al barrer el order book con tu orden (incluye slippage). |
| **EV (valor esperado)** | `prob − precio_entrada`. Si es ≥ `EV_MIN`, el mercado te paga de más → conviene apostar. |
| **MIN_PROB / EV_MIN** | Los dos parámetros ajustables, centralizados en `config.json` por timeframe. |
| **Liquidez** | Dinero disponible en el mercado; evita que tu orden mueva mucho el precio. |
| **Look-ahead bias** | El error de usar información del futuro; el bot lo evita con la tabla temporal. |
| **Paper vs Real** | Paper simula sin dinero; Real coloca órdenes con tu wallet (CLOB v2). |

---

> **Filosofía del sistema:** no se trata de adivinar el futuro, sino de apostar **únicamente** cuando las matemáticas están a tu favor (EV positivo) y dejar que la **ley de los grandes números** convierta esa ventaja en ganancia con el tiempo. Y todo se mide contra las **fuentes exactas de Polymarket** (Chainlink + liquidación real), para que paper y real sean fieles a lo que pasaría operando en serio.
