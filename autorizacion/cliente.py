"""
Cliente HTTP del motor de autorización: implementación real del gancho
nucleo.ganchos.autorizar (se activa en settings.VERDEAZUL_GANCHOS).

Django (PEP) arma los atributos con autorizacion.pep, los manda al servicio
autorizador (PDP, sin acceso a la base) y traduce su decisión a las excepciones
de nucleo.errores:

    CONCEDE           -> no lanza nada
    NIEGA             -> AccesoDenegado (403)
    FUERA_DE_ALCANCE  -> FueraDeAlcance (404, RG3)
    REAUTENTICAR      -> ReautenticacionRequerida (reto de segundo factor, RG6)

Regla de oro (RG5): cualquier otra cosa (URL sin configurar, motor caído,
timeout, respuesta rara) es NIEGA. Solo se concede con un 200 cuyo resultado es
exactamente "CONCEDE".

Autenticación hacia el motor (AUTORIZADOR_AUTENTICACION):
  - "oidc" (por defecto): token de identidad del metadata server de Cloud Run,
    con la URL del motor como audiencia.
  - "ninguna": solo en desarrollo (DEBUG); con DEBUG apagado se niega todo.

Después de cada decisión, cuentas.hbac anota la actividad concedida (contadores
de H1 y H3) y crea las alertas que pidió el motor (H1–H3). Si no puede anotar
una concesión, se niega.

usuarios_con_rol ya no pasa por el motor: lo resuelve Django leyendo la base
(cuentas.roles.usuarios_con_rol), porque el motor no tiene acceso a ella.
"""

import logging
import os

import httpx
from django.conf import settings

from nucleo.errores import AccesoDenegado, FueraDeAlcance, ReautenticacionRequerida

from . import pep

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
        return decision
    if resultado == "FUERA_DE_ALCANCE":
        raise FueraDeAlcance("No encontrado.")
    if resultado == "REAUTENTICAR":
        raise ReautenticacionRequerida(motivo)
    raise AccesoDenegado(motivo)


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
