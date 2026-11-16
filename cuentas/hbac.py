"""
Efectos de los controles HBAC del lado Django (PEP), después de que el motor decide.

  - Lectura concedida de O1/O4: se anota para n(s) y μ(s) de H1.
  - Exportación concedida: se anota con sus registros para N(s) de H3.
  - Alertas que pide el motor (H1 al negar; H2 y H3 al conceder): se crean en
    O9 con services.crear_alerta, que también las deja en la bitácora con el
    detalle (H2 eleva el detalle de bitácora).

Una negación H1 normalmente revierte la transacción en curso; si la alerta se
creara dentro, se perdería. Por eso, dentro de una transacción y durante una
petición, la alerta queda pendiente y PeticionActualMiddleware la crea al
terminar la petición, fuera de la transacción (como el registro de negaciones).
Fuera de una transacción, o sin petición (consola, tareas), se crea en el acto.

Si no se puede anotar una concesión, se niega (RG4: si no se puede registrar,
no se concede).

H3: toda exportación lleva marca de agua con usuario y fecha; quien arma el
archivo la toma de marca_de_agua().
"""

import logging
from contextvars import ContextVar

from django.db import connection
from django.utils import timezone

from autorizacion import hbac, matriz
from nucleo.errores import AccesoDenegado

from . import services
from .models import ControlHBAC, TipoActividad

log = logging.getLogger("verdeazul.hbac")

_pendientes: ContextVar = ContextVar("verdeazul_alertas_pendientes", default=None)


def iniciar_pendientes() -> list:
    lista: list = []
    _pendientes.set(lista)
    return lista


def crear_pendientes(lista: list) -> None:
    """Crea las alertas pendientes de la petición (fuera de su transacción)."""
    while lista:
        sujeto_id, control, detalle = lista.pop(0)
        try:
            services.crear_alerta(sujeto_id, control, detalle=detalle)
        except Exception:
            log.exception("No se pudo crear la alerta %s del sujeto %s.", control, sujeto_id)
    _pendientes.set(None)


def _alertas(decision: dict) -> list[tuple[str, dict]]:
    """Solo alertas bien formadas del motor; cualquier otra cosa se ignora."""
    validas = []
    for alerta in decision.get("alertas") or []:
        if not isinstance(alerta, dict):
            continue
        control, detalle = alerta.get("control"), alerta.get("detalle") or {}
        if control in ControlHBAC.values and isinstance(detalle, dict):
            validas.append((control, detalle))
    return validas


def aplicar(usuario, accion: str, solicitud: dict, decision: dict) -> None:
    """Anota la actividad concedida y crea (o deja pendientes) las alertas del motor."""
    sujeto_id = solicitud["sujeto"]["id"]
    concede = decision.get("resultado") == "CONCEDE"
    objeto, operacion = matriz.ACCIONES.get(accion, (None, None))
    if concede and operacion == "L" and objeto in hbac.LECTURAS_H1:
        services.registrar_actividad(sujeto_id, TipoActividad.LECTURA, objeto)
    elif concede and operacion == "X":
        registros = (solicitud.get("contexto") or {}).get("registros")
        services.registrar_actividad(sujeto_id, TipoActividad.EXPORTACION, objeto, registros=registros)

    pendientes = _pendientes.get()
    for control, detalle in _alertas(decision):
        if pendientes is not None and connection.in_atomic_block:
            pendientes.append((sujeto_id, control, detalle))
        else:
            services.crear_alerta(sujeto_id, control, detalle=detalle)


def aplicar_o_negar(usuario, accion: str, solicitud: dict, decision: dict) -> None:
    try:
        aplicar(usuario, accion, solicitud, decision)
    except Exception as exc:
        log.warning("No se pudo registrar el control HBAC de %s: %s", accion, exc)
        if decision.get("resultado") == "CONCEDE":
            raise AccesoDenegado("No se pudo registrar el control de comportamiento: se niega.") from exc


def marca_de_agua(usuario, momento=None) -> str:
    """Texto de la marca de agua de una exportación (H3): cuenta nominal y fecha de Costa Rica."""
    momento = timezone.localtime(momento or timezone.now())
    return f"Exportado por {usuario.get_username()} (cuenta {usuario.pk}) el {momento:%Y-%m-%d %H:%M} (hora de Costa Rica)"
