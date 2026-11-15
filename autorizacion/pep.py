"""
PEP (lado Django): arma los atributos que el motor necesita para decidir.

Los atributos se leen de la base en CADA decisión, nunca de la sesión ni de la
petición del navegador (los roles no se copian en la sesión):
  - sujeto: id, estado de la cuenta, roles vigentes y edad del segundo factor
    de la sesión en curso (atributo `mfa`).
  - recurso: tipo e id del objeto ya leído y bloqueado por el servicio.
  - contexto: datos simples que pasa el servicio (ids, no objetos).
Nunca se envían datos personales (nombres, correos, identificación).

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


def atributos_recurso(recurso) -> dict | None:
    if recurso is None:
        return None
    if hasattr(recurso, "pk") and hasattr(recurso, "_meta"):
        return {"tipo": recurso._meta.model_name, "id": recurso.pk}
    return _simple(recurso) if isinstance(recurso, dict) else {"valor": _simple(recurso)}


def construir_solicitud(usuario, accion: str, recurso=None, contexto: dict | None = None) -> dict:
    return {
        "sujeto": atributos_sujeto(usuario),
        "accion": accion,
        "recurso": atributos_recurso(recurso),
        "contexto": _simple(contexto or {}),
    }
