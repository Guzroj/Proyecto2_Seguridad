"""
Operaciones sobre cuentas (O7a). ÚNICA vía para cambiar el estado de una cuenta.

`cambiar_estado` es la pieza de dominio: valida la transición, relee y bloquea
la cuenta y fija el nuevo estado. No autoriza ni registra el evento: lo hacen
los servicios que la usan (alta, baja, activación de ventanas de auditoría,
tarea diaria), porque cada uno tiene su propio actor y su propia celda de la
matriz.
"""

from django.db import transaction
from django.utils import timezone

from nucleo.errores import TransicionInvalida
from nucleo.transicion import bloquear

from .models import EstadoCuenta, Usuario

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
