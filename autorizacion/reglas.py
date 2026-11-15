"""
Decisión del motor de autorización (PDP). Python puro: no lee la base ni la
petición del navegador; decide solo con los atributos que le manda el PEP
(Django), que los lee de la base en cada petición.

Regla de decisión:

    concede(s, a, o)  ⇔  ∃ r ∈ R_vig(s): a ∈ M[r, col(o)] ∧ Cond_{r,o,a}(s, o)  ∧  ¬Proh(s, a, o)

  - Con varios roles basta una concesión, pero cualquier prohibición prevalece.
  - RG1 Denegación por defecto: acción desconocida, rol sin la operación en la
    celda o celda "—" => NIEGA (403).
  - RG3 Si el objeto está fuera del alcance de todos los roles que tienen la
    operación => FUERA_DE_ALCANCE (404, sin revelar si existe).
  - RG5 Falla cerrada: si falta un atributo o una condición no se puede evaluar
    => NIEGA.
  - RG6 Si lo único que falla es el segundo factor reciente => REAUTENTICAR.

Las condiciones C1–C22 se registran en CONDICIONES. Mientras una condición no
esté implementada, su celda se niega (RG5).
"""

from dataclasses import dataclass, field
from typing import Callable

from . import matriz
from .roles import ROLES

CONCEDE = "CONCEDE"
NIEGA = "NIEGA"
FUERA_DE_ALCANCE = "FUERA_DE_ALCANCE"
REAUTENTICAR = "REAUTENTICAR"
RESULTADOS = (CONCEDE, NIEGA, FUERA_DE_ALCANCE, REAUTENTICAR)

ESTADO_ACTIVA = "ACTIVA"
VENTANA_MFA = 300  # segundos (mfa: edad_mfa(s) <= 300 s)


@dataclass(frozen=True)
class Evaluacion:
    resultado: str
    regla: str
    motivo: str = ""


@dataclass(frozen=True)
class Decision:
    resultado: str
    regla: str
    motivo: str
    rol: str | None = None                       # rol que concedió
    hbac: tuple[str, ...] = field(default=())    # controles de la celda concedida

    @property
    def concede(self) -> bool:
        return self.resultado == CONCEDE

    def como_dict(self) -> dict:
        return {"resultado": self.resultado, "regla": self.regla, "motivo": self.motivo,
                "rol": self.rol, "hbac": list(self.hbac)}


@dataclass(frozen=True)
class Solicitud:
    sujeto_id: int
    estado: str
    roles: frozenset[str]
    mfa_edad: float | None
    accion: str
    recurso: dict
    contexto: dict

    @property
    def mfa(self) -> bool:
        return self.mfa_edad is not None and 0 <= self.mfa_edad <= VENTANA_MFA


class AtributoFaltante(Exception):
    """Falta un atributo para evaluar una condición (RG5: se niega)."""


# ---------------------------------------------------------------------------
# Condiciones y prohibiciones
# ---------------------------------------------------------------------------
Condicion = Callable[[Solicitud, str], Evaluacion]
Prohibicion = Callable[[Solicitud], Evaluacion | None]


def concede(regla: str) -> Evaluacion:
    return Evaluacion(CONCEDE, regla)


def niega(regla: str, motivo: str) -> Evaluacion:
    return Evaluacion(NIEGA, regla, motivo)


def fuera_de_alcance(regla: str) -> Evaluacion:
    return Evaluacion(FUERA_DE_ALCANCE, regla, "No encontrado.")


def reautenticar(regla: str) -> Evaluacion:
    return Evaluacion(REAUTENTICAR, regla, "Se requiere segundo factor reciente.")


def _no_implementada(cid: str) -> Condicion:
    def condicion(solicitud: Solicitud, rol: str) -> Evaluacion:
        return niega("RG5", f"{cid} aún no se evalúa: se niega por defecto.")

    condicion.__name__ = f"{cid}_no_implementada"
    return condicion


IDS_CONDICIONES = tuple(f"C{n}" for n in range(1, 23))
CONDICIONES: dict[str, Condicion] = {cid: _no_implementada(cid) for cid in IDS_CONDICIONES}

# Prohibiciones globales (H4, independencia de C2/C3, exclusiones de C7): si una
# devuelve una Evaluación, prevalece sobre cualquier concesión.
PROHIBICIONES: list[Prohibicion] = []


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
def leer_solicitud(datos) -> Solicitud:
    """Valida la forma de la solicitud del PEP. Cualquier rareza => AtributoFaltante."""
    if not isinstance(datos, dict):
        raise AtributoFaltante("solicitud")
    sujeto = datos.get("sujeto")
    if not isinstance(sujeto, dict):
        raise AtributoFaltante("sujeto")
    sujeto_id, estado, roles = sujeto.get("id"), sujeto.get("estado"), sujeto.get("roles")
    mfa_edad = sujeto.get("mfa_edad")
    accion, recurso, contexto = datos.get("accion"), datos.get("recurso"), datos.get("contexto", {})
    if not isinstance(sujeto_id, int) or isinstance(sujeto_id, bool) or sujeto_id <= 0:
        raise AtributoFaltante("sujeto.id")
    if not isinstance(estado, str):
        raise AtributoFaltante("sujeto.estado")
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        raise AtributoFaltante("sujeto.roles")
    if mfa_edad is not None and (isinstance(mfa_edad, bool) or not isinstance(mfa_edad, (int, float))):
        raise AtributoFaltante("sujeto.mfa_edad")
    if not isinstance(accion, str) or not accion:
        raise AtributoFaltante("accion")
    if recurso is not None and not isinstance(recurso, dict):
        raise AtributoFaltante("recurso")
    if not isinstance(contexto, dict):
        raise AtributoFaltante("contexto")
    return Solicitud(
        sujeto_id=sujeto_id, estado=estado, roles=frozenset(r for r in roles if r in ROLES),
        mfa_edad=None if mfa_edad is None else float(mfa_edad), accion=accion,
        recurso=recurso or {}, contexto=contexto,
    )


# ---------------------------------------------------------------------------
# Decisión
# ---------------------------------------------------------------------------
def _evaluar_celda(solicitud: Solicitud, rol: str, permiso: matriz.Permiso) -> Evaluacion:
    """Todas las condiciones de la celda deben conceder; la primera que no, decide."""
    for cid in permiso.condiciones:
        condicion = CONDICIONES.get(cid)
        if condicion is None:
            return niega("RG5", f"Condición {cid} desconocida.")
        try:
            evaluacion = condicion(solicitud, rol)
        except AtributoFaltante as exc:
            return niega("RG5", f"Falta el atributo {exc} para {cid}.")
        except Exception:  # una condición defectuosa nunca concede
            return niega("RG5", f"Error al evaluar {cid}.")
        if not isinstance(evaluacion, Evaluacion) or evaluacion.resultado not in RESULTADOS:
            return niega("RG5", f"{cid} no devolvió una evaluación válida.")
        if evaluacion.resultado != CONCEDE:
            return evaluacion
    return concede(",".join(permiso.condiciones) or "matriz")


def decidir(datos) -> Decision:
    try:
        solicitud = leer_solicitud(datos)
    except AtributoFaltante as exc:
        return Decision(NIEGA, "RG5", f"Solicitud incompleta: {exc}.")

    if solicitud.accion in matriz.ACCIONES_DE_PROCESO:
        return Decision(NIEGA, "RG1", "Operación reservada a un proceso automático.")
    destino = matriz.ACCIONES.get(solicitud.accion)
    if destino is None:
        return Decision(NIEGA, "RG1", "Acción desconocida: se niega por defecto.")
    objeto, operacion = destino

    if solicitud.estado != ESTADO_ACTIVA:
        return Decision(NIEGA, "RG1", "La cuenta del sujeto no está ACTIVA.")

    evaluaciones: list[Evaluacion] = []
    concedida: tuple[str, matriz.Permiso] | None = None
    for rol in sorted(solicitud.roles):
        permiso = matriz.permiso(rol, objeto, operacion)
        if permiso is None:
            continue
        evaluacion = _evaluar_celda(solicitud, rol, permiso)
        if evaluacion.resultado == CONCEDE:
            concedida = (rol, permiso)
            break
        evaluaciones.append(evaluacion)

    if concedida is not None:
        veto = _veto(solicitud)
        if veto is not None:
            return veto
        rol, permiso = concedida
        return Decision(CONCEDE, ",".join(permiso.condiciones) or "matriz",
                        f"{rol}·{objeto} {operacion}", rol=rol, hbac=permiso.hbac)
    decision = _negacion(solicitud, objeto, operacion, evaluaciones)
    # Una prohibición también prevalece sobre la razón de una negación (salvo el 404,
    # que no debe revelar nada del objeto).
    if decision.resultado != FUERA_DE_ALCANCE:
        return _veto(solicitud) or decision
    return decision


def _veto(solicitud: Solicitud) -> Decision | None:
    for prohibicion in PROHIBICIONES:
        try:
            veto = prohibicion(solicitud)
        except Exception:
            return Decision(NIEGA, "RG5", "No se pudo evaluar una prohibición.")
        if veto is not None and veto.resultado != CONCEDE:
            return Decision(veto.resultado, veto.regla, veto.motivo)
    return None


def _negacion(solicitud: Solicitud, objeto: str, operacion: str, evaluaciones: list) -> Decision:
    if not evaluaciones:
        roles = ", ".join(sorted(solicitud.roles)) or "ninguno"
        return Decision(NIEGA, "RG1", f"La matriz no concede {operacion} sobre {objeto} (roles: {roles}).")
    # RG6: algún rol solo necesita el segundo factor reciente.
    reto = next((e for e in evaluaciones if e.resultado == REAUTENTICAR), None)
    if reto is not None:
        return Decision(REAUTENTICAR, reto.regla, reto.motivo)
    # RG3: fuera del alcance de todos los roles que tienen la operación.
    if all(e.resultado == FUERA_DE_ALCANCE for e in evaluaciones):
        return Decision(FUERA_DE_ALCANCE, evaluaciones[0].regla, "No encontrado.")
    primera = next(e for e in evaluaciones if e.resultado == NIEGA)
    return Decision(NIEGA, primera.regla, primera.motivo)


# Las condiciones implementadas se registran al importar su módulo.
from . import condiciones  # noqa: E402,F401
