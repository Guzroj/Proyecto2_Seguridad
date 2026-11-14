"""
Control de sesión.

  - Inactividad máxima: 15 min para GC, AC y AE; 30 min para TC, VF y PF. Con
    varios roles se aplica la más estricta; sin roles, 15 min (fallo cerrado).
  - Máximo absoluto: 10 h desde el ingreso, aunque haya actividad.
  - Los roles NO se copian en la sesión: se leen de la base en cada petición.
  - Toda petición autenticada exige una fila abierta en cuentas.Sesion; cerrar
    las sesiones de un usuario es cerrar esas filas (cerrar_sesiones).

Los tiempos son del servidor y se guardan en la base, no en la cookie.
"""

import hashlib
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import logout
from django.db import transaction
from django.utils import timezone

from autorizacion import roles

from . import rls
from .models import MotivoCierre, Sesion
from .selectores import roles_vigentes

log = logging.getLogger("verdeazul.cuentas")

INACTIVIDAD_POR_ROL = {
    roles.GC: timedelta(minutes=15),
    roles.AC: timedelta(minutes=15),
    roles.AE: timedelta(minutes=15),
    roles.TC: timedelta(minutes=30),
    roles.VF: timedelta(minutes=30),
    roles.PF: timedelta(minutes=30),
}
INACTIVIDAD_SIN_ROL = timedelta(minutes=15)
MAXIMO_ABSOLUTO = timedelta(hours=10)


def limite_inactividad(roles_del_usuario) -> timedelta:
    limites = [INACTIVIDAD_POR_ROL[r] for r in roles_del_usuario if r in INACTIVIDAD_POR_ROL]
    return min(limites, default=INACTIVIDAD_SIN_ROL)


def hash_clave(clave_sesion: str) -> str:
    return hashlib.sha256(clave_sesion.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Registro de sesiones
# ---------------------------------------------------------------------------
def registrar_inicio(usuario, clave_sesion: str) -> Sesion:
    ahora = timezone.now()
    return Sesion.objects.create(
        usuario=usuario, clave_hash=hash_clave(clave_sesion), iniciada_en=ahora, ultima_actividad=ahora
    )


def cerrar_registro(clave_sesion: str | None, motivo: str) -> int:
    if not clave_sesion:
        return 0
    return Sesion.objects.filter(clave_hash=hash_clave(clave_sesion), cerrada_en__isnull=True).update(
        cerrada_en=timezone.now(), motivo_cierre=motivo
    )


def renovar_clave(clave_anterior: str, clave_nueva: str) -> int:
    """Tras renovar el identificador (p. ej. al reautenticarse), la fila sigue a la sesión."""
    return Sesion.objects.filter(clave_hash=hash_clave(clave_anterior), cerrada_en__isnull=True).update(
        clave_hash=hash_clave(clave_nueva)
    )


def cerrar_sesiones(usuario, motivo: str = MotivoCierre.FORZADO) -> int:
    """
    Cierra TODAS las sesiones abiertas de `usuario` (baja o cambio de privilegios).
    Pieza de dominio: la autorización la hace el servicio que la llama. La
    siguiente petición de cada sesión cerrada llega como anónima.
    """
    usuario_id = getattr(usuario, "pk", usuario)
    with transaction.atomic():
        return Sesion.objects.filter(usuario_id=usuario_id, cerrada_en__isnull=True).update(
            cerrada_en=timezone.now(), motivo_cierre=motivo
        )


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MENSAJES = {
    MotivoCierre.INACTIVIDAD: "Tu sesión se cerró por inactividad.",
    MotivoCierre.MAXIMO: "Tu sesión alcanzó el máximo de 10 horas.",
    MotivoCierre.FORZADO: "Tu sesión fue cerrada.",
    MotivoCierre.INVALIDA: "Tu sesión fue cerrada.",
}


class ControlSesionMiddleware:
    """Va después de ContextoRLSMiddleware (necesita al sujeto para leer sus roles y sesiones)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            motivo = self._revisar(request)
            if motivo:
                self._expulsar(request, motivo)
        return self.get_response(request)

    def _revisar(self, request) -> str | None:
        clave = request.session.session_key
        ahora = timezone.now()
        registro = (
            Sesion.objects.filter(usuario=request.user, clave_hash=hash_clave(clave or ""))
            .order_by("-iniciada_en")
            .first()
        )
        if registro is None:
            return MotivoCierre.INVALIDA
        if registro.cerrada_en is not None:
            return registro.motivo_cierre or MotivoCierre.INVALIDA
        if ahora - registro.iniciada_en > MAXIMO_ABSOLUTO:
            return MotivoCierre.MAXIMO
        if ahora - registro.ultima_actividad > limite_inactividad(roles_vigentes(request.user)):
            return MotivoCierre.INACTIVIDAD
        Sesion.objects.filter(pk=registro.pk).update(ultima_actividad=ahora)
        return None

    def _expulsar(self, request, motivo: str) -> None:
        cerrar_registro(request.session.session_key, motivo)
        logout(request)
        rls.fijar_contexto()
        messages.info(request, MENSAJES.get(motivo, MENSAJES[MotivoCierre.INVALIDA]))
