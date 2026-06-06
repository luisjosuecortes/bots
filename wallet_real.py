"""Operativa REAL con la wallet vía el CLOB v2 de Polymarket (py-clob-client-v2).

Polymarket migró a CLOB v2: el colateral ya no es USDC.e suelto en tu wallet, sino
pUSD dentro de una 'deposit wallet' (un proxy por usuario). La firma por defecto es
la 3 (POLY_1271), y el 'funder' es esa deposit wallet, que se deriva sola desde tu
clave con el relayer. Por eso ahora SÍ se ve el saldo y se puede operar.

Si la librería no está instalada o la wallet no está bien configurada, las funciones
lanzan WalletError con un mensaje claro en vez de romper el bot que las llama.

Requisitos (una sola vez):
  - pip install py-clob-client-v2 py-builder-relayer-client
  - una cuenta de Polymarket con saldo (pUSD) y las allowances aprobadas
    (se aprueban solas al operar desde polymarket.com).
  - configurar la clave en PolyPenguin › Ajustes › Wallet (el funder se deriva solo).

Documentación: https://docs.polymarket.com/trading/deposit-wallets
"""
import config

HOST = "https://clob.polymarket.com"
RELAYER = "https://relayer-v2.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet

# Tipos de firma de Polymarket (v2):
#   0 = EOA (clave propia)         1 = email/Magic (proxy)
#   2 = wallet del navegador (Safe)  3 = deposit wallet (POLY_1271, el actual)
FIRMA_DEPOSITO = 3

_cliente = None  # ClobClient cacheado (derivar credenciales es lento)
_funder_cache = {}  # firma -> dirección derivada, para no llamar al relayer cada vez


class WalletError(Exception):
    """Problema de configuración, instalación o conexión de la wallet real."""


def reiniciar():
    """Olvida lo cacheado (úsalo tras cambiar la configuración)."""
    global _cliente
    _cliente = None
    _funder_cache.clear()


_HEX = set("0123456789abcdefABCDEF")


def _normalizar_clave(clave):
    """Limpia y valida la clave privada. Devuelve '0x'+64hex o lanza WalletError."""
    limpia = clave.strip().strip("'\"").replace(" ", "")
    if limpia.lower().startswith("0x"):
        limpia = limpia[2:]
    if len(limpia) != 64 or any(ch not in _HEX for ch in limpia):
        raise WalletError(
            f"la clave privada no es válida: deben ser 64 dígitos hex (0-9, a-f), "
            f"con o sin '0x'. Recibí {len(limpia)} caracteres válidos. Re-cópiala "
            f"completa desde Polymarket (Export Private Key), sin espacios.")
    return "0x" + limpia


def _validar_funder(funder):
    """Valida la dirección funder. Devuelve '0x'+40hex o lanza WalletError."""
    f = funder.strip()
    cuerpo = f[2:] if f.lower().startswith("0x") else f
    if len(cuerpo) != 40 or any(ch not in _HEX for ch in cuerpo):
        raise WalletError("la dirección 'funder' no es válida: 0x + 40 dígitos hex.")
    return "0x" + cuerpo


def _importar():
    """Importa py-clob-client-v2 de forma perezosa; WalletError si no está."""
    try:
        from py_clob_client_v2 import (ClobClient, OrderArgs, OrderType, Side,
                                        PartialCreateOrderOptions, MarketOrderArgs)
    except ImportError as e:
        raise WalletError(
            "falta py-clob-client-v2 — instálalo con "
            "'pip install py-clob-client-v2 py-builder-relayer-client'") from e
    return (ClobClient, OrderArgs, OrderType, Side,
            PartialCreateOrderOptions, MarketOrderArgs)


def _derivar_funder(clave, sig):
    """Deriva la dirección del proxy/deposit wallet desde la clave, vía relayer.

    sig 3 -> deposit wallet · sig 1 -> proxy email/Magic · sig 2 -> Safe navegador.
    """
    if sig in _funder_cache:
        return _funder_cache[sig]
    try:
        from py_builder_relayer_client.client import RelayClient
    except ImportError as e:
        raise WalletError(
            "falta py-builder-relayer-client — instálalo con "
            "'pip install py-builder-relayer-client'") from e
    rel = RelayClient(relayer_url=RELAYER, chain_id=CHAIN_ID, private_key=clave)
    try:
        if sig == 1:
            addr = rel.get_expected_proxy_wallet()
        elif sig == 2:
            addr = rel.get_expected_safe()
        else:  # 3, deposit wallet (por defecto)
            addr = rel.get_expected_deposit_wallet()
    except Exception as e:
        raise WalletError(f"no se pudo derivar la dirección de la wallet: {e}") from e
    _funder_cache[sig] = addr
    return addr


def crear_cliente(cfg):
    """Crea (y cachea) el ClobClient v2 autenticado a partir de la config."""
    global _cliente
    if _cliente is not None:
        return _cliente

    ClobClient, _, _, _, _, _ = _importar()
    clave = config.clave_privada()
    if not clave:
        raise WalletError("no hay clave privada (configúrala en Ajustes o exporta POLY_PK)")
    clave = _normalizar_clave(clave)

    w = cfg["wallet"]
    sig = int(w.get("signature_type", FIRMA_DEPOSITO))

    if sig == 0:
        cli = ClobClient(host=HOST, chain_id=CHAIN_ID, key=clave)
    else:
        # El funder se toma de la config o, si está vacío, se deriva solo.
        funder = w.get("funder") or _derivar_funder(clave, sig)
        cli = ClobClient(host=HOST, chain_id=CHAIN_ID, key=clave,
                         signature_type=sig, funder=_validar_funder(funder))
    try:
        cli.set_api_creds(cli.derive_api_key())
    except Exception as e:
        raise WalletError(f"no se pudieron derivar las credenciales de API: {e}") from e

    _cliente = cli
    return cli


def comprar_up(cfg, token_id, precio, tamano_usdc):
    """Coloca una orden REAL de compra de UP por 'tamano_usdc' dólares de colateral.

    'precio'       : precio límite por share (0-1) = el PEOR precio aceptable.
    'tamano_usdc'  : dólares (USDC) a gastar en la compra.

    FOK se envía como ORDEN DE MERCADO (create_market_order): Polymarket exige que
    el importe en USDC sea el 'maker' (máx 2 decimales) y las shares el 'taker'
    (máx 4 decimales). Si un FOK se construye por la vía de orden límite, esos
    decimales se invierten (USDC con 4) y el CLOB lo rechaza con
    'invalid amounts ... maker amount supports a max accuracy of 2 decimals'.
    GTC se deja como orden límite normal en el libro (expresada en nº de shares).

    Lanza WalletError ante cualquier problema (config, conexión o rechazo).
    """
    (_, OrderArgs, OrderType, Side,
     PartialCreateOrderOptions, MarketOrderArgs) = _importar()
    cli = crear_cliente(cfg)
    tipo = str(cfg["wallet"].get("tipo_orden", "FOK")).upper()
    precio = round(float(precio), 4)
    try:
        tick = cli.get_tick_size(token_id)
        neg_risk = cli.get_neg_risk(token_id)
        opciones = PartialCreateOrderOptions(tick_size=str(tick), neg_risk=neg_risk)
        if tipo == "GTC":
            # Orden límite que queda en el libro: se expresa por nº de shares.
            shares = float(tamano_usdc) / precio if precio > 0 else 0.0
            orden = OrderArgs(token_id=token_id, price=precio,
                              size=shares, side=Side.BUY)
            return cli.create_and_post_order(
                orden, options=opciones, order_type=OrderType.GTC)
        # FOK = orden de mercado: se expresa por IMPORTE en USDC (maker, 2 decimales).
        orden = MarketOrderArgs(token_id=token_id,
                                amount=round(float(tamano_usdc), 2),
                                side=Side.BUY, price=precio,
                                order_type=OrderType.FOK)
        return cli.create_and_post_market_order(
            orden, options=opciones, order_type=OrderType.FOK)
    except WalletError:
        raise
    except Exception as e:
        raise WalletError(f"el CLOB rechazó la orden: {e}") from e


def balance_usdc(cfg):
    """Saldo operable (pUSD) de la wallet. Lanza WalletError si falla."""
    _importar()
    from py_clob_client_v2 import BalanceAllowanceParams, AssetType
    cli = crear_cliente(cfg)
    sig = int(cfg["wallet"].get("signature_type", FIRMA_DEPOSITO))
    try:
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=sig)
        cli.update_balance_allowance(params)   # refresca la caché on-chain
        r = cli.get_balance_allowance(params)
    except Exception as e:
        raise WalletError(f"no se pudo leer el balance: {e}") from e
    bruto = r.get("balance") if isinstance(r, dict) else None
    if bruto is None:
        raise WalletError(f"respuesta inesperada del CLOB: {r}")
    try:
        return float(bruto) / 1e6   # el colateral tiene 6 decimales
    except (TypeError, ValueError) as e:
        raise WalletError(f"balance no numérico: {bruto}") from e


def direcciones(cfg):
    """Devuelve (firmante, funder) para verificar que apuntan a lo correcto."""
    cli = crear_cliente(cfg)
    firmante = None
    try:
        firmante = cli.get_address()
    except Exception:
        pass
    w = cfg["wallet"]
    sig = int(w.get("signature_type", FIRMA_DEPOSITO))
    if sig == 0:
        funder = firmante
    else:
        funder = w.get("funder") or _funder_cache.get(sig) or "(derivando…)"
    return firmante, funder


def probar_conexion(cfg):
    """Verifica credenciales/red sin operar. Devuelve (ok, mensaje)."""
    reiniciar()
    try:
        crear_cliente(cfg)
        return True, "conexión OK · credenciales de API derivadas (CLOB v2)"
    except WalletError as e:
        return False, str(e)
    except Exception as e:
        return False, f"error inesperado: {e}"
