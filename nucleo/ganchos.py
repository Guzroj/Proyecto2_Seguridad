"""
Puntos de enganche entre Persona 2 (dominio) y Personas 3 y 4.

Los servicios SIEMPRE llaman a estas funciones; qué implementación se ejecuta se
decide en settings.VERDEAZUL_GANCHOS, sin tocar el código de los servicios:

    VERDEAZUL_GANCHOS = {
        "autorizar":            "autorizacion.motor.autorizar",          # Persona 3
        "usuarios_con_rol":     "cuentas.roles.usuarios_con_rol",        # Persona 3
        "registrar_evento":     "bitacora.servicios.registrar_evento",   # Persona 4
        "on_medicion_aprobada": "certificados.servicios.emitir",         # Persona 4
    }

Mientras no se configuren, se usan las implementaciones *_provisional de abajo.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.module_loading import import_string

from .errores import AccesoDenegado

log = logging.getLogger("verdeazul.ganchos")

_POR_DEFECTO = {
    "autorizar": "nucleo.ganchos.autorizar_provisional",
    "usuarios_con_rol": "nucleo.ganchos.usuarios_con_rol_provisional",
    "registrar_evento": "nucleo.ganchos.registrar_evento_provisional",
    "on_medicion_aprobada": "nucleo.ganchos.on_medicion_aprobada_provisional",
}


def _impl(nombre: str):
    ruta = getattr(settings, "VERDEAZUL_GANCHOS", {}).get(nombre) or _POR_DEFECTO[nombre]
    return import_string(ruta)


# ===========================================================================
# Interfaz pública (estas firmas son el contrato; no cambiarlas sin avisar)
# ===========================================================================
def autorizar(usuario, accion: str, recurso=None, contexto: dict | None = None) -> None:
    """
    Persona 3. Decide si `usuario` puede ejecutar `accion` (ver nucleo.acciones.Accion)
    sobre `recurso` (instancia del modelo, ya leída de la BD y bloqueada).
    `contexto` lleva datos extra que no están en el recurso (p. ej. el período o la
    finca al CREAR, el técnico destino de una asignación).

    No devuelve nada si concede. Si niega, LANZA:
      AccesoDenegado (403), FueraDeAlcance (404) o ReautenticacionRequerida (reto MFA).
    Aquí van: rol vigente, alcance ABAC, edad del segundo factor, HBAC (H1–H3).
    Las invariantes de dominio (estados, H4, separación de funciones por historial)
    ya las aplica el servicio; repetirlas en el motor es defensa en profundidad.
    """
    _impl("autorizar")(usuario, accion, recurso, contexto or {})


def usuarios_con_rol(rol: str):
    """
    Persona 3. QuerySet de usuarios ACTIVOS con el rol vigente `rol`
    ("TC", "VF", "GC", "AC", "AE", "PF"). Lo usa el servicio para escoger
    verificador al azar y para validar asignaciones (C19).
    """
    return _impl("usuarios_con_rol")(rol)


def registrar_evento(
    *,
    actor,
    accion: str,
    recurso_tipo: str,
    recurso_id: str,
    resultado: str = "OK",
    antes: dict | None = None,
    despues: dict | None = None,
    detalle: str = "",
) -> None:
    """
    Persona 4. Agrega un evento a la bitácora. Se llama DENTRO de la transacción de
    la operación (si la operación se revierte, el evento de éxito también: patrón
    outbox). Las negaciones (resultado="DENEGADO") se registran FUERA de la
    transacción revertida, así que también quedan.
    `antes`/`despues`: fotografía de campos relevantes (sin secretos ni datos personales).
    """
    _impl("registrar_evento")(
        actor=actor,
        accion=accion,
        recurso_tipo=recurso_tipo,
        recurso_id=recurso_id,
        resultado=resultado,
        antes=antes,
        despues=despues,
        detalle=detalle,
    )


def on_medicion_aprobada(medicion) -> None:
    """
    Persona 4. Se llama DENTRO de la transacción de aprobación, con la medición ya en
    APROBADA y bloqueada (select_for_update). Aquí se emite y firma el certificado.
    Si `medicion.version_de` no es None, es una versión enmendada: aplicar P2
    (si cambian las toneladas, revocar el vigente y emitir otro que lo referencie).
    Si esta función lanza una excepción, la aprobación completa se revierte.
    """
    _impl("on_medicion_aprobada")(medicion)


# ===========================================================================
# Implementaciones provisionales (hasta que Personas 3 y 4 conecten las suyas)
# ===========================================================================
def autorizar_provisional(usuario, accion, recurso, contexto) -> None:
    # Fallo cerrado (RG5): sin motor de autorización, se niega... salvo que se
    # active explícitamente el modo permisivo de desarrollo.
    if getattr(settings, "AUTORIZACION_PROVISIONAL_PERMISIVA", False):
        log.warning("AUTORIZACIÓN PROVISIONAL (permisiva): %s %s", usuario, accion)
        return
    raise AccesoDenegado("Motor de autorización no configurado: se niega por defecto.")


def usuarios_con_rol_provisional(rol: str):
    # Provisional: el rol es un Group de Django con el nombre de la sigla.
    return get_user_model().objects.filter(groups__name=rol, is_active=True)


def registrar_evento_provisional(**evento) -> None:
    log.info("EVENTO %s", evento)


def on_medicion_aprobada_provisional(medicion) -> None:
    log.info("Medición %s aprobada: aquí Persona 4 emite el certificado.", medicion.pk)
