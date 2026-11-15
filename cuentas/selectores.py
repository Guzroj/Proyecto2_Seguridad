"""
Funciones de atributos de cuentas (solo lectura). Siempre leen de la base de
datos, nunca de la sesión ni de la petición (los roles no se copian en la sesión).

  roles_vigentes(s)   R_vig(s): roles con vigencia en el instante dado
  registros_sin_sincronizar(u)  decide si la baja deja la cuenta EN_BAJA (C15)
  hoy()               fecha de hoy en Costa Rica (reloj del servidor)
  ventana_vigente(s)  C5: ventana de auditoría que habilita hoy al AE, o None
"""

from django.db.models import Q
from django.utils import timezone

from .models import DIAS_MAXIMOS_VENTANA, EstadoCuenta, EstadoVentana, RolUsuario, VentanaAuditoria


def vigentes_en(instante=None) -> Q:
    """Filtro de RolUsuario vigente en `instante` (por defecto, ahora)."""
    instante = instante or timezone.now()
    return Q(vigente_desde__lte=instante) & (
        Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gt=instante)
    )


def roles_vigentes(usuario, en=None) -> set[str]:
    """R_vig(s). Acepta usuario o pk. No mira el estado de la cuenta."""
    usuario_id = getattr(usuario, "pk", usuario)
    return set(
        RolUsuario.objects.filter(vigentes_en(en), usuario_id=usuario_id).values_list("rol", flat=True)
    )


def tiene_rol(usuario, rol: str, en=None) -> bool:
    return rol in roles_vigentes(usuario, en)


def registros_sin_sincronizar(cuenta) -> bool:
    """
    C15: la baja deja la cuenta EN_BAJA (y no INACTIVA) solo si quedan registros
    de campo firmados por sus dispositivos que aún no llegan al servidor. Los
    dispositivos de campo (O7c, C18) se agregan en el paso 10; hasta entonces
    ninguna cuenta tiene registros pendientes.
    """
    return False


def hoy():
    """Fecha de hoy en TIME_ZONE (America/Costa_Rica), según el reloj del servidor."""
    return timezone.localdate()


def ventana_vigente(usuario, en=None) -> VentanaAuditoria | None:
    """
    C5: cuenta(s).estado = ACTIVA ∧ hoy ∈ [v.inicio, v.fin] ∧ (v.fin − v.inicio) ≤ 30 d
        ∧ v.aprobada_por ∈ U_GC ∧ v.aprobada_por ≠ v.solicitada_por.
    U_GC se evalúa en el momento de la aprobación (quien aprobó era GC entonces).
    `en` es un datetime (por defecto ahora); el día se toma en hora de Costa Rica.
    """
    usuario_id = getattr(usuario, "pk", usuario)
    dia = timezone.localdate(en) if en else hoy()
    v = (
        VentanaAuditoria.objects.select_related("auditor")
        .filter(auditor_id=usuario_id, estado=EstadoVentana.APROBADA, inicio__lte=dia, fin__gte=dia)
        .first()
    )
    if (
        v is None
        or v.auditor.estado != EstadoCuenta.ACTIVA
        or (v.fin - v.inicio).days > DIAS_MAXIMOS_VENTANA
        or v.aprobada_por_id in (None, v.solicitada_por_id)
        or not tiene_rol(v.aprobada_por_id, "GC", en=v.aprobada_en)
    ):
        return None
    return v
