"""
Implementación del gancho nucleo.ganchos.usuarios_con_rol.

Vive en Django y no en el servicio autorizador: es una consulta a la base, no
una decisión de autorización, y el autorizador no tiene acceso a la base.
Se conecta en settings.VERDEAZUL_GANCHOS["usuarios_con_rol"].
"""

import logging

from django.utils import timezone

from autorizacion import roles

from .models import EstadoCuenta, RolUsuario, Usuario
from .selectores import vigentes_en

log = logging.getLogger("verdeazul.cuentas")


def usuarios_con_rol(rol: str, en=None):
    """
    QuerySet de usuarios con cuenta ACTIVA y el rol `rol` vigente en `en`
    (por defecto, ahora). Se devuelve un QuerySet sin joins duplicados para que
    los servicios puedan encadenar .filter(pk=...) y .exclude(pk__in=...).
    Un rol desconocido devuelve un QuerySet vacío (fallo cerrado).
    """
    if not roles.es_rol(rol):
        log.warning("usuarios_con_rol con rol desconocido: %r", rol)
        return Usuario.objects.none()
    con_rol = RolUsuario.objects.filter(vigentes_en(en or timezone.now()), rol=rol).values(
        "usuario_id"
    )
    return Usuario.objects.filter(pk__in=con_rol, estado=EstadoCuenta.ACTIVA, is_active=True)
