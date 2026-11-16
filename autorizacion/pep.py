"""
PEP (lado Django): arma los atributos que el motor necesita para decidir.

Los atributos se leen de la base en CADA decisión, nunca de la sesión ni de la
petición del navegador (los roles no se copian en la sesión):
  - sujeto: id, estado de la cuenta, roles vigentes y edad del segundo factor
    de la sesión en curso (atributo `mfa`).
  - recurso: tipo, id y los atributos que usan las condiciones (ATRIBUTOS), del
    objeto ya leído y bloqueado por el servicio.
  - contexto: lo que pasa el servicio; los objetos del modelo llevan sus
    atributos igual que el recurso (p. ej. el técnico, la finca y el período al
    crear una asignación).
  - hbac: la hora del servidor y los contadores de H1 (lecturas de O1/O4) y H3
    (exportaciones) del sujeto; las reglas de H1–H3 las aplica el motor.
Nunca se envían datos personales (nombres, correos, identificación): solo ids,
estados y conjuntos de ids.

El gancho autorizar() no recibe la petición, así que PeticionActualMiddleware la
deja en una variable de contexto mientras dura. Fuera de una petición (consola,
tareas) no hay segundo factor: mfa_edad = None.
"""

from contextvars import ContextVar

peticion_actual: ContextVar = ContextVar("verdeazul_peticion_actual", default=None)


class PeticionActualMiddleware:
    """
    Va después de AuthenticationMiddleware y de ContextoRLSMiddleware. Al terminar
    la petición crea las alertas HBAC que quedaron pendientes porque se pidieron
    dentro de una transacción (cuentas.hbac): así una negación H1 deja su alerta
    aunque la operación se revierta.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from cuentas import hbac

        marca = peticion_actual.set(request)
        pendientes = hbac.iniciar_pendientes()
        try:
            return self.get_response(request)
        finally:
            hbac.crear_pendientes(pendientes)
            peticion_actual.reset(marca)


def _simple(valor, profundidad=0):
    """JSON simple: modelos -> {"tipo", "id"}; nada de objetos ni textos largos."""
    if profundidad > 3:
        return None
    if valor is None or isinstance(valor, (bool, int, float)):
        return valor
    if isinstance(valor, str):
        return valor[:200]
    if isinstance(valor, dict):
        return {str(k)[:50]: _simple(v, profundidad + 1) for k, v in list(valor.items())[:50]}
    if isinstance(valor, (list, tuple, set, frozenset)):
        return [_simple(v, profundidad + 1) for v in list(valor)[:100]]
    if hasattr(valor, "pk") and hasattr(valor, "_meta"):
        return {"tipo": valor._meta.model_name, "id": valor.pk}
    return str(valor)[:200]


def _edad_mfa(usuario_id) -> float | None:
    request = peticion_actual.get()
    if request is None or getattr(getattr(request, "user", None), "pk", None) != usuario_id:
        return None
    from cuentas.mfa import edad_mfa

    return edad_mfa(request)


def atributos_sujeto(usuario) -> dict:
    from cuentas.models import Usuario
    from cuentas.selectores import roles_vigentes

    usuario_id = getattr(usuario, "pk", usuario)
    estado = Usuario.objects.filter(pk=usuario_id).values_list("estado", flat=True).first()
    roles = sorted(roles_vigentes(usuario_id)) if estado else []
    datos = {"id": usuario_id, "estado": estado or "", "roles": roles, "mfa_edad": _edad_mfa(usuario_id)}
    if "AE" in roles:
        datos["ventana"] = _ventana_del_auditor(usuario_id)
    return datos


def _ventana_del_auditor(usuario_id) -> dict | None:
    """C5: la ventana del AE y lo que hace falta para evaluarla (U_GC al aprobar)."""
    from cuentas.models import VentanaAuditoria
    from cuentas.selectores import hoy, tiene_rol

    v = VentanaAuditoria.objects.filter(auditor_id=usuario_id).first()
    if v is None:
        return None
    return {
        "estado": v.estado, "inicio": v.inicio.isoformat(), "fin": v.fin.isoformat(), "hoy": hoy().isoformat(),
        "aprobada_por_gc": bool(v.aprobada_por_id and tiene_rol(v.aprobada_por_id, "GC", en=v.aprobada_en)),
        "aprobada_por_distinto": bool(v.aprobada_por_id and v.aprobada_por_id != v.solicitada_por_id),
    }


# ---------------------------------------------------------------------------
# Atributos por tipo de objeto (los que leen las condiciones de autorizacion.condiciones)
# ---------------------------------------------------------------------------
def _usuario(u) -> dict:
    """Otra persona como recurso o contexto: lo mismo que se lee del sujeto, sin id de sesión."""
    datos = atributos_sujeto(u)
    return {"estado": datos["estado"], "roles": datos["roles"]}


def _de_finca(finca_id: int) -> dict:
    from catalogo.models import Finca
    from mediciones.models import Asignacion, EstadoPeriodo, Medicion, MedicionActor

    propietario = Finca.objects.filter(pk=finca_id).values_list("propietario_id", flat=True).first()
    tecnicos = (
        Asignacion.objects.filter(finca_id=finca_id, vigente_hasta__isnull=True)
        .exclude(periodo__estado=EstadoPeriodo.CERRADO)
        .values_list("tecnico_id", flat=True)
    )
    verificadores = set(
        Medicion.objects.filter(parcela__finca_id=finca_id, verificador__isnull=False)
        .values_list("verificador_id", flat=True)
    ) | set(MedicionActor.objects.filter(medicion__parcela__finca_id=finca_id).values_list("usuario_id", flat=True))
    return {"propietario": propietario, "tecnicos_vigentes": sorted(set(tecnicos)),
            "verificadores": sorted(verificadores)}


def _finca(f) -> dict:
    return {"estado": f.estado, **_de_finca(f.pk)}


def _parcela(p) -> dict:
    return {"estado": p.estado, "finca": p.finca_id, **_de_finca(p.finca_id)}


def _datos_propietario(d) -> dict:
    return {"propietario": d.usuario_id}


def _periodo(p) -> dict:
    from django.db.models import Q
    from mediciones.models import EstadoMedicion, Medicion

    # Igual que mediciones.services.cerrar_periodo: una versión enmendada devuelta no bloquea.
    pendientes = Medicion.objects.filter(periodo_id=p.pk).filter(
        Q(estado=EstadoMedicion.ENVIADA) | Q(estado=EstadoMedicion.DEVUELTA, version_de__isnull=True)
    ).count()
    return {"estado": p.estado, "pendientes": pendientes}


def _asignacion(a) -> dict:
    from catalogo.models import Finca
    from mediciones.models import Periodo

    return {
        "tecnico": a.tecnico_id, "finca": a.finca_id, "periodo": a.periodo_id,
        "propietario": Finca.objects.filter(pk=a.finca_id).values_list("propietario_id", flat=True).first(),
        "periodo_estado": Periodo.objects.filter(pk=a.periodo_id).values_list("estado", flat=True).first(),
        "vigente": a.vigente_hasta is None,
    }


def _asignados_vigentes(finca_id, periodo_id) -> list:
    """asig(·, f, p): técnicos con asignación vigente para esa finca y período."""
    from mediciones.models import Asignacion

    return sorted(set(Asignacion.objects.filter(
        finca_id=finca_id, periodo_id=periodo_id, vigente_hasta__isnull=True).values_list("tecnico_id", flat=True)))


def _medicion(m) -> dict:
    from catalogo.models import Parcela
    from mediciones import selectores
    from mediciones.models import Enmienda, MedicionActor, Periodo

    finca_id, propietario = Parcela.objects.filter(pk=m.parcela_id).values_list(
        "finca_id", "finca__propietario_id").first() or (None, None)
    enmienda = Enmienda.objects.filter(version_id=m.pk).values_list("estado", flat=True).first()
    ultima = selectores.ultima_aprobada(m)
    return {
        "estado": m.estado, "autor": m.autor_id, "autor_original": selectores.raiz(m).autor_id,
        "verificador": m.verificador_id,
        "version_de": str(m.version_de_id) if m.version_de_id else None,
        "actores": sorted(set(MedicionActor.objects.filter(medicion_id=m.pk).values_list("usuario_id", flat=True))),
        "actores_cadena": sorted(selectores.actores_cadena(m)),
        "tecnicos": sorted(selectores.tecnicos(finca_id, m.periodo_id)),
        "asignados_vigentes": _asignados_vigentes(finca_id, m.periodo_id),
        "parcela": m.parcela_id, "finca": finca_id, "propietario": propietario, "periodo": m.periodo_id,
        "periodo_estado": Periodo.objects.filter(pk=m.periodo_id).values_list("estado", flat=True).first(),
        "enmienda_estado": enmienda,
        "enmienda_en_curso": selectores.enmienda_en_curso(m) is not None,
        "ultima_aprobada": str(ultima.pk) if ultima else None,
    }


def _enmienda(e) -> dict:
    from mediciones import selectores
    from mediciones.models import Medicion

    m0 = Medicion.objects.select_related("periodo", "parcela__finca").get(pk=e.medicion_original_id)
    return {
        "estado": e.estado, "solicitante": e.solicitante_id, "medicion": str(m0.pk),
        "m0_estado": m0.estado, "periodo_estado": m0.periodo.estado,
        "actores_m0": sorted(selectores.actores_cadena(m0)),
        "propietario": m0.parcela.finca.propietario_id,
        "justificacion_presente": bool((e.justificacion or "").strip()),  # nunca el texto
    }


def _solicitud_rol(r) -> dict:
    from cuentas.selectores import roles_vigentes

    return {"usuario": r.usuario_id, "usuario_roles": sorted(roles_vigentes(r.usuario_id)), "rol": r.rol,
            "tipo": r.tipo, "estado": r.estado, "solicitada_por": r.solicitada_por_id}


def _ventana(v) -> dict:
    return {"estado": v.estado, "inicio": v.inicio.isoformat(), "fin": v.fin.isoformat(),
            "auditor": v.auditor_id, "solicitada_por": v.solicitada_por_id}


def _restablecimiento(r) -> dict:
    return {"usuario": r.usuario_id, "estado": r.estado, "solicitado_por": r.solicitado_por_id}


def _alerta(a) -> dict:
    return {"sujeto": a.sujeto_id, "control": a.control, "estado": a.estado}


def _sesion(r) -> dict:
    return {"usuario": r.usuario_id}


def _dispositivo(d) -> dict:
    return {"responsable": d.responsable_id, "estado": d.estado}


ATRIBUTOS = {
    "usuario": _usuario,
    "finca": _finca,
    "parcela": _parcela,
    "datospropietario": _datos_propietario,
    "periodo": _periodo,
    "asignacion": _asignacion,
    "medicion": _medicion,
    "enmienda": _enmienda,
    "solicitudrol": _solicitud_rol,
    "ventanaauditoria": _ventana,
    "restablecimientofactor": _restablecimiento,
    "alerta": _alerta,
    "sesion": _sesion,
    "dispositivocampo": _dispositivo,
}


def _contexto_medicion_crear(recurso, contexto: dict) -> dict:
    """C1 CR: el recurso es la parcela y el contexto trae el período; asig(s, f, p) los combina."""
    periodo = (contexto or {}).get("periodo")
    if recurso is None or periodo is None or not hasattr(recurso, "finca_id"):
        return {}
    return {"asignados_vigentes": _asignados_vigentes(recurso.finca_id, getattr(periodo, "pk", periodo))}


def _contexto_factor_registrar(recurso, contexto: dict) -> dict:
    """C21: si la cuenta ya tiene un segundo factor, registrar otro exige mfa."""
    from allauth.mfa.models import Authenticator

    usuario_id = getattr(recurso, "pk", None)
    return {"tiene_factor": usuario_id is not None and Authenticator.objects.filter(user_id=usuario_id).exists()}


# Atributos que dependen de la combinación recurso + contexto de una acción concreta.
CONTEXTO_POR_ACCION = {
    "medicion:crear": _contexto_medicion_crear,
    "factor:registrar": _contexto_factor_registrar,
}


def atributos_objeto(objeto) -> dict:
    """{"tipo", "id"} y, si el tipo está en ATRIBUTOS, sus atributos leídos de la base."""
    tipo = objeto._meta.model_name
    datos = {"tipo": tipo, "id": _simple(objeto.pk)}
    lector = ATRIBUTOS.get(tipo)
    if lector is not None:
        datos.update(lector(objeto))
    return datos


def _es_modelo(valor) -> bool:
    return hasattr(valor, "pk") and hasattr(valor, "_meta")


def atributos_recurso(recurso) -> dict | None:
    if recurso is None:
        return None
    if _es_modelo(recurso):
        return atributos_objeto(recurso)
    return _simple(recurso) if isinstance(recurso, dict) else {"valor": _simple(recurso)}


def atributos_contexto(contexto: dict) -> dict:
    return {
        str(clave)[:50]: atributos_objeto(valor) if _es_modelo(valor) else _simple(valor)
        for clave, valor in list((contexto or {}).items())[:50]
    }


def reloj():
    """Reloj del servidor para HBAC (punto único: las pruebas lo fijan)."""
    from django.utils import timezone

    return timezone.now()


def atributos_hbac(usuario_id, accion: str, roles) -> dict:
    """
    Hora del servidor y, según la acción, los contadores de H1 (lectura de O1/O4)
    o H3 (exportación). Sin datos de qué se leyó: solo números.
    """
    from django.utils import timezone

    from cuentas import selectores

    from . import hbac, matriz

    ahora = reloj()
    datos = {"hora": timezone.localtime(ahora).isoformat()}
    objeto, operacion = matriz.ACCIONES.get(accion, (None, None))
    if operacion == "L" and objeto in hbac.LECTURAS_H1:
        resuelta = selectores.ultima_resolucion_h1(usuario_id)
        propia = selectores.media_propia_h1(usuario_id, ahora)
        datos.update({
            "h1_suspendido": selectores.suspendido_h1(usuario_id),
            "h1_resuelta_hace": None if resuelta is None else max(0.0, (ahora - resuelta).total_seconds()),
            "h1_lecturas": selectores.lecturas_h1(usuario_id, ahora),
            "h1_media_propia": propia,
            "h1_media_rol": {} if propia is not None else {
                rol: selectores.media_rol_h1(rol, ahora) for rol in sorted(set(roles) & hbac.ROLES_H1)},
        })
    elif operacion == "X":
        datos["h3_exportados"] = selectores.exportados_h3(usuario_id, ahora)
    return datos


def construir_solicitud(usuario, accion: str, recurso=None, contexto: dict | None = None) -> dict:
    datos_contexto = atributos_contexto(contexto)
    extra = CONTEXTO_POR_ACCION.get(accion)
    if extra is not None:
        datos_contexto.update(extra(recurso, contexto))
    sujeto = atributos_sujeto(usuario)
    return {
        "sujeto": sujeto,
        "accion": accion,
        "recurso": atributos_recurso(recurso),
        "contexto": datos_contexto,
        "hbac": atributos_hbac(sujeto["id"], accion, sujeto["roles"]),
    }
