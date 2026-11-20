"""
Pantallas del flujo de negocio (O2 períodos, O3 asignaciones, O4 mediciones, O5 enmiendas).

Las vistas no deciden permisos: cada lectura pasa por el motor (autorizar o listar)
y cada cambio por mediciones.services, que autoriza, valida el estado y registra el
evento. Los botones se muestran según rol y estado solo por comodidad: si alguien
llama la URL igual, el servidor lo niega (403/404/409).

Las acciones que cambian datos se confirman en una página (GET) y se ejecutan con
POST. Aprobar, devolver y autorizar una enmienda exigen segundo factor de <= 300 s:
si falta, el sistema pide el código y vuelve a la página de confirmación (RG6).

  periodos, periodo_abrir, periodo_pasar, periodo_cerrar        O2 L; GC AB, TV, CI (C13)
  asignaciones, asignacion_nueva, asignacion_terminar           O3 L; AC CR, MO (C19)
  mediciones, medicion                                          O4 L (C4, C9, C10)
  medicion_nueva, medicion_modificar, medicion_enviar           TC CR, MO, EV (C1, H4)
  medicion_aprobar, medicion_devolver                           VF AP, DV (C2)
  enmiendas, enmienda_solicitar                                 O5 L; TC SO (C11)
  enmienda_autorizar, enmienda_rechazar                         GC AU, RE (C3)
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from autorizacion.listas import listar
from cuentas.pantallas import autorizar_lectura
from cuentas.selectores import roles_vigentes
from nucleo.acciones import Accion
from nucleo.errores import AccesoDenegado, FueraDeAlcance, ReglaNegocio

from . import selectores, services
from .formularios import (
    AsignacionForm,
    DatosCampoForm,
    DevolverForm,
    EnmiendaForm,
    MedicionNuevaForm,
    MotivoRechazoForm,
    PeriodoForm,
)
from .models import (
    Asignacion,
    Enmienda,
    EstadoEnmienda,
    EstadoMedicion,
    EstadoPeriodo,
    Medicion,
    MedicionActor,
    Periodo,
)

FORMULARIO = "cuentas/gestion/formulario.html"
CONFIRMAR = "cuentas/gestion/confirmar.html"
CAMPOS = ("numero_arboles", "dap_promedio_cm", "altura_promedio_m", "observaciones")


def _leer(usuario, modelo, pk, accion: str, tipo: str):
    """Inexistente y fuera de alcance dan el mismo 404 (RG3). Un UUID mal formado, también."""
    try:
        objeto = modelo.objects.filter(pk=pk).first()
    except (ValueError, TypeError):
        objeto = None
    if objeto is None:
        raise FueraDeAlcance("No encontrado.")
    autorizar_lectura(usuario, accion, objeto, tipo)
    return objeto


def _formulario(request, form, titulo, boton, accion_ok, aviso=""):
    """POST válido -> servicio; un rechazo del dominio se muestra en el mismo formulario."""
    if request.method == "POST" and form.is_valid():
        try:
            return accion_ok(form.cleaned_data)
        except ReglaNegocio as exc:
            form.add_error(None, str(exc))
    estado = 400 if request.method == "POST" else 200
    plantilla = CONFIRMAR if aviso else FORMULARIO
    return render(request, plantilla, {"form": form, "titulo": titulo, "boton": boton, "aviso": aviso}, status=estado)


def _confirmar(request, titulo, boton, aviso, accion_ok):
    """GET muestra la confirmación; POST ejecuta. Los errores los traduce ErroresMiddleware."""
    if request.method == "POST":
        return accion_ok()
    return render(request, CONFIRMAR, {"titulo": titulo, "boton": boton, "aviso": aviso})


def _o_nada(usuario, nombre: str):
    """Una lista opcional en una ficha: None si el sujeto no puede listarla."""
    try:
        return listar(usuario, nombre)
    except (AccesoDenegado, FueraDeAlcance):
        return None


# Nombres para mostrar. NUNCA con select_related a cuentas o períodos: bajo RLS, un
# JOIN a una fila que el sujeto no puede leer (p. ej. PF no tiene O2, VF no lee la
# cuenta del técnico) haría desaparecer la fila entera. Se resuelven aparte, con lo
# que el sujeto sí puede listar, y lo demás se muestra como "#id".
def _nombres_usuarios(usuario, ids) -> dict:
    cuentas = _o_nada(usuario, "cuentas")
    if cuentas is None:
        return {}
    return dict(cuentas.filter(pk__in=[i for i in ids if i]).values_list("pk", "username"))


def _nombres_periodos(usuario, ids) -> dict:
    lista = _o_nada(usuario, "periodos")
    if lista is None:
        return {}
    return dict(lista.filter(pk__in=list(ids)).values_list("pk", "nombre"))


def _nombre(nombres: dict, pk, propio=None) -> str:
    if pk is None:
        return "—"
    if propio is not None and pk == propio.pk:
        return f"{propio.username} (usted)"
    return nombres.get(pk, f"#{pk}")


# ===========================================================================
# O2 · Períodos
# ===========================================================================
@login_required
def periodos(request):
    return render(request, "mediciones/periodos.html", {
        "periodos": listar(request.user, "periodos").order_by("-fecha_inicio"),
        "es_gc": "GC" in roles_vigentes(request.user),
    })


@login_required
@require_http_methods(["GET", "POST"])
def periodo_abrir(request):
    def abrir(datos):
        p = services.abrir_periodo(request.user, datos["nombre"], datos["fecha_inicio"], datos["fecha_fin"])
        messages.success(request, f"Período {p.nombre} abierto.")
        return redirect("mediciones_periodos")

    return _formulario(request, PeriodoForm(request.POST or None), "Abrir período", "Abrir", abrir)


@login_required
@require_http_methods(["GET", "POST"])
def periodo_pasar(request, pk):
    p = _leer(request.user, Periodo, pk, Accion.PERIODO_LEER, "periodo")

    def pasar():
        services.pasar_a_verificacion(request.user, p)
        messages.success(request, f"Período {p.nombre} en verificación: ya no se registran mediciones nuevas.")
        return redirect("mediciones_periodos")

    return _confirmar(request, f"Pasar {p.nombre} a verificación", "Pasar a verificación",
                      "Los técnicos ya no podrán registrar mediciones nuevas; las devueltas aún se corrigen.", pasar)


@login_required
@require_http_methods(["GET", "POST"])
def periodo_cerrar(request, pk):
    p = _leer(request.user, Periodo, pk, Accion.PERIODO_LEER, "periodo")

    def cerrar():
        services.cerrar_periodo(request.user, p)
        messages.success(request, f"Período {p.nombre} cerrado.")
        return redirect("mediciones_periodos")

    return _confirmar(request, f"Cerrar {p.nombre}", "Cerrar período",
                      "El cierre es definitivo: nadie reabre un período, y después solo se corrige por enmienda (H4).",
                      cerrar)


# ===========================================================================
# O3 · Asignaciones
# ===========================================================================
@login_required
def asignaciones(request):
    lista = list(listar(request.user, "asignaciones").order_by("vigente_hasta", "-vigente_desde"))
    usuarios = _nombres_usuarios(request.user, {a.tecnico_id for a in lista})
    periodos_ = _nombres_periodos(request.user, {a.periodo_id for a in lista})
    fincas = _o_nada(request.user, "fincas")
    nombres_fincas = dict(fincas.filter(pk__in={a.finca_id for a in lista}).values_list("pk", "nombre")) if fincas else {}
    filas = [{"a": a, "tecnico": _nombre(usuarios, a.tecnico_id, request.user),
              "finca": _nombre(nombres_fincas, a.finca_id), "periodo": _nombre(periodos_, a.periodo_id)} for a in lista]
    return render(request, "mediciones/asignaciones.html", {
        "filas": filas,
        "es_ac": "AC" in roles_vigentes(request.user),
    })


@login_required
@require_http_methods(["GET", "POST"])
def asignacion_nueva(request):
    def crear(datos):
        services.crear_asignacion(request.user, datos["tecnico"], datos["finca"], datos["periodo"])
        messages.success(request, f"{datos['tecnico'].username} asignado a {datos['finca'].nombre}.")
        return redirect("mediciones_asignaciones")

    return _formulario(request, AsignacionForm(request.user, request.POST or None), "Nueva asignación", "Asignar", crear)


@login_required
@require_http_methods(["GET", "POST"])
def asignacion_terminar(request, pk):
    a = _leer(request.user, Asignacion, pk, Accion.ASIGNACION_LEER, "asignacion")

    def terminar():
        services.terminar_asignacion(request.user, a)
        messages.success(request, "Asignación terminada. Queda en el historial (C2 la sigue usando).")
        return redirect("mediciones_asignaciones")

    return _confirmar(request, f"Terminar la asignación #{a.pk}", "Terminar",
                      "La asignación no se borra: queda en el historial.", terminar)


# ===========================================================================
# O4 · Mediciones
# ===========================================================================
@login_required
def mediciones(request):
    lista = listar(request.user, "mediciones").select_related("parcela__finca")
    estado = request.GET.get("estado")
    if estado in EstadoMedicion.values:  # filtro de lista blanca
        lista = lista.filter(estado=estado)
    lista = list(lista.order_by("-creada_en"))
    usuarios = _nombres_usuarios(request.user, {m.autor_id for m in lista} | {m.verificador_id for m in lista})
    periodos_ = _nombres_periodos(request.user, {m.periodo_id for m in lista})
    filas = [{"m": m, "periodo": _nombre(periodos_, m.periodo_id),
              "autor": _nombre(usuarios, m.autor_id, request.user),
              "verificador": _nombre(usuarios, m.verificador_id, request.user)} for m in lista]
    return render(request, "mediciones/mediciones.html", {
        "filas": filas,
        "estados": EstadoMedicion.choices,
        "estado": estado,
        "es_tc": "TC" in roles_vigentes(request.user),
    })


def _acciones_medicion(usuario, m: Medicion, roles) -> dict:
    """Qué botones mostrar. Solo comodidad: el servicio y el motor vuelven a decidir."""
    acciones = {"editar": False, "enmendar": False,
                "verificar": "VF" in roles and m.estado == EstadoMedicion.ENVIADA and m.verificador_id == usuario.pk}
    if "TC" in roles:  # el TC sí lee períodos (O2); el PF no, así que no se consulta para otros roles
        periodo = Periodo.objects.filter(pk=m.periodo_id).values_list("estado", flat=True).first()
        acciones["editar"] = m.version_de_id is None and m.autor_id == usuario.pk and (
            (m.estado == EstadoMedicion.BORRADOR and periodo == EstadoPeriodo.ABIERTO)
            or (m.estado == EstadoMedicion.DEVUELTA and periodo in (EstadoPeriodo.ABIERTO, EstadoPeriodo.EN_VERIFICACION)))
        acciones["enmendar"] = selectores.raiz(m).autor_id == usuario.pk and (
            m.estado == EstadoMedicion.APROBADA or (m.version_de_id is None and periodo == EstadoPeriodo.CERRADO))
    return acciones


@login_required
def medicion(request, pk):
    m = _leer(request.user, Medicion, pk, Accion.MEDICION_LEER, "medicion")
    roles = roles_vigentes(request.user)
    actores = list(MedicionActor.objects.filter(medicion=m).order_by("fecha"))
    usuarios = _nombres_usuarios(request.user, {a.usuario_id for a in actores} | {m.autor_id, m.verificador_id})
    enmiendas = _o_nada(request.user, "enmiendas")
    return render(request, "mediciones/medicion.html", {
        "m": m,
        "periodo": _nombre(_nombres_periodos(request.user, {m.periodo_id}), m.periodo_id),
        "autor": _nombre(usuarios, m.autor_id, request.user),
        "verificador": _nombre(usuarios, m.verificador_id, request.user),
        "actores": [{"a": a, "usuario": _nombre(usuarios, a.usuario_id, request.user)} for a in actores],
        "versiones": Medicion.objects.filter(version_de=m).order_by("creada_en") if m.version_de_id is None else None,
        "enmiendas": (enmiendas.filter(medicion_original_id__in=selectores.ids_cadena(m)).order_by("-solicitada_en")
                      if enmiendas is not None else None),
        "acciones": _acciones_medicion(request.user, m, roles),
    })


@login_required
@require_http_methods(["GET", "POST"])
def medicion_nueva(request):
    def crear(datos):
        parcela, periodo = datos.pop("parcela"), datos.pop("periodo")
        m = services.crear_medicion(request.user, parcela, periodo, **datos)
        messages.success(request, f"Medición registrada en BORRADOR: {m.toneladas_co2} t CO₂ (calculadas por el servidor).")
        return redirect("mediciones_medicion", pk=m.pk)

    return _formulario(request, MedicionNuevaForm(request.user, request.POST or None), "Nueva medición", "Registrar", crear)


@login_required
@require_http_methods(["GET", "POST"])
def medicion_modificar(request, pk):
    m = _leer(request.user, Medicion, pk, Accion.MEDICION_LEER, "medicion")

    def guardar(datos):
        services.modificar_medicion(request.user, m, **datos)
        messages.success(request, "Medición modificada; el servidor recalculó las toneladas.")
        return redirect("mediciones_medicion", pk=m.pk)

    form = DatosCampoForm(request.POST or None, initial={c: getattr(m, c) for c in CAMPOS})
    return _formulario(request, form, "Modificar medición", "Guardar", guardar)


@login_required
@require_http_methods(["GET", "POST"])
def medicion_enviar(request, pk):
    m = _leer(request.user, Medicion, pk, Accion.MEDICION_LEER, "medicion")

    def enviar():
        services.enviar_medicion(request.user, m)
        messages.success(request, "Medición enviada: el sistema le asignó un verificador al azar.")
        return redirect("mediciones_medicion", pk=m.pk)

    return _confirmar(request, "Enviar a verificación", "Enviar",
                      "Ya no podrá modificarla salvo que el verificador la devuelva.", enviar)


@login_required
@require_http_methods(["GET", "POST"])
def medicion_aprobar(request, pk):
    m = _leer(request.user, Medicion, pk, Accion.MEDICION_LEER, "medicion")

    def aprobar():
        services.aprobar_medicion(request.user, m)
        messages.success(request, "Medición aprobada. El certificado se emite en la misma operación.")
        return redirect("mediciones_medicion", pk=m.pk)

    return _confirmar(request, "Aprobar medición", "Aprobar",
                      f"{m.numero_arboles} árboles · {m.toneladas_co2} t CO₂. La aprobación es definitiva y "
                      "dispara la emisión del certificado; después solo se corrige por enmienda.", aprobar)


@login_required
@require_http_methods(["GET", "POST"])
def medicion_devolver(request, pk):
    m = _leer(request.user, Medicion, pk, Accion.MEDICION_LEER, "medicion")

    def devolver(datos):
        services.devolver_medicion(request.user, m, datos["motivo"], datos["comentario"])
        messages.success(request, "Medición devuelta al técnico con el motivo indicado.")
        return redirect("mediciones_medicion", pk=m.pk)

    return _formulario(request, DevolverForm(request.POST or None), "Devolver medición", "Devolver", devolver,
                       aviso="Al reenviarla, el sistema le asignará otro verificador (quien devuelve queda como actor).")


# ===========================================================================
# O5 · Enmiendas
# ===========================================================================
@login_required
def enmiendas(request):
    lista = list(listar(request.user, "enmiendas").order_by("-solicitada_en"))
    usuarios = _nombres_usuarios(request.user, {e.solicitante_id for e in lista} | {e.autorizador_id for e in lista})
    filas = [{"e": e, "solicitante": _nombre(usuarios, e.solicitante_id, request.user),
              "autorizador": _nombre(usuarios, e.autorizador_id, request.user)} for e in lista]
    return render(request, "mediciones/enmiendas.html", {
        "filas": filas,
        "es_gc": "GC" in roles_vigentes(request.user),
        "SOLICITADA": EstadoEnmienda.SOLICITADA,
    })


@login_required
@require_http_methods(["GET", "POST"])
def enmienda_solicitar(request, pk):
    m = _leer(request.user, Medicion, pk, Accion.MEDICION_LEER, "medicion")

    def solicitar(datos):
        services.solicitar_enmienda(request.user, m, **datos)
        messages.success(request, "Enmienda solicitada. La medición original no cambia hasta que un GC la autorice "
                                  "y un verificador apruebe la versión corregida.")
        return redirect("mediciones_medicion", pk=m.pk)

    form = EnmiendaForm(request.POST or None, initial={c: getattr(m, c) for c in CAMPOS})
    return _formulario(request, form, "Solicitar enmienda", "Solicitar", solicitar)


@login_required
@require_http_methods(["GET", "POST"])
def enmienda_autorizar(request, pk):
    e = _leer(request.user, Enmienda, pk, Accion.ENMIENDA_LEER, "enmienda")

    def autorizar():
        e2 = services.autorizar_enmienda(request.user, e)
        messages.success(request, "Enmienda autorizada: se creó la versión corregida y pasa a un verificador nuevo.")
        return redirect("mediciones_medicion", pk=e2.version_id)

    return _confirmar(request, "Autorizar enmienda", "Autorizar",
                      f"Justificación: {e.justificacion}. Propone {e.numero_arboles} árboles, "
                      f"{e.dap_promedio_cm} cm y {e.altura_promedio_m} m.", autorizar)


@login_required
@require_http_methods(["GET", "POST"])
def enmienda_rechazar(request, pk):
    e = _leer(request.user, Enmienda, pk, Accion.ENMIENDA_LEER, "enmienda")

    def rechazar(datos):
        services.rechazar_enmienda(request.user, e, datos["motivo"])
        messages.success(request, "Enmienda rechazada.")
        return redirect("mediciones_enmiendas")

    return _formulario(request, MotivoRechazoForm(request.POST or None), "Rechazar enmienda", "Rechazar", rechazar)
