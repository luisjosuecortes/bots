"""PolyPenguin — CLI para lanzar los bots de trading de Polymarket.

Menú inicial (navegable con flechas ↑/↓ y enter):
  1) elegir bots: ambos / solo 5m / solo 15m
  2) elegir modo: paper trading (simulado) o real (wallet, aún sin uso)

El modo real por ahora NO opera con la wallet: queda como marcador para el
futuro y se ejecuta igual que paper trading.
"""
import subprocess
import time
import os
import fcntl
import sys
from datetime import datetime

try:
    import termios
    import tty
    _TTY = True
except ImportError:  # p. ej. Windows: caemos a menú por número
    _TTY = False

BASE = os.path.dirname(os.path.abspath(__file__))

SCRIPTS = {
    "5m": os.path.join(BASE, "5_minutos", "paper_trading_bot_5m.py"),
    "15m": os.path.join(BASE, "15_minutos", "paper_trading_bot_15m.py"),
}

CSVS = {
    "5m": os.path.join(BASE, "5_minutos", "senales_5m.csv"),
    "15m": os.path.join(BASE, "15_minutos", "senales_15m.csv"),
}

# --- Paleta azul (ANSI) -----------------------------------------------------
AZUL = "\033[38;2;64;156;255m"   # azul vivo (truecolor)
AZUL_OSC = "\033[38;5;27m"       # azul oscuro
CIAN = "\033[38;5;51m"           # cian
GRIS = "\033[38;5;245m"          # gris para notas
BLANCO = "\033[97m"
NEGRITA = "\033[1m"
RESET = "\033[0m"

# Banner "PolyPenguin": pequeño, hueco y en una sola línea.
BANNER = r"""
  ╭─╮ ╭─╮ ╷   ╲ ╱ ╭─╮ ╭── ╷ ╷ ╭── ╷ ╷ ╷ ╷ ╷
  ├─╯ │ │ │    │  ├─╯ ├─  │╲│ │╶╮ │ │ │ │╲│
  ╵   ╰─╯ ╰──  ╵  ╵   ╰── ╵ ╵ ╰─╯ ╰─╯ ╵ ╵ ╵
"""


def c(texto, color):
    return f"{color}{texto}{RESET}"


def limpiar():
    os.system("cls" if os.name == "nt" else "clear")


def cabecera():
    """Imprime el banner azul grande de PolyPenguin."""
    print(c(BANNER, AZUL + NEGRITA))


def _getch():
    """Lee una tecla (incluidas las flechas) en modo crudo."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":                 # secuencia de escape (flechas)
            ch += sys.stdin.read(2)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def menu(titulo, opciones):
    """Menú navegable con flechas. 'opciones' = [(clave, etiqueta, pista)].

    Devuelve la clave elegida. Si no hay terminal interactiva, cae a número.
    """
    if not (_TTY and sys.stdin.isatty()):
        return _menu_numerico(titulo, opciones)

    idx = 0
    while True:
        limpiar()
        cabecera()
        print(c(f"  {titulo}", BLANCO + NEGRITA))
        print(c("  " + "─" * 46, AZUL_OSC))
        for i, (_, etiqueta, pista) in enumerate(opciones):
            if i == idx:
                fila = c("  ▸ ", CIAN + NEGRITA) + c(etiqueta, CIAN + NEGRITA)
            else:
                fila = "    " + c(etiqueta, BLANCO)
            if pista:
                fila += "  " + c(pista, GRIS)
            print(fila)
        print()
        print(c("  ↑/↓ moverse · enter elegir · ctrl-c salir", GRIS))

        tecla = _getch()
        if tecla in ("\x1b[A", "k"):
            idx = (idx - 1) % len(opciones)
        elif tecla in ("\x1b[B", "j"):
            idx = (idx + 1) % len(opciones)
        elif tecla in ("\r", "\n"):
            return opciones[idx][0]
        elif tecla == "\x03":
            raise KeyboardInterrupt


def _menu_numerico(titulo, opciones):
    """Respaldo por número cuando no hay terminal interactiva."""
    print(c(f"  {titulo}", BLANCO + NEGRITA))
    for i, (_, etiqueta, pista) in enumerate(opciones, 1):
        extra = f"  {c(pista, GRIS)}" if pista else ""
        print(f"  {c(str(i), CIAN)}) {etiqueta}{extra}")
    while True:
        sel = input(c("  ▸ opción: ", AZUL)).strip()
        if sel.isdigit() and 1 <= int(sel) <= len(opciones):
            return opciones[int(sel) - 1][0]


def configurar_lectura_no_bloqueante(process):
    fd = process.stdout.fileno()
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)


def leer_salida(process):
    lineas = []
    try:
        while True:
            line = process.stdout.readline()
            if line:
                lineas.append(line.rstrip())
            else:
                break
    except (IOError, BlockingIOError):
        pass
    return lineas


def elegir_config():
    """Pregunta bots y modo. Devuelve (lista_de_claves_bot, modo)."""
    eleccion = menu("¿Qué bots quieres ejecutar?", [
        ("ambos", "Ambos", "5m + 15m"),
        ("5m", "Solo 5 minutos", "rápido, más señales"),
        ("15m", "Solo 15 minutos", "más lento, más filtrado"),
    ])
    bots = ["5m", "15m"] if eleccion == "ambos" else [eleccion]

    modo = menu("¿Con qué modo quieres operar?", [
        ("paper", "Paper trading", "simulado, sin dinero real"),
        ("real", "Real (wallet)", "aún no disponible"),
    ])
    return bots, modo


def lanzar(bots, modo):
    limpiar()
    cabecera()

    etiqueta_modo = "PAPER TRADING (simulado)" if modo == "paper" else "REAL (wallet)"
    print(c("  configuración", BLANCO + NEGRITA))
    print(c("  " + "─" * 46, AZUL_OSC))
    print(f"  {c('fecha', GRIS):<8} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {c('bots', GRIS):<8} {c(', '.join(bots), CIAN)}")
    print(f"  {c('modo', GRIS):<8} {c(etiqueta_modo, CIAN)}")
    print(f"  {c('nota', GRIS):<8} cada bot auto-calibra al iniciar (~30-60s)")
    print(f"  {c('ctrl-c', GRIS):<8} detiene todos los bots")

    if modo == "real":
        print()
        print(c("  ⚠  el modo real aún no opera con tu wallet:", AZUL))
        print(c("     se ejecuta como paper trading por ahora.", GRIS))
    print()

    processes = {}
    try:
        for nombre in bots:
            ruta = SCRIPTS[nombre]
            if not os.path.exists(ruta):
                print(c(f"  ERROR archivo no encontrado: {ruta}", AZUL))
                continue
            process = subprocess.Popen(
                ["python3", "-u", ruta],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            configurar_lectura_no_bloqueante(process)
            processes[nombre] = process
            print(c(f"  ▸ iniciado {nombre} (pid {process.pid})", AZUL))
            time.sleep(2)

        print(c(f"\n  {len(processes)} bot(s) corriendo\n", CIAN + NEGRITA))

        while processes and all(p.poll() is None for p in processes.values()):
            for process in processes.values():
                for linea in leer_salida(process):
                    print(linea, flush=True)
            time.sleep(1)

        for nombre, process in processes.items():
            if process.poll() is not None:
                print(c(f"  {nombre} terminó (código {process.returncode})", GRIS))
                for linea in leer_salida(process):
                    print(linea, flush=True)

    except KeyboardInterrupt:
        print(c("\n  deteniendo todos los bots...", AZUL))
        for nombre, process in processes.items():
            if process.poll() is None:
                process.terminate()
                print(c(f"  {nombre} detenido", GRIS))
        for process in processes.values():
            process.wait()
        print(c("\n  datos guardados en:", BLANCO))
        for nombre in bots:
            print(f"    {c(nombre, CIAN)}: {CSVS[nombre]}")


def main():
    try:
        bots, modo = elegir_config()
    except KeyboardInterrupt:
        print(c("\n  hasta luego 🐧", AZUL))
        return
    lanzar(bots, modo)


if __name__ == "__main__":
    main()
