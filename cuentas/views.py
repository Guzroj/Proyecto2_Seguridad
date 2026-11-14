from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

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
