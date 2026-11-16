"""
Control de sesión.

  - Inactividad máxima: 15 min para GC, AC y AE; 30 min para TC, VF y PF. Con
    varios roles se aplica la más estricta; sin roles, 15 min (fallo cerrado).
  - Máximo absoluto: 10 h desde el ingreso, aunque haya actividad.
  - Los roles NO se copian en la sesión: se leen de la base en cada petición.
  - Toda petición autenticada exige una fila abierta en cuentas.Sesion; cerrar
    las sesiones de un usuario es cerrar esas filas (cerrar_sesiones).
  - Máximo 2 sesiones simultáneas por usuario (navegador y PWA): al abrir una
    tercera se cierra la más antigua con motivo FORZADO.
  - Al cerrar sesión (voluntaria o forzada por el control) la respuesta lleva
    Clear-Site-Data para que el navegador borre caché, cookies y almacenamiento.

Los tiempos son del servidor y se guardan en la base, no en la cookie.
"""

import hashlib
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import logout
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.csrf import csrf_failure

from autorizacion import roles

from . import rls
from .models import MotivoCierre, Sesion, Usuario
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
MAX_SESIONES_SIMULTANEAS = 2
CLEAR_SITE_DATA = '"cache", "cookies", "storage"'


def limite_inactividad(roles_del_usuario) -> timedelta:
    limites = [INACTIVIDAD_POR_ROL[r] for r in roles_del_usuario if r in INACTIVIDAD_POR_ROL]
    return min(limites, default=INACTIVIDAD_SIN_ROL)


def hash_clave(clave_sesion: str) -> str:
    return hashlib.sha256(clave_sesion.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Registro de sesiones
# ---------------------------------------------------------------------------
def registrar_inicio(usuario, clave_sesion: str) -> Sesion:
    """Registra la sesión nueva y cierra las más antiguas por encima del máximo simultáneo."""
    with transaction.atomic():
        # Bloquear la cuenta serializa ingresos simultáneos del mismo usuario.
        Usuario.objects.select_for_update().filter(pk=usuario.pk).first()
        ahora = timezone.now()
        nueva = Sesion.objects.create(
            usuario=usuario, clave_hash=hash_clave(clave_sesion), iniciada_en=ahora, ultima_actividad=ahora
        )
        sobrantes = list(
            Sesion.objects.filter(usuario=usuario, cerrada_en__isnull=True)
            .order_by("-iniciada_en", "-pk")
            .values_list("pk", flat=True)[MAX_SESIONES_SIMULTANEAS:]
        )
        if sobrantes:
            Sesion.objects.filter(pk__in=sobrantes).update(cerrada_en=ahora, motivo_cierre=MotivoCierre.FORZADO)
    return nueva


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
        respuesta = self.get_response(request)
        if getattr(request, "_vz_limpiar_sitio", False):
            respuesta["Clear-Site-Data"] = CLEAR_SITE_DATA
        return respuesta

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


def csrf_fallido(request, reason=""):
    """
    CSRF_FAILURE_VIEW. Clear-Site-Data borra la cookie CSRF al salir. Si el
    formulario de salida se reenvía (doble clic, o el navegador tarda en limpiar
    la caché) y ya no hay sesión, no hay nada que cerrar: se lleva al login en
    vez de mostrar un 403. Cualquier otro fallo de CSRF sigue siendo 403.
    """
    if request.path == reverse("account_logout") and not request.user.is_authenticated:
        return redirect("account_login")
    return csrf_failure(request, reason)

