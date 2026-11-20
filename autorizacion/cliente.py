import logging
import os

import httpx
from django.conf import settings

from nucleo import ganchos
from nucleo.errores import AccesoDenegado, FueraDeAlcance, ReautenticacionRequerida

from . import matriz, pep

log = logging.getLogger("verdeazul.autorizacion")

URL_IDENTIDAD = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/identity"
)
# Una autorización colgada bloquea la operación: se corta pronto y se niega.
TIEMPO_ESPERA = 3.0


def _url_motor() -> str:
    return os.environ.get("AUTORIZADOR_URL", "").strip().rstrip("/")


def _modo_autenticacion() -> str:
    return os.environ.get("AUTORIZADOR_AUTENTICACION", "oidc").strip().lower()


def _http() -> httpx.Client:
    """Punto único de salida HTTP (las pruebas lo sustituyen por un transporte falso)."""
    return httpx.Client(timeout=TIEMPO_ESPERA, follow_redirects=False)


def _encabezados(cliente: httpx.Client, base: str) -> dict:
    modo = _modo_autenticacion()
    if modo == "ninguna":
        if not settings.DEBUG:
            raise AccesoDenegado("Motor sin autenticación fuera de desarrollo: se niega.")
        return {}
    if modo != "oidc":
        raise AccesoDenegado("AUTORIZADOR_AUTENTICACION desconocida: se niega.")
    respuesta = cliente.get(URL_IDENTIDAD, params={"audience": base}, headers={"Metadata-Flavor": "Google"})
    respuesta.raise_for_status()
    return {"Authorization": f"Bearer {respuesta.text.strip()}"}


def consultar(solicitud: dict) -> dict:
    """Devuelve la decisión del motor como dict; ante cualquier falla, una negación."""
    base = _url_motor()
    if not base:
        return {"resultado": "NIEGA", "regla": "RG5", "motivo": "AUTORIZADOR_URL no está configurada."}
    try:
        with _http() as cliente:
            encabezados = _encabezados(cliente, base)
            respuesta = cliente.post(f"{base}/autorizar", json=solicitud, headers=encabezados)
        if respuesta.status_code != 200:
            raise ValueError(f"el motor respondió {respuesta.status_code}")
        decision = respuesta.json()
        if not isinstance(decision, dict) or not isinstance(decision.get("resultado"), str):
            raise ValueError("respuesta sin resultado")
        return decision
    except AccesoDenegado as exc:
        return {"resultado": "NIEGA", "regla": "RG5", "motivo": str(exc)}
    except (httpx.HTTPError, OSError, ValueError) as exc:
        log.warning("Motor de autorización no disponible o respuesta inválida: %s", exc)
        return {"resultado": "NIEGA", "regla": "RG5", "motivo": "Motor de autorización no disponible."}


def _decidir(usuario, accion: str, recurso=None, contexto: dict | None = None) -> dict:
    """Pregunta al motor, aplica los efectos HBAC y traduce la negación a su excepción."""
    from cuentas import hbac

    try:
        solicitud = pep.construir_solicitud(usuario, accion, recurso, contexto)
    except Exception as exc:  # RG5: sin atributos (p. ej., RLS oculta una fila relacionada) no se decide
        log.warning("El PEP no pudo leer los atributos de %s: %s", accion, type(exc).__name__)
        raise AccesoDenegado("No se pudieron leer los atributos: se niega.") from exc
    decision = consultar(solicitud)
    hbac.aplicar_o_negar(usuario, accion, solicitud, decision)
    resultado = decision.get("resultado")
    motivo = str(decision.get("motivo") or "Acceso denegado.")[:300]
    if resultado == "CONCEDE":
        _registrar_lectura(usuario, accion, recurso)
        return decision
    if resultado == "FUERA_DE_ALCANCE":
        raise FueraDeAlcance("No encontrado.")
    if resultado == "REAUTENTICAR":
        raise ReautenticacionRequerida(motivo)
    raise AccesoDenegado(motivo)


# RG4: toda lectura concedida de O1, O4, O6 y O8 se registra en la bitácora; si el evento no
# puede registrarse, la lectura se niega. (Las escrituras las registran los servicios, las
# exportaciones su propio servicio y las negaciones nucleo.transicion y autorizar_lectura.)
LECTURAS_RG4 = frozenset({matriz.O1A, matriz.O1B, matriz.O4, matriz.O6, matriz.O8})


def _registrar_lectura(usuario, accion: str, recurso) -> None:
    objeto, operacion = matriz.ACCIONES.get(accion, (None, None))
    if operacion != "L" or objeto not in LECTURAS_RG4:
        return
    es_modelo = hasattr(recurso, "pk") and hasattr(recurso, "_meta")
    try:
        ganchos.registrar_evento(
            actor=usuario, accion=accion,
            recurso_tipo=recurso._meta.model_name if es_modelo else accion.split(":")[0],
            recurso_id=str(recurso.pk) if es_modelo else "", resultado="CONCEDIDO",
            detalle="lista" if accion.endswith(":listar") else "",
        )
    except Exception as exc:
        log.warning("No se pudo registrar la lectura %s en la bitácora: %s", accion, type(exc).__name__)
        raise AccesoDenegado("No se pudo registrar la lectura en la bitácora: se niega (RG4).") from exc


def autorizar(usuario, accion: str, recurso=None, contexto: dict | None = None) -> None:
    """Gancho nucleo.ganchos.autorizar. No devuelve nada si concede; si no, lanza."""
    _decidir(usuario, accion, recurso, contexto)


def autorizar_lista(usuario, accion: str) -> list[str]:
    """
    "<objeto>:listar": si concede, devuelve los roles con los que el sujeto puede
    listar (el PEP filtra la consulta con el alcance de cada uno). Si no, lanza.
    """
    decision = _decidir(usuario, accion)
    roles = decision.get("roles")
    if not isinstance(roles, list) or not roles or not all(isinstance(r, str) for r in roles):
        raise AccesoDenegado("Respuesta del motor sin roles para la lista: se niega.")
    return roles
