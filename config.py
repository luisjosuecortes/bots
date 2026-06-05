"""Configuración persistente de PolyPenguin (config.json) y la clave de la wallet.

- Las preferencias (bots, modo, ajustes de wallet NO secretos) viven en config.json.
- La clave privada NUNCA se guarda en config.json: va en un archivo aparte
  (.wallet_secreto, permisos 600, ignorado por git) o en la variable POLY_PK.

Ambos archivos están en .gitignore para no subirlos nunca al repositorio.
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
RUTA = os.path.join(BASE, "config.json")
RUTA_CLAVE = os.path.join(BASE, ".wallet_secreto")

DEFECTOS = {
    "bots": "ambos",          # ambos | 5m | 15m
    "modo": "paper",          # paper | real
    "wallet": {
        "signature_type": 3,  # 0=EOA · 1=email/Magic · 2=wallet navegador · 3=deposit wallet
        "funder": "",         # dirección con los fondos; si está vacía se deriva sola
        "tamano_usdc": 5.0,   # USDC a gastar por orden real
        "tipo_orden": "FOK",  # FOK (todo o nada) | GTC (queda en el libro)
    },
}


def cargar():
    """Devuelve la config combinando los valores guardados con los por defecto."""
    cfg = json.loads(json.dumps(DEFECTOS))  # copia profunda de los defectos
    if os.path.exists(RUTA):
        try:
            with open(RUTA, encoding="utf-8") as f:
                guardado = json.load(f)
            for k, v in guardado.items():
                if k == "wallet" and isinstance(v, dict):
                    cfg["wallet"].update(v)
                else:
                    cfg[k] = v
        except (ValueError, OSError):
            pass
    return cfg


def guardar(cfg):
    """Escribe la config en config.json (legible y con UTF-8)."""
    with open(RUTA, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def clave_privada():
    """Clave privada de la wallet: POLY_PK tiene prioridad; si no, el archivo."""
    env = os.environ.get("POLY_PK")
    if env and env.strip():
        return env.strip()
    if os.path.exists(RUTA_CLAVE):
        try:
            with open(RUTA_CLAVE, encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None
    return None


def guardar_clave(clave):
    """Guarda la clave privada en .wallet_secreto con permisos 600."""
    with open(RUTA_CLAVE, "w", encoding="utf-8") as f:
        f.write(clave.strip())
    os.chmod(RUTA_CLAVE, 0o600)


def hay_clave():
    return clave_privada() is not None


def wallet_lista(cfg):
    """True si hay lo mínimo para operar real: basta la clave privada.

    El 'funder' (deposit wallet / proxy) se deriva solo desde la clave, así que no
    hace falta configurarlo a mano.
    """
    return hay_clave()
