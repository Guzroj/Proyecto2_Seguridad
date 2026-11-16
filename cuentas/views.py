from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordResetConfirmView
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode

from . import rls, services
from .models import EstadoCuenta, Usuario
from .selectores import roles_vigentes


@login_required
def inicio(request):
    return render(
        request,
        "cuentas/inicio.html",
        {"roles": sorted(roles_vigentes(request.user))},
    )


def no_disponible(request, *args, **kwargs):
    """Rutas de allauth deshabilitadas (restablecer contraseña y gestionar correos)."""
    raise Http404


class ActivarCuentaView(PasswordResetConfirmView):
    """
    Enlace de activación (C15): la persona fija su primera contraseña.
    Django guarda el token en la sesión y lo quita de la URL antes de mostrar el
    formulario (no se filtra por Referer). Un enlace inválido, vencido o ya usado
    da la misma respuesta que uno inexistente. No inicia sesión: luego se entra
    por el login normal (con su segundo factor).
    """

    template_name = "cuentas/activar.html"
    success_url = reverse_lazy("account_login")
    post_reset_login = False

    def dispatch(self, *args, **kwargs):
        if self.request.user.is_authenticated:
            return redirect("inicio")
        return super().dispatch(*args, **kwargs)

    def get_user(self, uidb64):
        try:
            pk = int(force_str(urlsafe_base64_decode(uidb64)))
        except (TypeError, ValueError, OverflowError):
            return None
        # Igual que en el login: hasta terminar, solo ve su propia cuenta.
        rls.fijar_contexto(usuario_id=pk, fase=rls.FASE_PREINGRESO)
        cuenta = Usuario._default_manager.filter(pk=pk).first()
        if (
            cuenta is None
            or cuenta.has_usable_password()
            or cuenta.estado not in (EstadoCuenta.PENDIENTE, EstadoCuenta.ACTIVA)
        ):
            return None
        return cuenta

    def form_valid(self, form):
        services.establecer_primera_clave(self.user, form.cleaned_data["new_password1"])
        del self.request.session["_password_reset_token"]
        return redirect(self.get_success_url())
