"""
Bitácora de auditoría (O8): implementación del gancho registrar_evento.

    VERDEAZUL_GANCHOS["registrar_evento"] = "bitacora.servicios.registrar_evento"

Contrato (nucleo/ganchos.py y README §3.2):
  - Los eventos de un cambio (resultado "OK") se insertan DENTRO de la transacción de la
    operación: si la operación se revierte, el evento también (no queda constancia de algo
    que no pasó). Si el evento no se puede insertar, la excepción revierte la operación
    (RG4: si no se puede registrar, no se hace).
  - Las negaciones (resultado "DENEGADO") llegan desde nucleo.transicion ya FUERA de la
    transacción revertida y se insertan en el acto. Si aun así llegan dentro de una
    transacción ajena durante una petición web, se dejan pendientes y
    BitacoraPendientesMiddleware las inserta al terminar la petición, fuera de ella: un
    rollback nunca borra un rechazo.
  - Las lecturas concedidas de O1, O4, O6 y O8 (RG4) las registra el PEP con
    resultado "CONCEDIDO" (autorizacion.cliente).

Minimización (RG2, Ley 8968): antes/despues/detalle se limpian de claves sensibles
(contraseñas, secretos, tokens, códigos de un solo uso o de recuperación, llaves privadas,
identificación y contacto del propietario) y se recortan. La secuencia, la hora y el encadenamiento los fija la base (migración 0002).
"""

import ipaddress
import logging
import re
from contextvars import ContextVar

from django.db import connection

from .models import Evento, Resultado

log = logging.getLogger("verdeazul.bitacora")

# Claves que nunca se guardan (se reemplazan por "[omitido]"), en cualquier nivel.
SENSIBLE = re.compile(
    r"(pass|contrase|^clave$|clave_(privada|secreta|hash)|hash_clave|secret|token|totp|\botp|c[oó]digos?_|"
    r"recupera|privad|cookie|session|sesion_clave|identificacion|cedula|telefono|correo|email)",
    re.IGNORECASE,
)
MAX_TEXTO = 300
MAX_DETALLE = 1000
MAX_ELEMENTOS = 50
MAX_PROFUNDIDAD = 4
OMITIDO = "[omitido]"

_pendientes: ContextVar = ContextVar("verdeazul_bitacora_pendientes", default=None)


def limpiar(valor, profundidad: int = 0):
    """JSON acotado y sin secretos: dicts y listas recortados, textos cortos, claves sensibles fuera."""
    if profundidad > MAX_PROFUNDIDAD:
        return "[...]"
    if valor is None or isinstance(valor, (bool, int)):
        return valor
    if isinstance(valor, float):
        return valor if valor == valor and abs(valor) != float("inf") else str(valor)
    if isinstance(valor, str):
        return valor[:MAX_TEXTO]
    if isinstance(valor, dict):
        limpio = {}
        for clave, v in list(valor.items())[:MAX_ELEMENTOS]:
            clave = str(clave)[:64]
            limpio[clave] = OMITIDO if SENSIBLE.search(clave) else limpiar(v, profundidad + 1)
        return limpio
    if isinstance(valor, (list, tuple, set, frozenset)):
        return [limpiar(v, profundidad + 1) for v in list(valor)[:MAX_ELEMENTOS]]
    if hasattr(valor, "pk") and hasattr(valor, "_meta"):
        return {"tipo": valor._meta.model_name, "id": str(valor.pk)}
    return str(valor)[:MAX_TEXTO]


def _texto(valor, maximo: int) -> str:
    return str(valor or "")[:maximo]


def ip_de(request) -> str:
    """
    IP del cliente: la ÚLTIMA de X-Forwarded-For, que agrega el frente de Cloud Run (las
    anteriores las puede inventar el cliente), o REMOTE_ADDR. "" si no es una IP válida.
    """
    meta = getattr(request, "META", None)
    if not isinstance(meta, dict):
        return ""
    reenviada = meta.get("HTTP_X_FORWARDED_FOR", "")
    candidato = reenviada.split(",")[-1].strip() if reenviada else meta.get("REMOTE_ADDR", "")
    try:
        return str(ipaddress.ip_address(candidato))
    except ValueError:
        return ""


def _origen() -> str:
    """IP de la petición en curso, si la hay."""
    from autorizacion.pep import peticion_actual

    return ip_de(peticion_actual.get())


def _roles(actor_id) -> list[str]:
    if actor_id is None:
        return []
    try:
        from cuentas.selectores import roles_vigentes

        return sorted(roles_vigentes(actor_id))
    except Exception:  # los roles son contexto, no condición para registrar
        return []


def _fila(*, actor, accion, recurso_tipo, recurso_id, resultado, antes, despues, detalle) -> dict:
    if resultado not in Resultado.values:
        resultado = Resultado.DENEGADO if str(resultado).upper().startswith("DEN") else Resultado.OK
    actor_id = getattr(actor, "pk", actor) if actor is not None else None
    if actor_id is not None and not str(actor_id).isdigit():
        actor_id = None
    nombre = actor.get_username() if hasattr(actor, "get_username") else ""
    return {
        "actor_id": int(actor_id) if actor_id is not None else None,
        "actor_nombre": _texto(nombre, 150),
        "roles": _roles(actor_id),
        "accion": _texto(accion, 64),
        "recurso_tipo": _texto(recurso_tipo, 64),
        "recurso_id": _texto(recurso_id, 64),
        "resultado": resultado,
        "antes": limpiar(antes) if antes is not None else None,
        "despues": limpiar(despues) if despues is not None else None,
        "detalle": _texto(detalle, MAX_DETALLE),
        "origen": _origen(),
    }


def _insertar(fila: dict) -> Evento:
    # Sin RETURNING (la clave es un UUID de la aplicación): quien escribe no necesita poder leer.
    return Evento.objects.create(**fila)


def registrar_evento(*, actor, accion, recurso_tipo, recurso_id, resultado="OK", antes=None, despues=None,
                     detalle="") -> None:
    fila = _fila(actor=actor, accion=accion, recurso_tipo=recurso_tipo, recurso_id=recurso_id,
                 resultado=resultado, antes=antes, despues=despues, detalle=detalle)
    if fila["resultado"] != Resultado.DENEGADO:
        _insertar(fila)  # dentro de la transacción de la operación: si falla, la operación se revierte
        return
    pendientes = _pendientes.get()
    if pendientes is not None and connection.in_atomic_block:
        pendientes.append(fila)  # la transacción en curso puede revertirse: se inserta al terminar
        return
    try:
        _insertar(fila)
    except Exception:
        # La operación ya se negó; no se oculta esa negación con otro error, pero se deja rastro.
        log.exception("No se pudo registrar en la bitácora la negación de %s.", fila["accion"])


def iniciar_pendientes() -> list:
    lista: list = []
    _pendientes.set(lista)
    return lista


def insertar_pendientes(lista: list) -> None:
    """Inserta las negaciones que quedaron dentro de una transacción (ya terminada)."""
    try:
        while lista:
            fila = lista.pop(0)
            try:
                _insertar(fila)
            except Exception:
                log.exception("No se pudo registrar en la bitácora la negación de %s.", fila["accion"])
    finally:
        _pendientes.set(None)


class BitacoraPendientesMiddleware:
    """
    Va después de ContextoRLSMiddleware (el sujeto de RLS sigue fijado al insertar) y de
    AuthenticationMiddleware. Al terminar la petición, fuera de toda transacción, inserta
    las negaciones que quedaron pendientes.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        pendientes = iniciar_pendientes()
        try:
            return self.get_response(request)
        finally:
            insertar_pendientes(pendientes)


def verificar_cadena() -> list[tuple[int, str]]:
    """Problemas de integridad de la cadena (vacío = íntegra). Corre con los permisos de quien llama."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT secuencia, problema FROM bitacora_verificar()")
        return [(fila[0], fila[1]) for fila in cursor.fetchall()]
