"""
Condiciones C1–C22 de la matriz (Python puro, sin Django).

Cada condición recibe la Solicitud (atributos que el PEP leyó de la base) y el
rol que se está evaluando, y devuelve una Evaluación:
  concede · niega (403) · fuera_de_alcance (404, RG3) · reautenticar (RG6)
Si falta un atributo, lanza AtributoFaltante y el motor niega (RG5).

Atributos del recurso y del contexto (los arma autorizacion.pep):
  finca/parcela       propietario, estado, tecnicos_vigentes, verificadores
  datospropietario    propietario
  periodo             estado, pendientes (mediciones ENVIADA o DEVUELTA originales)
  asignacion          tecnico, propietario, periodo_estado
  medicion            estado, propietario, verificador, actores, ...
  usuario             estado, roles
Las invariantes de dominio (versiones de geometría, estados, H4) las vuelven a
aplicar los servicios; aquí está lo que decide la matriz.
"""

from . import matriz
from .reglas import AtributoFaltante, CONDICIONES, Solicitud, concede, fuera_de_alcance, niega

# Estados del dominio (texto, como los guarda la base).
ACTIVA = "ACTIVA"
ABIERTO, EN_VERIFICACION, CERRADO = "ABIERTO", "EN_VERIFICACION", "CERRADO"
APROBADA = "APROBADA"
EMITIDO, REVOCADO = "EMITIDO", "REVOCADO"


def registrar(cid: str):
    def decorador(funcion):
        CONDICIONES[cid] = funcion
        return funcion

    return decorador


# ---------------------------------------------------------------------------
# Acceso a atributos (RG5: si falta, se niega)
# ---------------------------------------------------------------------------
def destino(s: Solicitud) -> tuple[str, str]:
    return matriz.ACCIONES[s.accion]


def recurso(s: Solicitud) -> dict:
    if not s.recurso:
        raise AtributoFaltante("recurso")
    return s.recurso


def dato(origen: dict, clave: str, nombre: str = ""):
    if not isinstance(origen, dict) or clave not in origen:
        raise AtributoFaltante(f"{nombre or 'recurso'}.{clave}")
    return origen[clave]


def ids(origen: dict, clave: str, nombre: str = "") -> set:
    valor = dato(origen, clave, nombre)
    if not isinstance(valor, list):
        raise AtributoFaltante(f"{nombre or 'recurso'}.{clave}")
    return set(valor)


def del_contexto(s: Solicitud, clave: str) -> dict:
    valor = s.contexto.get(clave)
    if not isinstance(valor, dict):
        raise AtributoFaltante(f"contexto.{clave}")
    return valor


# ---------------------------------------------------------------------------
# O1–O3
# ---------------------------------------------------------------------------
@registrar("C4")
def c4(s: Solicitud, rol: str):
    """PF·O1a, O1b, O4, O6 L: f.propietario = s; O4 solo APROBADA; O6 EMITIDO o REVOCADO."""
    objeto, _ = destino(s)
    r = recurso(s)
    if dato(r, "propietario") != s.sujeto_id:
        return fuera_de_alcance("C4")
    if objeto == matriz.O4 and dato(r, "estado") != APROBADA:
        return fuera_de_alcance("C4")
    if objeto == matriz.O6 and dato(r, "estado") not in (EMITIDO, REVOCADO):
        return fuera_de_alcance("C4")
    return concede("C4")


@registrar("C6")
def c6(s: Solicitud, rol: str):
    """TC·O1a L: ∃a: a.tecnico = s ∧ a.finca = f ∧ a vigente ∧ a.periodo.estado ≠ CERRADO."""
    if s.sujeto_id not in ids(recurso(s), "tecnicos_vigentes"):
        return fuera_de_alcance("C6")
    return concede("C6")


@registrar("C8")
def c8(s: Solicitud, rol: str):
    """TC·O3 L: a.tecnico = s."""
    if dato(recurso(s), "tecnico") != s.sujeto_id:
        return fuera_de_alcance("C8")
    return concede("C8")


@registrar("C9")
def c9(s: Solicitud, rol: str):
    """
    VF·O4 L: m.verificador = s ∨ s ∈ actores(m).
    VF·O1a L: f es la finca de una m que cumple lo anterior (sin O1b).
    """
    objeto, _ = destino(s)
    r = recurso(s)
    if objeto == matriz.O4:
        dentro = dato(r, "verificador") == s.sujeto_id or s.sujeto_id in ids(r, "actores")
    else:
        dentro = s.sujeto_id in ids(r, "verificadores")
    return concede("C9") if dentro else fuera_de_alcance("C9")


@registrar("C13")
def c13(s: Solicitud, rol: str):
    """
    GC·O2 AB: crea un período ABIERTO.
    TV: p.estado = ABIERTO (los paquetes de trabajo sin conexión de la PWA no
        existen en la Parte 2, así que no hay ninguno vigente).
    CI: p.estado = EN_VERIFICACION ∧ ¬∃ m de p con estado ∈ {ENVIADA, DEVUELTA}.
    Nada sobre un período CERRADO: nadie lo reabre.
    """
    _, operacion = destino(s)
    if operacion == "AB":
        return concede("C13")
    r = recurso(s)
    estado = dato(r, "estado")
    if operacion == "TV" and estado == ABIERTO:
        return concede("C13")
    if operacion == "CI" and estado == EN_VERIFICACION:
        if dato(r, "pendientes") == 0:
            return concede("C13")
        return niega("C13", "El período tiene mediciones ENVIADA o DEVUELTA.")
    return niega("C13", f"Un período {estado} no admite esta operación.")


@registrar("C19")
def c19(s: Solicitud, rol: str):
    """
    AC·O3 CR: TC ∈ R_vig(a.tecnico) ∧ a.periodo.estado ≠ CERRADO ∧ a.tecnico ≠ f.propietario.
    AC·O3 MO (solo termina la vigencia): a.periodo.estado ≠ CERRADO ∧ a.tecnico ≠ f.propietario.
    En MO no se exige que el técnico siga siendo TC: si perdió el rol, su
    asignación tiene que poder terminarse.
    """
    _, operacion = destino(s)
    if operacion == "CR":
        tecnico, finca, periodo = (del_contexto(s, c) for c in ("tecnico", "finca", "periodo"))
        if "TC" not in dato(tecnico, "roles", "tecnico"):
            return niega("C19", "El destino no tiene el rol TC vigente.")
        if dato(periodo, "estado", "periodo") == CERRADO:
            return niega("C19", "El período está CERRADO.")
        if dato(tecnico, "id", "tecnico") == dato(finca, "propietario", "finca"):
            return niega("C19", "El técnico es propietario de la finca.")
        return concede("C19")
    r = recurso(s)
    if dato(r, "periodo_estado") == CERRADO:
        return niega("C19", "El período está CERRADO.")
    if dato(r, "tecnico") == dato(r, "propietario"):
        return niega("C19", "El técnico es propietario de la finca.")
    return concede("C19")


@registrar("C20")
def c20(s: Solicitud, rol: str):
    """
    AC·O1a, O1b CR, MO, BA. El motor exige que el objeto sobre el que se actúa
    siga vigente (lo dado de baja no se modifica ni recibe parcelas nuevas).
    Las invariantes de geometría (MO crea versión nueva, una versión
    referenciada no cambia, sin traslape, BA cierra la vigencia) las aplica
    catalogo.services.
    """
    if not s.recurso:
        return concede("C20")  # CR sin objeto previo (finca nueva, datos de propietario)
    if dato(s.recurso, "estado") != ACTIVA:
        return niega("C20", "El objeto está dado de baja.")
    return concede("C20")
