# PolyPenguin - Trading Bot Polymarket 🐧

Bot automático para BTC Up/Down en Polymarket (5m y 15m). Lee parámetros centralizados de `config.json`, calibra cada 21 días y opera en vivo con feed Chainlink.

**Versión 2.0**: Arquitectura refactorizada, parámetros centralizados en `config.json`.

## 🎯 Inicio Rápido

```bash
cd /home/penguin/Documentos/poly
python polypenguin.py
```

Menú interactivo:
- **Iniciar**: Lanza bots 5m/15m (elige si validar parámetros primero)
- **Ajustes**: Configura bots, modo (paper/real), wallet
- **Salir**

## 📋 Requisitos

- Python 3.8+
- `aiohttp` (async requests)
- `py-clob-client-v2` (solo modo real)

## 🔧 Parámetros (Centralizados en `config.json`)

```json
{
  "parametros": {
    "5m": {
      "MIN_PROB": 0.60,      // Probabilidad mínima para señal
      "EV_MIN": 0.15         // Edge value mínimo (prob - precio - fee)
    },
    "15m": {
      "MIN_PROB": 0.60,
      "EV_MIN": 0.15
    }
  }
}
```

**Todos los bots leen automáticamente de `config.json`** (sin reiniciar).

### ¿Cómo se ajustan?

Los parámetros (`MIN_PROB`, `EV_MIN`) viven en `config.json` y se editan a mano
o con los backtests de `backtest_pnl_*.py`, que miden el P&L real (precio de
entrada y liquidación oficial). Cada bot, al arrancar, calibra por su cuenta la
**tabla de probabilidades** sobre los últimos 21 días y la refresca cada 24h.

## 🏗️ Arquitectura

```
config.json (parámetros centralizados: MIN_PROB / EV_MIN)
    ↓
polypenguin.py (CLI menú)
    └─ paper_trading_bot_5m.py ──┐
       paper_trading_bot_15m.py ──┤─ leen parámetros de config.json
                                  ├─ calibran su tabla de probabilidades (21d, cada 24h)
                                  ├─ ejecutan bucle en vivo
                                  └─ guardan en CSV
```

## 📁 Archivos Principales

### Core (Bot)
- **polypenguin.py** - CLI menú, lanzador de bots
- **paper_trading_bot_5m.py** - Bot 5 minutos
- **paper_trading_bot_15m.py** - Bot 15 minutos

### Librerías
- **config.py** - Gestión centralizada de configuración
- **analisis_historico_modulo.py** - Análisis y calibración
- **backtest_lib.py** - Lógica compartida de backtesting
- **cli_util.py** - Utilidades de CLI y operación
- **wallet_real.py** - Integración con Polymarket CLOB v2

### Análisis (Opcional)
- **backtest_walkforward.py** - Validación walk-forward
- **backtest_pnl_real.py** - Medición P&L real vs Polymarket
- **backtest_pnl_sweep.py** - Parameter sweep completo

## 📊 Cómo Funciona

1. **Calibración Temporal** (30 días históricos)
   - Descarga datos de Coinbase (cached en `/tmp`)
   - Construye tabla: P(UP | minutos_transcurridos, caída%)
   - Re-calibra automáticamente cada 24h

2. **Bucle en Vivo**
   - Lee feed Chainlink (precio en vivo)
   - Mide caída desde inicio de ventana
   - Consulta tabla de probabilidades
   - Si P >= MIN_PROB Y EV >= EV_MIN → **SEÑAL**
   - Lee resultado real de Polymarket

3. **Validación Exacta**
   - Resultado no se infiere: se lee de `outcomePrices` de Polymarket
   - Calcula P&L real vs precio de entrada

## 📈 Parámetros Por Defecto

| Parámetro | Valor | Nota |
|-----------|-------|------|
| MIN_PROB | 0.60 | Óptimo según análisis 21d |
| EV_MIN | 0.15 | 3x el baseline (0.05) |
| DIAS_HISTORICOS | 30 | Ventana en vivo |
| REFRESH_HORAS | 24 | Re-calibración |

## 🔐 Modo Real (Wallet)

En **Ajustes › Wallet**:
1. Ingresa clave privada (guardar en `.wallet_secreto`)
2. Selecciona tipo de firma (recomendado: 3 = deposit wallet)
3. Prueba conexión
4. Ver balance

⚠️ **Prueba en modo paper primero**

## 📊 Salida (CSV)

```
Fecha/Hora,Ventana,Min transcurrido,Apertura,Minimo,Caida %,Prob,Ask UP,Volumen,Liquidez,EV,Accion,Resultado
2026-06-05 14:30:00,2026-06-05 14:30:00,1,43500.00,43450.00,0.11,0.615,0.48,1000,5000,+0.135,SEÑAL,WIN
```

Guardado en:
- `5_minutos/senales_5m.csv`
- `15_minutos/senales_15m.csv`

## 🧪 Análisis Manual

```bash
python backtest_pnl_real.py       # P&L real últimos 6d
python backtest_walkforward.py    # Validación completa
python backtest_pnl_sweep.py      # Parameter sweep
```

## 🛠️ Troubleshooting

**Feed Chainlink sin datos**: Tarda unos segundos en conectar. Bot continúa en background intentando.

**Saldo 0 pero tengo fondos**: Prueba cambiar tipo de firma (3=actual, 1=legacy email).

**Parámetros no se aplican**: edita `MIN_PROB` / `EV_MIN` en `config.json`; los bots los leen al arrancar (sin hardcodear nada).

## 📝 Notas Técnicas

### Single Source of Truth
```
Antes:  MIN_PROB hardcodeado en 10 archivos → inconsistencia
Ahora:  config.json → todos leen de ahí → cambios automáticos
```

### Parámetros Leídos en Startup
```python
CFG = config.cargar()
MIN_PROB, EV_MIN = config.cargar_parametros(CFG, "5m")
# Bots NO tienen parámetros hardcodeados
```

### Walk-Forward (Sin Look-Ahead)
Cada día se calibra SOLO con datos previos → el futuro no influye en el pasado.

### Wilson Lower Bound
Estadística conservadora para muestras pequeñas (protege contra ruido).

## 🐧 Info General

- **Estrategia**: Reversión a la media en micro-caídas
- **Mercado**: BTC Up/Down (5m y 15m)
- **Fuente de precios**: Chainlink (misma que Polymarket)
- **Resolución**: Liquidación real de Polymarket (no inferida)
- **Operación**: Paper (simulado) o Real (wallet CLOB v2)
- **Calibración**: 21 días análisis + 30 días históricos en vivo
