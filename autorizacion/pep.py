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
Nunca se envían datos personales (nombres, correos, identificación): solo ids,
estados y conjuntos de ids.

El gancho autorizar() no recibe la petición, así que PeticionActualMiddleware la
deja en una variable de contexto mientras dura. Fuera de una petición (consola,
tareas) no hay segundo factor: mfa_edad = None.
"""

from contextvars import ContextVar

peticion_actual: ContextVar = ContextVar("verdeazul_peticion_actual", default=None)


class PeticionActualMiddleware:
    """Va después de AuthenticationMiddleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        marca = peticion_actual.set(request)
        try:
            return self.get_response(request)
        finally:
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
    return {
        "id": usuario_id,
        "estado": estado or "",
        "roles": sorted(roles_vigentes(usuario_id)) if estado else [],
        "mfa_edad": _edad_mfa(usuario_id),
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


def _medicion(m) -> dict:
    from catalogo.models import Parcela
    from mediciones.models import MedicionActor, Periodo

    finca = Parcela.objects.filter(pk=m.parcela_id).values_list("finca_id", "finca__propietario_id").first()
    return {
        "estado": m.estado, "autor": m.autor_id, "verificador": m.verificador_id,
        "actores": sorted(set(MedicionActor.objects.filter(medicion_id=m.pk).values_list("usuario_id", flat=True))),
        "parcela": m.parcela_id, "finca": finca[0] if finca else None, "propietario": finca[1] if finca else None,
        "periodo": m.periodo_id,
        "periodo_estado": Periodo.objects.filter(pk=m.periodo_id).values_list("estado", flat=True).first(),
        "version_de": str(m.version_de_id) if m.version_de_id else None,
    }


ATRIBUTOS = {
    "usuario": _usuario,
    "finca": _finca,
    "parcela": _parcela,
    "datospropietario": _datos_propietario,
    "periodo": _periodo,
    "asignacion": _asignacion,
    "medicion": _medicion,
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


def construir_solicitud(usuario, accion: str, recurso=None, contexto: dict | None = None) -> dict:
    return {
        "sujeto": atributos_sujeto(usuario),
        "accion": accion,
        "recurso": atributos_recurso(recurso),
        "contexto": atributos_contexto(contexto),
    }
