"""PolyPenguin — CLI para lanzar los bots de trading de Polymarket.

Menú principal (navegable con flechas ↑/↓ y enter):
  • Iniciar   — lanza los bots con la configuración guardada
  • Ajustes   — elige bots, modo (paper/real) y configura la wallet real
  • Salir

En modo PAPER todo es simulado (sin dinero). En modo REAL, los bots colocan
órdenes reales en Polymarket con tu wallet (py-clob-client). La wallet se
configura en Ajustes › Wallet; la config se guarda en config.json y la clave
privada en .wallet_secreto (ambos ignorados por git).
"""
import subprocess
import time
import os
import fcntl
import sys
import getpass
from datetime import datetime

import config
import wallet_real

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

_FIRMAS = {0: "EOA (clave propia)", 1: "email / Magic", 2: "wallet del navegador",
           3: "deposit wallet"}


def c(texto, color):
    return f"{color}{texto}{RESET}"


def limpiar():
    os.system("cls" if os.name == "nt" else "clear")


def cabecera():
    """Imprime el banner azul de PolyPenguin."""
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


def pedir_texto(titulo, actual="", secreto=False):
    """Pide un valor por teclado mostrando la cabecera. Enter vacío = sin cambios."""
    limpiar()
    cabecera()
    print(c(f"  {titulo}", BLANCO + NEGRITA))
    print(c("  " + "─" * 46, AZUL_OSC))
    if actual and not secreto:
        print(c(f"  actual: {actual}", GRIS))
    print(c("  (enter vacío para dejarlo igual)", GRIS))
    prompt = c("  ▸ ", AZUL)
    valor = getpass.getpass(prompt) if secreto else input(prompt)
    return valor.strip()


# --- Lectura de la salida de los bots ---------------------------------------
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


# --- Ajustes ----------------------------------------------------------------
def _resumen_wallet(cfg):
    w = cfg["wallet"]
    estado = "lista ✓" if config.wallet_lista(cfg) else "sin configurar"
    return (f"{estado} · firma {_FIRMAS.get(int(w['signature_type']), '?')} · "
            f"orden ${w['tamano_usdc']:.2f} {w['tipo_orden']}")


def ajustes_wallet(cfg):
    """Submenú para configurar la wallet real."""
    while True:
        w = cfg["wallet"]
        clave = "guardada ✓" if config.hay_clave() else "no guardada"
        eleccion = menu("Ajustes › Wallet (modo real)", [
            ("clave", "Clave privada", clave),
            ("firma", "Tipo de firma", _FIRMAS.get(int(w["signature_type"]), "?")),
            ("funder", "Dirección funder", w["funder"] or "(vacía)"),
            ("tamano", "Tamaño de orden", f"${w['tamano_usdc']:.2f} USDC"),
            ("tipo", "Tipo de orden", w["tipo_orden"]),
            ("probar", "Probar conexión", "deriva credenciales sin operar"),
            ("balance", "Ver balance", "USDC disponible en la wallet"),
            ("volver", "Volver", ""),
        ])

        if eleccion == "clave":
            val = pedir_texto("Clave privada de la wallet", secreto=True)
            if val:
                config.guardar_clave(val)
        elif eleccion == "firma":
            sig = menu("Tipo de firma", [
                ("3", "3 · deposit wallet", "cuenta actual de polymarket.com (recomendado)"),
                ("1", "1 · email / Magic", "cuentas antiguas con email"),
                ("2", "2 · wallet navegador", "proxy de wallet del navegador"),
                ("0", "0 · EOA (clave propia)", "los fondos están en tu propia dirección"),
            ])
            w["signature_type"] = int(sig)
            config.guardar(cfg)
        elif eleccion == "funder":
            val = pedir_texto("Dirección funder (0x… · vacío = derivar sola)", w["funder"])
            w["funder"] = val   # vacío a propósito: se deriva desde la clave
            config.guardar(cfg)
        elif eleccion == "tamano":
            val = pedir_texto("Tamaño de orden en USDC", f"{w['tamano_usdc']:.2f}")
            try:
                if val:
                    w["tamano_usdc"] = max(1.0, float(val))
                    config.guardar(cfg)
            except ValueError:
                pass
        elif eleccion == "tipo":
            w["tipo_orden"] = menu("Tipo de orden", [
                ("FOK", "FOK", "todo o nada, al instante"),
                ("GTC", "GTC", "queda en el libro hasta llenarse"),
            ])
            config.guardar(cfg)
        elif eleccion == "probar":
            limpiar()
            cabecera()
            print(c("  probando conexión con el CLOB…", BLANCO + NEGRITA))
            ok, msg = wallet_real.probar_conexion(cfg)
            color = CIAN if ok else AZUL
            print(c(f"  {'✓' if ok else '✗'} {msg}", color))
            input(c("\n  enter para volver", GRIS))
        elif eleccion == "balance":
            limpiar()
            cabecera()
            print(c("  consultando tu saldo en Polymarket…\n", BLANCO + NEGRITA))

            # Saldo OPERABLE: lo que sabe el CLOB v2 (pUSD en tu deposit wallet).
            # Es el único dato fiable, porque Polymarket agrupa el colateral en un
            # contrato común, así que mirar balanceOf de tu dirección siempre da 0.
            try:
                wallet_real.reiniciar()
                clob = wallet_real.balance_usdc(cfg)
                firmante, funder = wallet_real.direcciones(cfg)
                print(c(f"  💰 {clob:,.2f} USDC operables (lo que ve Polymarket)", CIAN + NEGRITA))
                if firmante:
                    print(c(f"     firmante: {firmante}", GRIS))
                print(c(f"     funder:   {funder}", GRIS))
                if clob == 0:
                    print(c("\n  Si en la web tienes saldo pero aquí ves 0, prueba a", GRIS))
                    print(c("  cambiar el 'tipo de firma' (3=actual · 1=email) y reintenta.", GRIS))
            except wallet_real.WalletError as e:
                print(c(f"  ✗ {e}", AZUL))
            input(c("\n  enter para volver", GRIS))
        elif eleccion == "volver":
            return


def ajustes(cfg):
    """Submenú de ajustes generales."""
    while True:
        eleccion = menu("Ajustes", [
            ("bots", "Bots a ejecutar", {"ambos": "5m + 15m", "5m": "solo 5m",
                                         "15m": "solo 15m"}[cfg["bots"]]),
            ("modo", "Modo de operación", "real (wallet)" if cfg["modo"] == "real"
                                          else "paper (simulado)"),
            ("wallet", "Wallet (modo real)", _resumen_wallet(cfg)),
            ("volver", "Volver", ""),
        ])

        if eleccion == "bots":
            cfg["bots"] = menu("¿Qué bots quieres ejecutar?", [
                ("ambos", "Ambos", "5m + 15m"),
                ("5m", "Solo 5 minutos", "rápido, más señales"),
                ("15m", "Solo 15 minutos", "más lento, más filtrado"),
            ])
            config.guardar(cfg)
        elif eleccion == "modo":
            cfg["modo"] = menu("¿Con qué modo quieres operar?", [
                ("paper", "Paper trading", "simulado, sin dinero real"),
                ("real", "Real (wallet)", "órdenes reales en Polymarket"),
            ])
            config.guardar(cfg)
        elif eleccion == "wallet":
            ajustes_wallet(cfg)
        elif eleccion == "volver":
            return


# --- Lanzamiento de los bots ------------------------------------------------
def _confirmar_real(cfg):
    """En modo real, exige confirmación explícita. Devuelve True si seguir."""
    if not config.wallet_lista(cfg):
        print(c("\n  ⚠  modo real elegido pero la wallet NO está configurada.", AZUL))
        print(c("     ve a Ajustes › Wallet. Por ahora correría como paper.", GRIS))
        return menu("¿Continuar igualmente (como paper)?", [
            ("si", "Sí, continuar", ""), ("no", "No, volver", "")]) == "si"
    print(c("\n  ⚠  MODO REAL: se colocarán órdenes con DINERO REAL.", AZUL + NEGRITA))
    print(c(f"     orden por señal: ${cfg['wallet']['tamano_usdc']:.2f} USDC", GRIS))
    return menu("¿Operar con dinero real?", [
        ("no", "No, volver", ""), ("si", "Sí, operar real", "")]) == "si"


def lanzar(cfg):
    bots = ["5m", "15m"] if cfg["bots"] == "ambos" else [cfg["bots"]]
    modo = cfg["modo"]

    limpiar()
    cabecera()
    if modo == "real" and not _confirmar_real(cfg):
        return

    real_activo = modo == "real" and config.wallet_lista(cfg)
    etiqueta_modo = "REAL (wallet)" if real_activo else "PAPER TRADING (simulado)"

    limpiar()
    cabecera()
    print(c("  configuración", BLANCO + NEGRITA))
    print(c("  " + "─" * 46, AZUL_OSC))
    print(f"  {c('fecha', GRIS):<8} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {c('bots', GRIS):<8} {c(', '.join(bots), CIAN)}")
    print(f"  {c('modo', GRIS):<8} {c(etiqueta_modo, CIAN)}")
    if real_activo:
        orden_txt = f"${cfg['wallet']['tamano_usdc']:.2f} USDC por señal"
        print(f"  {c('orden', GRIS):<8} {c(orden_txt, CIAN)}")
    print(f"  {c('nota', GRIS):<8} cada bot auto-calibra al iniciar (~30-60s)")
    print(f"  {c('ctrl-c', GRIS):<8} detiene todos los bots")
    print()

    # Los bots leen POLY_MODO: solo operan real si vale "real".
    entorno = dict(os.environ, POLY_MODO="real" if real_activo else "paper")

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
                env=entorno,
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
    input(c("\n  enter para volver al menú", GRIS))


def main():
    cfg = config.cargar()
    try:
        while True:
            modo_txt = "real (wallet)" if cfg["modo"] == "real" else "paper (simulado)"
            bots_txt = {"ambos": "5m + 15m", "5m": "solo 5m", "15m": "solo 15m"}[cfg["bots"]]
            eleccion = menu("Menú principal", [
                ("iniciar", "Iniciar", f"{bots_txt} · {modo_txt}"),
                ("ajustes", "Ajustes", "bots, modo y wallet"),
                ("salir", "Salir", ""),
            ])
            if eleccion == "iniciar":
                lanzar(cfg)
            elif eleccion == "ajustes":
                ajustes(cfg)
            elif eleccion == "salir":
                break
    except KeyboardInterrupt:
        pass
    print(c("\n  hasta luego 🐧", AZUL))


if __name__ == "__main__":
    main()
