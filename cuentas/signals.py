from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from . import sesion
from .models import MotivoCierre


@receiver(user_logged_in, dispatch_uid="cuentas_registrar_sesion")
def registrar_sesion(sender, request, user, **kwargs):
    # login() ya renovó el identificador de sesión (cycle_key) antes de esta señal.
    if request is None:
        return
    if request.session.session_key is None:
        request.session.save()
    sesion.registrar_inicio(user, request.session.session_key)


@receiver(user_logged_out, dispatch_uid="cuentas_cerrar_sesion")
def cerrar_sesion(sender, request, user, **kwargs):
    if request is not None:
        sesion.cerrar_registro(request.session.session_key, MotivoCierre.SALIDA)
        # ControlSesionMiddleware agrega Clear-Site-Data a esta respuesta.
        request._vz_limpiar_sitio = True
