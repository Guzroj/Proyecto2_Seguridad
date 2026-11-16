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

Roles y ventanas (O7b, O7a AU):
  rol_asignar, rol_retirar      C7 AR, RR (AC); GC y AE quedan como solicitud
  solicitudes_rol               lista; GC autoriza o rechaza (C7 AU, RE)
  ventanas                      lista; GC aprueba (C14)
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from autorizacion.listas import listar
from nucleo import ganchos
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, DatoInvalido, FueraDeAlcance, ReglaNegocio

from . import services
from .formularios import AsignarRolForm, CuentaModificarForm, CuentaNuevaForm, MotivoForm
from .models import EstadoSolicitudRol, EstadoVentana, SolicitudRol, Usuario, VentanaAuditoria
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


def _objeto(usuario, modelo, pk, accion: str, tipo: str):
    """Lee el objeto y autoriza su lectura; inexistente y fuera de alcance dan el mismo 404."""
    objeto = modelo.objects.filter(pk=pk).first()
    if objeto is None:
        raise FueraDeAlcance("No encontrado.")
    autorizar_lectura(usuario, accion, objeto, tipo)
    return objeto


def _o_nada(usuario, nombre: str):
    """Una lista opcional en una ficha: vacía si el sujeto no puede listarla."""
    try:
        return listar(usuario, nombre)
    except AccesoDenegado:
        return None


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
    historial = _o_nada(request.user, "roles")
    solicitudes = _o_nada(request.user, "solicitudes_rol")
    gestiona = "AC" in roles_vigentes(request.user) and cuenta.pk != request.user.pk
    vigentes = roles_vigentes(cuenta)
    contexto = {
        "cuenta": cuenta,
        "roles": sorted(vigentes),
        "ventana": VentanaAuditoria.objects.filter(auditor=cuenta).first(),
        "gestiona": gestiona,
        "sin_clave": not cuenta.has_usable_password(),
        "historial": historial.filter(usuario=cuenta).order_by("-vigente_desde") if historial is not None else None,
        "solicitudes": solicitudes.filter(usuario=cuenta).order_by("-solicitada_en") if solicitudes is not None else None,
        "form_rol": AsignarRolForm() if gestiona and cuenta.estado == "ACTIVA" else None,
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


# ---------------------------------------------------------------------------
# Roles (O7b) y ventanas de auditoría (O7a AU)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def rol_asignar(request, pk):
    cuenta = _cuenta(request.user, pk)
    form = AsignarRolForm(request.POST)
    if not form.is_valid():
        raise DatoInvalido("Rol desconocido.")
    rol = form.cleaned_data["rol"]
    resultado = services.asignar_rol(request.user, cuenta, rol)
    if isinstance(resultado, SolicitudRol):
        messages.success(request, f"Solicitud de {rol} registrada: la autoriza otro GC.")
    else:
        messages.success(request, f"Rol {rol} asignado; se cerraron las sesiones de la cuenta.")
    return redirect("gestion_cuenta", pk=cuenta.pk)


@login_required
@require_http_methods(["GET", "POST"])
def rol_retirar(request, pk, rol):
    cuenta = _cuenta(request.user, pk)
    if request.method == "POST":
        resultado = services.retirar_rol(request.user, cuenta, rol)
        if isinstance(resultado, SolicitudRol):
            messages.success(request, f"Solicitud de retiro de {rol} registrada: la autoriza otro GC.")
        else:
            messages.success(request, f"Rol {rol} retirado; se cerraron las sesiones de la cuenta.")
        return redirect("gestion_cuenta", pk=cuenta.pk)
    return render(request, "cuentas/gestion/confirmar.html", {
        "titulo": f"Retirar {rol} a {cuenta.username}", "boton": "Retirar",
        "aviso": "GC y AE necesitan la autorización de otro GC; los demás roles se retiran ya y se "
                 "cierran las sesiones de la cuenta.",
    })


@login_required
def solicitudes_rol(request):
    lista = listar(request.user, "solicitudes_rol").select_related("usuario", "solicitada_por", "resuelta_por")
    return render(request, "cuentas/gestion/solicitudes_rol.html", {
        "en_curso": lista.filter(estado=EstadoSolicitudRol.SOLICITADA).order_by("solicitada_en"),
        "resueltas": lista.exclude(estado=EstadoSolicitudRol.SOLICITADA).order_by("-resuelta_en")[:50],
        "resuelve": "GC" in roles_vigentes(request.user),
    })


@login_required
@require_http_methods(["GET", "POST"])
def solicitud_autorizar(request, pk):
    solicitud = _objeto(request.user, SolicitudRol, pk, Accion.ROL_LEER, "solicitud_rol")
    if request.method == "POST":
        services.autorizar_solicitud(request.user, solicitud)
        messages.success(request, "Solicitud autorizada; se cerraron las sesiones de la cuenta.")
        return redirect("gestion_solicitudes_rol")
    return render(request, "cuentas/gestion/confirmar.html", {
        "titulo": f"Autorizar: {solicitud.get_tipo_display()} {solicitud.rol} a {solicitud.usuario.username}",
        "boton": "Autorizar",
        "aviso": f"La pidió {solicitud.solicitada_por.username}. Al autorizar, el cambio tiene efecto ya.",
    })


@login_required
@require_http_methods(["GET", "POST"])
def solicitud_rechazar(request, pk):
    solicitud = _objeto(request.user, SolicitudRol, pk, Accion.ROL_LEER, "solicitud_rol")
    form = MotivoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.rechazar_solicitud(request.user, solicitud, motivo=form.cleaned_data["motivo"])
        messages.success(request, "Solicitud rechazada.")
        return redirect("gestion_solicitudes_rol")
    return render(request, "cuentas/gestion/confirmar.html", {
        "titulo": f"Rechazar: {solicitud.get_tipo_display()} {solicitud.rol} a {solicitud.usuario.username}",
        "boton": "Rechazar", "form": form,
    }, status=400 if request.method == "POST" else 200)


@login_required
def ventanas(request):
    lista = listar(request.user, "ventanas").select_related("auditor", "solicitada_por", "aprobada_por")
    return render(request, "cuentas/gestion/ventanas.html", {
        "pendientes": lista.filter(estado=EstadoVentana.PENDIENTE).order_by("inicio"),
        "aprobadas": lista.filter(estado=EstadoVentana.APROBADA).order_by("-inicio")[:50],
        "aprueba": "GC" in roles_vigentes(request.user),
    })


@login_required
@require_http_methods(["GET", "POST"])
def ventana_aprobar(request, pk):
    ventana = _objeto(request.user, VentanaAuditoria, pk, Accion.CUENTA_LEER, "ventana")
    if request.method == "POST":
        ventana = services.aprobar_ventana(request.user, ventana)
        messages.success(request, f"Ventana de {ventana.auditor.username} aprobada.")
        return redirect("gestion_ventanas")
    return render(request, "cuentas/gestion/confirmar.html", {
        "titulo": f"Aprobar la ventana de {ventana.auditor.username}", "boton": "Aprobar",
        "aviso": f"Del {ventana.inicio} al {ventana.fin}, pedida por {ventana.solicitada_por.username}. "
                 "La cuenta se activa el día de inicio y se da de baja al terminar.",
    })
