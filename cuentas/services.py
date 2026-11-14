"""
Operaciones sobre cuentas (O7a) y roles (O7b). ÚNICA vía para cambiar el estado
de una cuenta o sus roles.

`cambiar_estado` es la pieza de dominio: valida la transición, relee y bloquea
la cuenta y fija el nuevo estado. No autoriza ni registra el evento: lo hacen
los servicios que la usan (alta, baja, activación de ventanas de auditoría,
tarea diaria), porque cada uno tiene su propio actor y su propia celda de la
matriz.

`agregar_rol` y `terminar_rol` son las piezas de dominio de O7b con el mismo
criterio: aplican las invariantes que valen siempre (rol conocido, sin
duplicados, pares excluyentes E) y dejan la autorización, la doble aprobación
de GC/AE y el cierre de sesiones a los servicios de AR/RR (C7).
"""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from autorizacion import roles
from nucleo.errores import DatoInvalido, SeparacionDeFunciones, TransicionInvalida
from nucleo.transicion import bloquear

from .models import EstadoCuenta, RolUsuario, Usuario

TRANSICIONES: dict[str, frozenset[str]] = {
    EstadoCuenta.PENDIENTE: frozenset({EstadoCuenta.ACTIVA, EstadoCuenta.INACTIVA}),
    EstadoCuenta.ACTIVA: frozenset({EstadoCuenta.EN_BAJA, EstadoCuenta.INACTIVA}),
    EstadoCuenta.EN_BAJA: frozenset({EstadoCuenta.INACTIVA}),
    EstadoCuenta.INACTIVA: frozenset(),
}


def transicion_permitida(actual: str, nuevo: str) -> bool:
    return nuevo in TRANSICIONES.get(actual, frozenset())


def cambiar_estado(cuenta, nuevo: str) -> tuple[Usuario, str]:
    """
    Pasa `cuenta` (objeto o pk) al estado `nuevo`. Devuelve (cuenta, estado_anterior).
    Lanza FueraDeAlcance si no existe y TransicionInvalida si el paso no está permitido.
    """
    if nuevo not in EstadoCuenta.values:
        raise TransicionInvalida(f"Estado de cuenta desconocido: {nuevo!r}.")
    with transaction.atomic():
        cuenta = bloquear(Usuario, cuenta)
        anterior = cuenta.estado
        if not transicion_permitida(anterior, nuevo):
            raise TransicionInvalida(f"Una cuenta {anterior} no pasa a {nuevo}.")
        cuenta.estado = nuevo
        cuenta.estado_desde = timezone.now()
        cuenta.save(update_fields=["estado", "estado_desde"])
    return cuenta, anterior


# ===========================================================================
# O7b · Roles con vigencia y pares excluyentes (E)
# ===========================================================================
def _traslapadas(usuario_id, desde, hasta):
    """Pertenencias del usuario cuya vigencia se cruza con [desde, hasta)."""
    q = RolUsuario.objects.filter(usuario_id=usuario_id).filter(
        Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gt=desde)
    )
    if hasta is not None:
        q = q.filter(vigente_desde__lt=hasta)
    return q


def agregar_rol(usuario, rol: str, *, desde=None, hasta=None) -> RolUsuario:
    """
    Da a `usuario` el rol `rol` durante [desde, hasta) (por defecto desde ahora y sin fin).
    Niega si ya tiene ese rol en un tramo que se cruza, o si tiene un rol excluyente
    con vigencia que se cruza (E), sea vigente hoy o futura.
    """
    if not roles.es_rol(rol):
        raise DatoInvalido(f"Rol desconocido: {rol!r}.")
    desde = desde or timezone.now()
    if hasta is not None and hasta <= desde:
        raise DatoInvalido("La vigencia del rol termina antes de empezar.")
    with transaction.atomic():
        # Bloquear la cuenta serializa las asignaciones concurrentes al mismo usuario:
        # sin esto, dos peticiones simultáneas podrían dar TC y VF a la vez.
        usuario = bloquear(Usuario, usuario)
        cruzados = set(_traslapadas(usuario.pk, desde, hasta).values_list("rol", flat=True))
        if rol in cruzados:
            raise TransicionInvalida(f"El usuario ya tiene el rol {rol} en ese período.")
        choque = roles.conflictos(rol, cruzados)
        if choque:
            raise SeparacionDeFunciones(
                f"El rol {rol} es excluyente con {', '.join(sorted(choque))}."
            )
        return RolUsuario.objects.create(
            usuario=usuario, rol=rol, vigente_desde=desde, vigente_hasta=hasta
        )


def terminar_rol(usuario, rol: str, *, en=None) -> RolUsuario:
    """Cierra en `en` (por defecto ahora) la vigencia del rol que está vigente en ese instante."""
    if not roles.es_rol(rol):
        raise DatoInvalido(f"Rol desconocido: {rol!r}.")
    en = en or timezone.now()
    with transaction.atomic():
        usuario = bloquear(Usuario, usuario)
        pertenencia = (
            RolUsuario.objects.select_for_update()
            .filter(usuario=usuario, rol=rol, vigente_desde__lt=en)
            .filter(Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gt=en))
            .first()
        )
        if pertenencia is None:
            raise TransicionInvalida(f"El usuario no tiene el rol {rol} vigente.")
        pertenencia.vigente_hasta = en
        pertenencia.save(update_fields=["vigente_hasta"])
        return pertenencia
