import subprocess
import time
import os
import fcntl
from datetime import datetime

SCRIPTS = {
    "1h": "/home/penguin/Documentos/poly/1_hora/paper_trading_bot.py",
    "15m": "/home/penguin/Documentos/poly/15_minutos/paper_trading_bot_15m.py",
    "5m": "/home/penguin/Documentos/poly/5_minutos/paper_trading_bot_5m.py",
}

CSVS = {
    "1h": "/home/penguin/Documentos/poly/1_hora/senales_1h.csv",
    "15m": "/home/penguin/Documentos/poly/15_minutos/senales_15m.csv",
    "5m": "/home/penguin/Documentos/poly/5_minutos/senales_5m.csv",
}


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


def rule(char="=", width=72):
    print(char * width, flush=True)


def main():
    rule()
    print("MASTER PAPER TRADING - MULTI TIMEFRAME", flush=True)
    rule()
    print(f"fecha    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"bots     {len(SCRIPTS)} ({', '.join(SCRIPTS)})", flush=True)
    print("nota     cada bot auto-calibra al iniciar (~30-60s)", flush=True)
    print("ctrl-c   detiene todos los bots", flush=True)
    rule()

    processes = {}

    try:
        for nombre, ruta in SCRIPTS.items():
            if not os.path.exists(ruta):
                print(f"[master] ERROR archivo no encontrado: {ruta}", flush=True)
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
            print(f"[master] iniciado {nombre} (pid {process.pid})", flush=True)
            time.sleep(2)

        print(f"[master] {len(processes)} bots corriendo\n", flush=True)

        while all(p.poll() is None for p in processes.values()):
            for nombre, process in processes.items():
                for linea in leer_salida(process):
                    print(linea, flush=True)
            time.sleep(1)

        for nombre, process in processes.items():
            if process.poll() is not None:
                print(f"[master] {nombre} termino (codigo {process.returncode})", flush=True)
                for linea in leer_salida(process):
                    print(linea, flush=True)

    except KeyboardInterrupt:
        print("\n[master] deteniendo todos los bots...", flush=True)
        for nombre, process in processes.items():
            if process.poll() is None:
                process.terminate()
                print(f"[master] {nombre} detenido", flush=True)
        for process in processes.values():
            process.wait()
        print("[master] datos guardados en:", flush=True)
        for nombre, ruta in CSVS.items():
            print(f"[master]   {nombre}: {ruta}", flush=True)


if __name__ == "__main__":
    main()
