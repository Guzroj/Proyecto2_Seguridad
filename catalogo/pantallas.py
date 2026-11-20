"""
Pantallas del catálogo (O1a fincas y parcelas, O1b datos del propietario).

Las vistas no deciden permisos: cada lectura pasa por el motor (autorizar o
listar) y cada cambio por catalogo.services, que autoriza y registra el evento.
Ocultar un botón es solo comodidad: si alguien llama la URL igual, el servidor niega.

  fincas                 lista filtrada por el alcance (C4, C6, C9; AC, GC y AE ven todas)
  finca                  ficha (finca:leer); parcelas de la finca y, si puede leerlos, datos del propietario
  finca_nueva            CR de AC·O1a (C20)
  finca_modificar        MO de AC·O1a (C20)
  finca_baja             BA de AC·O1a (C20), con confirmación
  parcela                ficha (parcela:leer) con el historial de versiones de la geometría
  parcela_nueva          CR de AC·O1a: código y primera versión de la geometría
  parcela_geometria      MO de AC·O1a: crea una versión nueva, la anterior no cambia (C20, V18)
  parcela_baja           BA de AC·O1a
  propietario_datos      CR / MO de AC·O1b (alta o cambio de los datos personales)
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from autorizacion.listas import listar
from cuentas.pantallas import autorizar_lectura
from cuentas.selectores import roles_vigentes
from nucleo import ganchos
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, FueraDeAlcance, ReglaNegocio

from . import services
from .formularios import (
    DatosPropietarioForm,
    FincaModificarForm,
    FincaNuevaForm,
    GeometriaForm,
    ParcelaNuevaForm,
)
from .models import DatosPropietario, EstadoCatalogo, Finca, Parcela

FORMULARIO = "cuentas/gestion/formulario.html"
CONFIRMAR = "cuentas/gestion/confirmar.html"


def _leer(usuario, modelo, pk, accion: str, tipo: str):
    """Inexistente y fuera de alcance dan el mismo 404 (RG3)."""
    objeto = modelo.objects.filter(pk=pk).first()
    if objeto is None:
        raise FueraDeAlcance("No encontrado.")
    autorizar_lectura(usuario, accion, objeto, tipo)
    return objeto


def _datos_visibles(usuario, finca):
    """Datos personales (O1b) solo si el motor deja leerlos: AC, AE y el propio PF (C4)."""
    if not roles_vigentes(usuario) & {"AC", "AE", "PF"}:
        return None
    datos = DatosPropietario.objects.filter(usuario_id=finca.propietario_id).first()
    if datos is None:
        return None
    try:
        ganchos.autorizar(usuario, Accion.PROPIETARIO_LEER, datos)
    except (AccesoDenegado, FueraDeAlcance):
        return None
    return datos


def _formulario(request, form, titulo, boton, accion_ok):
    """POST válido -> llama al servicio; un rechazo del dominio se muestra en el formulario."""
    if request.method == "POST" and form.is_valid():
        try:
            return accion_ok(form.cleaned_data)
        except ReglaNegocio as exc:
            form.add_error(None, str(exc))
    estado = 400 if request.method == "POST" else 200
    return render(request, FORMULARIO, {"form": form, "titulo": titulo, "boton": boton}, status=estado)


# ---------------------------------------------------------------------------
# Fincas
# ---------------------------------------------------------------------------
@login_required
def fincas(request):
    lista = listar(request.user, "fincas").order_by("nombre")
    return render(request, "catalogo/fincas.html",
                  {"fincas": lista, "es_ac": "AC" in roles_vigentes(request.user)})


@login_required
def finca(request, pk):
    f = _leer(request.user, Finca, pk, Accion.FINCA_LEER, "finca")
    es_ac = "AC" in roles_vigentes(request.user)
    return render(request, "catalogo/finca.html", {
        "finca": f,
        "parcelas": f.parcelas.order_by("codigo"),  # mismo alcance que la finca (O1a)
        "datos": _datos_visibles(request.user, f),
        "gestiona": es_ac and f.estado == EstadoCatalogo.ACTIVA,
    })


@login_required
@require_http_methods(["GET", "POST"])
def finca_nueva(request):
    def crear(datos):
        nueva = services.crear_finca(request.user, datos["nombre"], datos["ubicacion"], datos["propietario"])
        messages.success(request, f"Finca {nueva.nombre} creada.")
        return redirect("catalogo_finca", pk=nueva.pk)

    return _formulario(request, FincaNuevaForm(request.POST or None), "Nueva finca", "Crear", crear)


@login_required
@require_http_methods(["GET", "POST"])
def finca_modificar(request, pk):
    f = _leer(request.user, Finca, pk, Accion.FINCA_LEER, "finca")

    def guardar(datos):
        services.modificar_finca(request.user, f, **datos)
        messages.success(request, "Finca modificada.")
        return redirect("catalogo_finca", pk=f.pk)

    form = FincaModificarForm(request.POST or None, initial={"nombre": f.nombre, "ubicacion": f.ubicacion})
    return _formulario(request, form, f"Modificar {f.nombre}", "Guardar", guardar)


@login_required
@require_http_methods(["GET", "POST"])
def finca_baja(request, pk):
    f = _leer(request.user, Finca, pk, Accion.FINCA_LEER, "finca")
    if request.method == "POST":
        services.dar_baja_finca(request.user, f)
        messages.success(request, f"Finca {f.nombre} dada de baja.")
        return redirect("catalogo_finca", pk=f.pk)
    return render(request, CONFIRMAR, {
        "titulo": f"Dar de baja {f.nombre}", "boton": "Dar de baja",
        "aviso": "Se dan de baja también sus parcelas y se cierra su geometría vigente. Nada se borra.",
    })


@login_required
@require_http_methods(["GET", "POST"])
def propietario_datos(request, pk):
    """Datos personales del propietario de la finca `pk`: alta (CR) o cambio (MO) según existan."""
    f = _leer(request.user, Finca, pk, Accion.FINCA_LEER, "finca")
    existentes = _datos_visibles(request.user, f)
    inicial = {c: getattr(existentes, c) for c in ("nombre_completo", "identificacion", "telefono", "correo")} \
        if existentes else None

    def guardar(datos):
        services.registrar_datos_propietario(request.user, f.propietario, **datos)
        messages.success(request, "Datos del propietario guardados.")
        return redirect("catalogo_finca", pk=f.pk)

    titulo = "Modificar datos del propietario" if existentes else "Registrar datos del propietario"
    return _formulario(request, DatosPropietarioForm(request.POST or None, initial=inicial), titulo, "Guardar", guardar)


# ---------------------------------------------------------------------------
# Parcelas
# ---------------------------------------------------------------------------
@login_required
def parcela(request, pk):
    p = _leer(request.user, Parcela, pk, Accion.PARCELA_LEER, "parcela")
    return render(request, "catalogo/parcela.html", {
        "parcela": p,
        "versiones": p.versiones.order_by("-numero"),
        "gestiona": "AC" in roles_vigentes(request.user) and p.estado == EstadoCatalogo.ACTIVA,
    })


@login_required
@require_http_methods(["GET", "POST"])
def parcela_nueva(request, pk):
    f = _leer(request.user, Finca, pk, Accion.FINCA_LEER, "finca")

    def crear(datos):
        nueva = services.crear_parcela(request.user, f, **datos)
        messages.success(request, f"Parcela {nueva.codigo} creada.")
        return redirect("catalogo_parcela", pk=nueva.pk)

    return _formulario(request, ParcelaNuevaForm(request.POST or None), f"Nueva parcela en {f.nombre}", "Crear", crear)


@login_required
@require_http_methods(["GET", "POST"])
def parcela_geometria(request, pk):
    p = _leer(request.user, Parcela, pk, Accion.PARCELA_LEER, "parcela")

    def guardar(datos):
        version = services.modificar_geometria(request.user, p, **datos)
        messages.success(request, f"Geometría actualizada: versión {version.numero}. La anterior no cambia.")
        return redirect("catalogo_parcela", pk=p.pk)

    return _formulario(request, GeometriaForm(request.POST or None),
                       f"Nueva geometría de {p.codigo}", "Guardar versión", guardar)


@login_required
@require_http_methods(["GET", "POST"])
def parcela_baja(request, pk):
    p = _leer(request.user, Parcela, pk, Accion.PARCELA_LEER, "parcela")
    if request.method == "POST":
        services.dar_baja_parcela(request.user, p)
        messages.success(request, f"Parcela {p.codigo} dada de baja.")
        return redirect("catalogo_parcela", pk=p.pk)
    return render(request, CONFIRMAR, {
        "titulo": f"Dar de baja la parcela {p.codigo}", "boton": "Dar de baja",
        "aviso": "Se cierra la vigencia de su geometría. Las mediciones y certificados conservan la versión que usaron.",
    })
