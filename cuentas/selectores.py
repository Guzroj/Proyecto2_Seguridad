"""
Funciones de atributos de cuentas (solo lectura). Siempre leen de la base de
datos, nunca de la sesión ni de la petición (los roles no se copian en la sesión).

  roles_vigentes(s)   R_vig(s): roles con vigencia en el instante dado
  registros_sin_sincronizar(u)  decide si la baja deja la cuenta EN_BAJA (C15)
  hoy()               fecha de hoy en Costa Rica (reloj del servidor)
  ventana_vigente(s)  C5: ventana de auditoría que habilita hoy al AE, o None
  metadatos_factores(u)  O7d L (metadatos): tipo y fechas de los factores, sin secretos
  suspendido_h1(s)    H1: hay una alerta H1 abierta (lecturas de O1/O4 suspendidas)
  ultima_resolucion_h1(s)  H1: cuándo se levantó la última suspensión (exige mfa después)
"""

from django.db.models import Q
from django.utils import timezone

from .models import (
    DIAS_MAXIMOS_VENTANA,
    Alerta,
    ControlHBAC,
    EstadoAlerta,
    DispositivoCampo,
    EstadoCuenta,
    EstadoDispositivo,
    EstadoVentana,
    RolUsuario,
    VentanaAuditoria,
)


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


def dispositivos_activos(cuenta):
    """Dispositivos de campo ACTIVO de la cuenta (O7c)."""
    return DispositivoCampo.objects.filter(responsable_id=getattr(cuenta, "pk", cuenta), estado=EstadoDispositivo.ACTIVO)


def registros_sin_sincronizar(cuenta) -> bool:
    """
    C15: la baja deja la cuenta EN_BAJA (y no INACTIVA) si pueden quedar
    registros de campo firmados que aún no llegan al servidor. El servidor no ve
    lo que guarda el dispositivo sin conexión, así que la regla es conservadora:
    hay registros pendientes mientras la cuenta tenga algún dispositivo ACTIVO.
    La sincronización final (C16) revoca sus dispositivos y la pasa a INACTIVA.
    """
    return dispositivos_activos(cuenta).exists()


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


def metadatos_factores(usuario) -> list[dict]:
    """
    AC·O7d L (metadatos) y AE·O7d L: qué factores tiene la cuenta y cuándo se
    crearon y usaron. Nunca el campo `data` (secretos cifrados, RG2).
    """
    from allauth.mfa.models import Authenticator

    return [
        {"tipo": tipo, "creado_en": creado, "usado_en": usado}
        for tipo, creado, usado in Authenticator.objects.filter(user_id=getattr(usuario, "pk", usuario))
        .order_by("created_at")
        .values_list("type", "created_at", "last_used_at")
    ]


def suspendido_h1(usuario) -> bool:
    """H1: mientras haya una alerta H1 ABIERTA, se niegan las lecturas de O1 y O4 del sujeto."""
    return Alerta.objects.filter(
        sujeto_id=getattr(usuario, "pk", usuario), control=ControlHBAC.H1, estado=EstadoAlerta.ABIERTA
    ).exists()


def ultima_resolucion_h1(usuario):
    """Momento en que se resolvió la última alerta H1 del sujeto (o None)."""
    return (
        Alerta.objects.filter(
            sujeto_id=getattr(usuario, "pk", usuario), control=ControlHBAC.H1, estado=EstadoAlerta.RESUELTA
        )
        .order_by("-resuelta_en")
        .values_list("resuelta_en", flat=True)
        .first()
    )
