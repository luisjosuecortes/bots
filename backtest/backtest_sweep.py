"""Sweep de parámetros sobre el backtest walk-forward (#2).

Barre MIN_PROB y la caída mínima exigida, y mide para cada combinación: número de
señales (frecuencia), tasa de acierto real out-of-sample y edge condicional vs la
tasa base. Objetivo: ver qué ajuste sube el win-rate sin secar las señales.

El margen EV NO se barre aquí (requiere precio de Polymarket) — eso va en el
backtest de P&L real. Aquí medimos la CALIDAD de la señal del modelo.
"""
from backtest.backtest_lib import descargar_cacheado, recolectar_señales

TRAIN_DAYS = 21
TEST_DAYS = 14
SOURCE = "coinbase"

PROBS = [0.52, 0.53, 0.55, 0.57, 0.60]
CAIDAS = [0.0, 0.05, 0.10, 0.15]   # caída mínima exigida (%)


def main():
    total = TRAIN_DAYS + TEST_DAYS + 1
    print(f"Descargando/cacheando {total} días de velas 1m ({SOURCE})…", flush=True)
    candles = descargar_cacheado("1m", total, SOURCE)
    print(f"  {len(candles)} velas.", flush=True)

    for interval in ("5m", "15m"):
        # tasa base (independiente de los parámetros): la sacamos con un pase laxo
        _, base_total, base_up = recolectar_señales(
            interval, candles, TRAIN_DAYS, TEST_DAYS, min_prob=0.52, min_caida=0.0)
        base = base_up / base_total if base_total else 0

        print(f"\n{'='*78}")
        print(f"  {interval}  ·  tasa base UP {base*100:.1f}%  ·  {base_total} ventanas de test")
        print(f"{'='*78}")
        print(f"  {'MIN_PROB':>8} {'min_caída':>9} | {'señales':>8} {'%vent':>6} "
              f"| {'win real':>8} {'edge':>7}")
        print("  " + "-" * 70)
        for mp in PROBS:
            for mc in CAIDAS:
                señales, bt, bu = recolectar_señales(
                    interval, candles, TRAIN_DAYS, TEST_DAYS, min_prob=mp, min_caida=mc)
                n = len(señales)
                if n == 0:
                    print(f"  {mp:>8.2f} {mc:>8.2f}% | {0:>8} {'—':>6} | {'—':>8} {'—':>7}")
                    continue
                wins = sum(1 for s in señales if s["gano_velas"])
                wr = wins / n
                freq = n / bt * 100
                print(f"  {mp:>8.2f} {mc:>8.2f}% | {n:>8} {freq:>5.1f}% "
                      f"| {wr*100:>7.1f}% {(wr-base)*100:>+6.1f}")
            print("  " + "-" * 70)


if __name__ == "__main__":
    main()
