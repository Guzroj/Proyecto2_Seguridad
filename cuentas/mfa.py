"""
Segundo factor (O7d) y reautenticación.

  - TC, VF, GC y AC deben tener TOTP. Mientras no lo activen, solo pueden ir a
    la activación (con su reautenticación), ver sus códigos de recuperación o
    salir (ExigirMFAMiddleware).
  - C21: quien ya tiene un segundo factor solo registra o ve otro (códigos de
    recuperación) con segundo factor reciente; la contraseña sola no basta.
  - edad_mfa(request): segundos desde el último segundo factor en ESTA sesión,
    según el registro de allauth. Es el atributo `mfa` de la matriz
    (edad_mfa(s) <= 300 s) que el PEP enviará al motor.
  - RG6: si el motor responde ReautenticacionRequerida, se redirige al reto de
    segundo factor y, al superarlo, se vuelve a la página pedida.
  - Tras cada reautenticación se renueva el identificador de sesión.
  - H1: tras levantarse una suspensión, la primera lectura exige un segundo
    factor posterior a la resolución (exige_mfa_tras_h1).
  - Activar TOTP exige un código válido: cuenta como segundo factor reciente
    (no se vuelve a pedir otro para ver los códigos de recuperación recién
    generados) y también renueva el identificador.
"""

import time
from functools import wraps
from urllib.parse import urlencode

from allauth.account.internal.flows.login import AUTHENTICATION_METHODS_SESSION_KEY, record_authentication
from allauth.mfa.models import Authenticator
from allauth.mfa.signals import authenticator_added, authenticator_used
from django.conf import settings
from django.dispatch import receiver
from django.shortcuts import redirect
from django.urls import resolve, reverse

from autorizacion import roles
from nucleo.errores import ReautenticacionRequerida

from . import sesion
from .selectores import roles_vigentes


def ventana_mfa() -> int:
    return getattr(settings, "MFA_REAUTENTICACION_SEGUNDOS", 300)


def tiene_totp(usuario) -> bool:
    return Authenticator.objects.filter(user_id=usuario.pk, type=Authenticator.Type.TOTP).exists()


def exige_mfa(usuario) -> bool:
    return bool(roles_vigentes(usuario) & roles.ROLES_CON_MFA)


def edad_mfa(request) -> float | None:
    """Segundos desde el último segundo factor de esta sesión; None si nunca."""
    registros = request.session.get(AUTHENTICATION_METHODS_SESSION_KEY) or []
    momentos = [r.get("at") for r in registros if r.get("method") == "mfa" and isinstance(r.get("at"), (int, float))]
    if not momentos:
        return None
    return max(0.0, time.time() - max(momentos))


def ultimo_mfa(request) -> float | None:
    """Instante (epoch) del último segundo factor de esta sesión; None si nunca."""
    edad = edad_mfa(request)
    return None if edad is None else time.time() - edad


def exige_mfa_tras_h1(request) -> bool:
    """
    H1: "la primera lectura posterior exige mfa". Verdadero si la suspensión
    del sujeto se levantó después de su último segundo factor en esta sesión.
    Al reautenticarse, deja de exigirse.
    """
    from .selectores import ultima_resolucion_h1

    resuelta = ultima_resolucion_h1(request.user)
    if resuelta is None:
        return False
    momento = ultimo_mfa(request)
    return momento is None or momento < resuelta.timestamp()


def mfa_reciente(request) -> bool:
    edad = edad_mfa(request)
    return edad is not None and edad <= ventana_mfa()


def url_reto(request) -> str:
    return f"{reverse('mfa_reauthenticate')}?{urlencode({'next': request.get_full_path()})}"


def requiere_mfa_reciente(vista):
    """Decorador para vistas que exigen segundo factor hace <= 300 s (aprobar, devolver, ...)."""

    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not mfa_reciente(request):
            raise ReautenticacionRequerida("Se requiere segundo factor reciente.")
        return vista(request, *args, **kwargs)

    return envoltura


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
RUTAS_SIN_TOTP = {
    "mfa_activate_totp",
    "mfa_view_recovery_codes",
    "mfa_download_recovery_codes",
    "mfa_index",
    "mfa_reauthenticate",
    "account_reauthenticate",
    "account_logout",
}
# C21: con un factor ya registrado, gestionar factores exige segundo factor reciente.
RUTAS_FACTOR_CON_MFA = {
    "mfa_activate_totp",
    "mfa_generate_recovery_codes",
    "mfa_view_recovery_codes",
    "mfa_download_recovery_codes",
}


class ExigirMFAMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            ruta = self._nombre_ruta(request)
            con_totp = tiene_totp(request.user)
            if ruta in RUTAS_FACTOR_CON_MFA and con_totp and not mfa_reciente(request):
                return redirect(url_reto(request))
            if ruta not in RUTAS_SIN_TOTP and not con_totp and exige_mfa(request.user):
                return redirect("mfa_activate_totp")
        return self.get_response(request)

    @staticmethod
    def _nombre_ruta(request) -> str | None:
        try:
            return resolve(request.path_info).url_name
        except Exception:
            return None

    def process_exception(self, request, exception):
        # RG6: reto de reautenticación, no negación definitiva.
        if isinstance(exception, ReautenticacionRequerida) and request.user.is_authenticated:
            return redirect(url_reto(request))
        return None


# ---------------------------------------------------------------------------
# Identificador nuevo tras cada reautenticación
# ---------------------------------------------------------------------------
def _renovar(request) -> None:
    anterior = request.session.session_key
    request.session.cycle_key()
    if anterior:
        sesion.renovar_clave(anterior, request.session.session_key)


@receiver(authenticator_used, dispatch_uid="cuentas_renovar_tras_reautenticar")
def renovar_identificador(sender, request, user, authenticator, reauthenticated=False, **kwargs):
    if not reauthenticated or request is None:
        return
    _renovar(request)


@receiver(authenticator_added, dispatch_uid="cuentas_totp_activado")
def totp_activado(sender, request, user, authenticator, **kwargs):
    # La activación ya verificó un código del nuevo TOTP: es un segundo factor reciente.
    if request is None or authenticator.type != Authenticator.Type.TOTP:
        return
    record_authentication(request, user, "mfa", id=authenticator.pk, type=authenticator.type, reauthenticated=True)
    _renovar(request)
