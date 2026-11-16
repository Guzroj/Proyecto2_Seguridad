"""
Pantallas de cuentas (O7a). Las vistas no deciden permisos: cada lectura pasa por
el motor (ganchos.autorizar o autorizacion.listas) y cada cambio por su servicio,
que autoriza y registra el evento. Los errores se traducen en cuentas.errores.

  cuentas            lista filtrada por el alcance (AC, GC y AE ven todas; los demás, la suya)
  cuenta             ficha (cuenta:leer; fuera de alcance = 404)
  cuenta_nueva       C15 CR (AC); GC y AE quedan PENDIENTES de su segunda aprobación
  cuenta_modificar   C15 MO (AC): nombre, apellidos y correo
  cuenta_baja        C15 BA (AC), con confirmación
  cuenta_enlace      enlace de activación de un solo uso, que AC entrega por otro canal
  mi_cuenta          la propia cuenta y sus sesiones (C21)
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from autorizacion.listas import listar
from nucleo import ganchos
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, FueraDeAlcance, ReglaNegocio

from . import services
from .formularios import CuentaModificarForm, CuentaNuevaForm
from .models import Usuario, VentanaAuditoria
from .selectores import roles_vigentes


def autorizar_lectura(usuario, accion: str, objeto, tipo: str):
    """Lectura de pantalla: si el motor niega, queda en la bitácora (RG4) y se vuelve a lanzar."""
    try:
        ganchos.autorizar(usuario, accion, objeto)
    except (AccesoDenegado, FueraDeAlcance) as exc:
        ganchos.registrar_evento(actor=usuario, accion=accion, recurso_tipo=tipo,
                                 recurso_id=str(getattr(objeto, "pk", "") or ""), resultado="DENEGADO",
                                 detalle=type(exc).__name__)
        raise


def _cuenta(usuario, pk, accion=Accion.CUENTA_LEER) -> Usuario:
    cuenta = Usuario.objects.filter(pk=pk).first()
    if cuenta is None:
        raise FueraDeAlcance("No encontrado.")  # igual que fuera de alcance (RG3)
    autorizar_lectura(usuario, accion, cuenta, "cuenta")
    return cuenta


def _sin_cache(respuesta):
    respuesta["Cache-Control"] = "no-store"
    return respuesta


@login_required
def cuentas(request):
    lista = listar(request.user, "cuentas").order_by("username")
    filas = [{"cuenta": c, "roles": sorted(roles_vigentes(c))} for c in lista]
    return render(request, "cuentas/gestion/cuentas.html", {"filas": filas})


@login_required
def cuenta(request, pk):
    cuenta = _cuenta(request.user, pk)
    contexto = {
        "cuenta": cuenta,
        "roles": sorted(roles_vigentes(cuenta)),
        "ventana": VentanaAuditoria.objects.filter(auditor=cuenta).first(),
        "gestiona": "AC" in roles_vigentes(request.user) and cuenta.pk != request.user.pk,
        "sin_clave": not cuenta.has_usable_password(),
    }
    return render(request, "cuentas/gestion/cuenta.html", contexto)


@login_required
@require_http_methods(["GET", "POST"])
def cuenta_nueva(request):
    form = CuentaNuevaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            nueva = services.crear_cuenta(request.user, **form.cleaned_data)
        except ReglaNegocio as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Cuenta {nueva.username} creada ({nueva.estado}).")
            return redirect("gestion_cuenta", pk=nueva.pk)
    estado = 400 if request.method == "POST" else 200
    return render(request, "cuentas/gestion/formulario.html",
                  {"form": form, "titulo": "Nueva cuenta", "boton": "Crear"}, status=estado)


@login_required
@require_http_methods(["GET", "POST"])
def cuenta_modificar(request, pk):
    cuenta = _cuenta(request.user, pk)
    inicial = {"first_name": cuenta.first_name, "last_name": cuenta.last_name, "email": cuenta.email}
    form = CuentaModificarForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        try:
            services.modificar_cuenta(request.user, cuenta, **form.cleaned_data)
        except ReglaNegocio as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Cuenta modificada.")
            return redirect("gestion_cuenta", pk=cuenta.pk)
    estado = 400 if request.method == "POST" else 200
    return render(request, "cuentas/gestion/formulario.html",
                  {"form": form, "titulo": f"Modificar {cuenta.username}", "boton": "Guardar"}, status=estado)


@login_required
@require_http_methods(["GET", "POST"])
def cuenta_baja(request, pk):
    cuenta = _cuenta(request.user, pk)
    if request.method == "POST":
        cuenta = services.dar_de_baja(request.user, cuenta)
        messages.success(request, f"Cuenta {cuenta.username} dada de baja ({cuenta.estado}).")
        return redirect("gestion_cuenta", pk=cuenta.pk)
    return render(request, "cuentas/gestion/confirmar.html", {
        "titulo": f"Dar de baja {cuenta.username}", "boton": "Dar de baja",
        "aviso": "Se cierran todas sus sesiones. Si tiene dispositivos de campo activos, queda EN_BAJA "
                 "hasta la sincronización final.",
    })


@login_required
@require_POST
def cuenta_enlace(request, pk):
    cuenta = _cuenta(request.user, pk)
    ruta = services.enlace_activacion(request.user, cuenta)
    respuesta = render(request, "cuentas/gestion/enlace.html",
                       {"cuenta": cuenta, "enlace": request.build_absolute_uri(ruta)})
    respuesta["Referrer-Policy"] = "no-referrer"
    return _sin_cache(respuesta)


@login_required
def mi_cuenta(request):
    autorizar_lectura(request.user, Accion.CUENTA_LEER, request.user, "cuenta")
    sesiones = listar(request.user, "sesiones").filter(usuario=request.user).order_by("-iniciada_en")[:20]
    return render(request, "cuentas/gestion/mi_cuenta.html",
                  {"roles": sorted(roles_vigentes(request.user)), "sesiones": sesiones})
