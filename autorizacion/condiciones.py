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
  medicion            estado, autor, autor_original, version_de, verificador,
                      actores, actores_cadena, tecnicos, asignados_vigentes,
                      propietario, periodo_estado, enmienda_estado,
                      enmienda_en_curso, ultima_aprobada
  enmienda            estado, solicitante, m0_estado, periodo_estado,
                      actores_m0, propietario, justificacion_presente
  usuario             estado, roles
  solicitudrol        usuario, usuario_roles, rol, tipo, estado, solicitada_por
  ventanaauditoria    estado, inicio, fin, auditor, solicitada_por
  restablecimientofactor  usuario, estado, solicitado_por
  alerta              sujeto, control, estado
  sesion              usuario
Del sujeto, además: `ventana` (solo AE, para C5) y, en factor:registrar,
`tiene_factor` en el contexto (C21).
Lo que viene en la petición y no en la base (la justificación de C11, el motivo
de devolución de C2) lo valida el servicio, que es quien lo recibe.
Las invariantes de dominio (versiones de geometría, estados, H4) las vuelven a
aplicar los servicios; aquí está lo que decide la matriz.
"""

from datetime import date

from . import matriz
from .roles import conflictos
from .reglas import (
    CONDICIONES,
    PROHIBICIONES,
    AtributoFaltante,
    Solicitud,
    concede,
    fuera_de_alcance,
    niega,
    reautenticar,
)

# Estados del dominio (texto, como los guarda la base).
ACTIVA = "ACTIVA"
ABIERTO, EN_VERIFICACION, CERRADO = "ABIERTO", "EN_VERIFICACION", "CERRADO"
BORRADOR, ENVIADA, APROBADA, DEVUELTA = "BORRADOR", "ENVIADA", "APROBADA", "DEVUELTA"
SOLICITADA, AUTORIZADA = "SOLICITADA", "AUTORIZADA"
EN_BAJA = "EN_BAJA"
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


# ---------------------------------------------------------------------------
# O4–O6
# ---------------------------------------------------------------------------
@registrar("C1")
def c1(s: Solicitud, rol: str):
    """
    TC·O4 CR: p.estado = ABIERTO ∧ asig(s, f, p) (el servidor fija m.autor := s).
    MO, EV: m.autor = s ∧ m.version_de = ∅ ∧ asig(s, f, p) ∧
            [(BORRADOR ∧ p ABIERTO) ∨ (DEVUELTA ∧ p ∈ {ABIERTO, EN_VERIFICACION})].
    Sin asignación (CR) o sin ser autor (MO, EV) la medición está fuera de su alcance: 404.
    """
    _, operacion = destino(s)
    if operacion == "CR":
        if s.sujeto_id not in ids(s.contexto, "asignados_vigentes", "contexto"):
            return fuera_de_alcance("C1")
        if dato(del_contexto(s, "periodo"), "estado", "periodo") != ABIERTO:
            return niega("C1", "Solo se registran mediciones en un período ABIERTO.")
        return concede("C1")
    m = recurso(s)
    if dato(m, "autor") != s.sujeto_id:
        return fuera_de_alcance("C1")
    if dato(m, "version_de") is not None:
        return niega("C1", "Una versión enmendada no se edita; pasa por verificación.")
    if s.sujeto_id not in ids(m, "asignados_vigentes"):
        return niega("C1", "Sin asignación vigente para esta finca y período.")
    estado, periodo = dato(m, "estado"), dato(m, "periodo_estado")
    if (estado == BORRADOR and periodo == ABIERTO) or (estado == DEVUELTA and periodo in (ABIERTO, EN_VERIFICACION)):
        return concede("C1")
    return niega("C1", f"No se edita una medición {estado} con el período {periodo}.")


@registrar("C10")
def c10(s: Solicitud, rol: str):
    """TC·O4 L: m.autor = s."""
    return concede("C10") if dato(recurso(s), "autor") == s.sujeto_id else fuera_de_alcance("C10")


@registrar("C2")
def c2(s: Solicitud, rol: str):
    """
    VF·O4 AP, DV: m.estado = ENVIADA ∧ [(m.version_de = ∅ ∧ p ∈ {ABIERTO, EN_VERIFICACION})
      ∨ (m.version_de ≠ ∅ ∧ e(m).estado = AUTORIZADA)] ∧ m.verificador = s ∧ s ∉ actores(m)
      ∧ s ∉ tecnicos(f, p) ∧ s ≠ f.propietario ∧ mfa.
    actores(m) se toma de toda la cadena (original y versiones), como el servicio
    al escoger verificador. El motivo de DV (catálogo) lo valida el servicio.
    Si solo falta el segundo factor reciente: reto (RG6).
    """
    m = recurso(s)
    yo = s.sujeto_id
    actores = ids(m, "actores_cadena")
    if dato(m, "verificador") != yo and yo not in ids(m, "actores"):
        return fuera_de_alcance("C2")
    if dato(m, "estado") != ENVIADA:
        return niega("C2", "Solo se aprueba o devuelve una medición ENVIADA.")
    if dato(m, "version_de") is None:
        if dato(m, "periodo_estado") not in (ABIERTO, EN_VERIFICACION):
            return niega("C2", "El período está CERRADO.")
    elif dato(m, "enmienda_estado") != AUTORIZADA:
        return niega("C2", "La versión no tiene una enmienda AUTORIZADA.")
    if dato(m, "verificador") != yo:
        return niega("C2", "Solo el verificador asignado aprueba o devuelve (V4).")
    if yo in actores:
        return niega("C2", "El verificador ya participó en la medición.")
    if yo in ids(m, "tecnicos"):
        return niega("C2", "El verificador fue técnico de esa finca y período (V5).")
    if yo == dato(m, "propietario"):
        return niega("C2", "El verificador es el propietario de la finca.")
    if not s.mfa:
        return reautenticar("C2")
    return concede("C2")


@registrar("C11")
def c11(s: Solicitud, rol: str):
    """
    TC·O5 SO: m0.autor = s ∧ (m0.estado = APROBADA ∨ p.estado = CERRADO) ∧ sin enmienda
    en curso ∧ sin una versión aprobada más reciente que la que se enmienda.
    Igual que mediciones.services: se enmienda la original (si no hay versión
    aprobada) o la versión aprobada más reciente. La justificación la valida el servicio.
    """
    m = recurso(s)
    if dato(m, "autor_original") != s.sujeto_id:
        return fuera_de_alcance("C11")
    if not (dato(m, "estado") == APROBADA or dato(m, "periodo_estado") == CERRADO):
        return niega("C11", "Solo se enmienda una medición aprobada o de un período cerrado.")
    if dato(m, "enmienda_en_curso"):
        return niega("C11", "Ya hay una enmienda en curso.")
    ultima = dato(m, "ultima_aprobada")
    if ultima is not None and ultima != dato(m, "id"):
        return niega("C11", "Se enmienda la versión aprobada más reciente.")
    if ultima is None and dato(m, "version_de") is not None:
        return niega("C11", "Solo se enmienda la versión aprobada más reciente.")
    return concede("C11")


@registrar("C12")
def c12(s: Solicitud, rol: str):
    """TC·O5 L: e.solicitante = s."""
    return concede("C12") if dato(recurso(s), "solicitante") == s.sujeto_id else fuera_de_alcance("C12")


@registrar("C3")
def c3(s: Solicitud, rol: str):
    """
    GC·O5 AU, RE: e.estado = SOLICITADA ∧ (m0.estado = APROBADA ∨ p.estado = CERRADO)
      ∧ e.solicitante ≠ s ∧ s ∉ actores(m0) ∧ s ≠ f.propietario ∧ e.justificacion ≠ '' ∧ mfa.
    """
    e = recurso(s)
    yo = s.sujeto_id
    if dato(e, "estado") != SOLICITADA:
        return niega("C3", "La enmienda ya fue resuelta.")
    if not (dato(e, "m0_estado") == APROBADA or dato(e, "periodo_estado") == CERRADO):
        return niega("C3", "La medición original no está aprobada ni su período cerrado.")
    if dato(e, "solicitante") == yo:
        return niega("C3", "Quien solicita una enmienda no la resuelve.")
    if yo in ids(e, "actores_m0"):
        return niega("C3", "El GC participó en la medición (V7).")
    if yo == dato(e, "propietario"):
        return niega("C3", "El GC es el propietario de la finca.")
    if not dato(e, "justificacion_presente"):
        return niega("C3", "La enmienda no tiene justificación.")
    if not s.mfa:
        return reautenticar("C3")
    return concede("C3")


@registrar("C16")
def c16(s: Solicitud, rol: str):
    """
    GC·O4 SI: cuenta(t).estado = EN_BAJA ∧ registro firmado por un dispositivo de t ∧
    parcela en el paquete de t ∧ validaciones de sincronización ∧ p.estado = ABIERTO.
    Sin registro es la sincronización final vacía: cierra la baja de un técnico
    sin pendientes (decisión del equipo). Con registro, el servicio de
    sincronización entrega el resultado de verificar la firma y el paquete.
    """
    tecnico = del_contexto(s, "tecnico")
    if dato(tecnico, "estado", "tecnico") != EN_BAJA:
        return niega("C16", "Solo se sincroniza la baja de un técnico EN_BAJA.")
    registro = s.contexto.get("registro")
    if registro is None:
        return concede("C16")
    if not isinstance(registro, dict):
        raise AtributoFaltante("contexto.registro")
    if dato(registro, "firma_valida", "registro") is not True:
        return niega("C16", "La firma del registro no es válida.")
    if dato(registro, "dispositivo_responsable", "registro") != dato(tecnico, "id", "tecnico"):
        return niega("C16", "El registro no lo firmó un dispositivo del técnico.")
    if dato(registro, "parcela_en_paquete", "registro") is not True:
        return niega("C16", "La parcela no estaba en el paquete del técnico.")
    if dato(registro, "periodo_estado", "registro") != ABIERTO:
        return niega("C16", "El período no está ABIERTO.")
    return concede("C16")


def h4(s: Solicitud):
    """
    H4 Secuencia: si m.estado = APROBADA ∨ p.estado = CERRADO se niega SIEMPRE la
    modificación de m (y nadie la elimina: no existe esa acción). Solo se corrige
    por C11 → C3 → C2 (V2).
    """
    if s.accion != "medicion:modificar" or not s.recurso:
        return None
    if s.recurso.get("estado") == APROBADA or s.recurso.get("periodo_estado") == CERRADO:
        return niega("H4", "Medición aprobada o de período cerrado: se corrige solo por enmienda.")
    return None


PROHIBICIONES.append(h4)


# ---------------------------------------------------------------------------
# O7–O9
# ---------------------------------------------------------------------------
MAX_DIAS_VENTANA = 30


def _fecha(valor, nombre: str) -> date:
    if not isinstance(valor, str):
        raise AtributoFaltante(nombre)
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise AtributoFaltante(nombre)


@registrar("C5")
def c5(s: Solicitud, rol: str):
    """
    AE·todas L, X: cuenta(s).estado = ACTIVA ∧ hoy ∈ [v.inicio, v.fin] ∧ (v.fin − v.inicio) ≤ 30 d
      ∧ v.aprobada_por ∈ U_GC ∧ v.aprobada_por ≠ v.solicitada_por.
    U_GC se evalúa en el momento de la aprobación. La cuenta ACTIVA ya la exige el motor (RG1).
    """
    v = s.ventana
    if v is None:
        return niega("C5", "El auditor no tiene una ventana de auditoría.")
    if dato(v, "estado", "ventana") != "APROBADA":
        return niega("C5", "La ventana de auditoría no está aprobada.")
    inicio, fin = _fecha(dato(v, "inicio", "ventana"), "ventana.inicio"), _fecha(dato(v, "fin", "ventana"), "ventana.fin")
    hoy = _fecha(dato(v, "hoy", "ventana"), "ventana.hoy")
    if (fin - inicio).days > MAX_DIAS_VENTANA:
        return niega("C5", "La ventana dura más de 30 días.")
    if dato(v, "aprobada_por_gc", "ventana") is not True:
        return niega("C5", "La ventana no la aprobó un GC.")
    if dato(v, "aprobada_por_distinto", "ventana") is not True:
        return niega("C5", "La ventana la aprobó quien la pidió.")
    if not inicio <= hoy <= fin:
        return niega("C5", "Hoy está fuera de la ventana de auditoría (V9).")
    return concede("C5")


@registrar("C7")
def c7(s: Solicitud, rol: str):
    """
    AC·O7b AR(u, r): u ≠ s ∧ cuenta(u).estado = ACTIVA ∧ ∀r' ∈ R_vig(u): {r, r'} ∉ E (V10).
    AC·O7b RR(u, r): u ≠ s.  (GC y AE quedan como solicitud: lo hace el servicio.)
    GC·O7b AU, RE: g ≠ solicitante ∧ g ≠ u; al autorizar una asignación, E se reevalúa.
    """
    _, operacion = destino(s)
    r = recurso(s)
    if operacion in ("AR", "RR"):
        if dato(r, "id") == s.sujeto_id:
            return niega("C7", "Nadie cambia sus propios roles (V10).")
        if operacion == "RR":
            return concede("C7")
        if dato(r, "estado") != ACTIVA:
            return niega("C7", "Solo se asignan roles a cuentas ACTIVA.")
        nuevo = s.contexto.get("rol")
        if not isinstance(nuevo, str):
            raise AtributoFaltante("contexto.rol")
        choque = conflictos(nuevo, dato(r, "roles"))
        if choque:
            return niega("C7", f"El rol {nuevo} es excluyente con {', '.join(sorted(choque))} (V10).")
        return concede("C7")
    if dato(r, "estado") != SOLICITADA:
        return niega("C7", "La solicitud ya fue resuelta.")
    if s.sujeto_id in (dato(r, "solicitada_por"), dato(r, "usuario")):
        return niega("C7", "Resuelve un GC distinto de quien la pidió y de la persona destino.")
    if operacion == "AU" and dato(r, "tipo") == "ASIGNAR":
        choque = conflictos(dato(r, "rol"), dato(r, "usuario_roles"))
        if choque:
            return niega("C7", f"El rol {dato(r, 'rol')} es excluyente con {', '.join(sorted(choque))}.")
    return concede("C7")


@registrar("C14")
def c14(s: Solicitud, rol: str):
    """GC·O7a AU: v.estado = PENDIENTE ∧ (v.fin − v.inicio) ≤ 30 d ∧ s ≠ v.solicitada_por ∧ s ≠ v.auditor."""
    v = recurso(s)
    if dato(v, "estado") != "PENDIENTE":
        return niega("C14", "La ventana ya fue aprobada.")
    if (_fecha(dato(v, "fin"), "recurso.fin") - _fecha(dato(v, "inicio"), "recurso.inicio")).days > MAX_DIAS_VENTANA:
        return niega("C14", "La ventana dura más de 30 días.")
    if s.sujeto_id in (dato(v, "solicitada_por"), dato(v, "auditor")):
        return niega("C14", "Aprueba un GC distinto de quien la pidió y del auditor.")
    return concede("C14")


@registrar("C15")
def c15(s: Solicitud, rol: str):
    """AC·O7a CR, MO, BA: u ≠ s (una cuenta nueva siempre es otra persona)."""
    if s.recurso and dato(s.recurso, "id") == s.sujeto_id:
        return niega("C15", "Nadie gestiona su propia cuenta.")
    return concede("C15")


@registrar("C17")
def c17(s: Solicitud, rol: str):
    """
    AC·O7d SO: u ≠ s ∧ cuenta(u).estado = ACTIVA.
    GC·O7d AU, RE: g ≠ solicitante ∧ g ≠ u. La verificación de identidad (método
    y fecha) llega en la petición y la valida el servicio antes de autorizar.
    """
    _, operacion = destino(s)
    r = recurso(s)
    if operacion == "SO":
        if dato(r, "id") == s.sujeto_id:
            return niega("C17", "Nadie pide restablecer su propio segundo factor.")
        if dato(r, "estado") != ACTIVA:
            return niega("C17", "La cuenta no está ACTIVA.")
        return concede("C17")
    if dato(r, "estado") != "SOLICITADO":
        return niega("C17", "El restablecimiento ya fue resuelto.")
    if s.sujeto_id in (dato(r, "solicitado_por"), dato(r, "usuario")):
        return niega("C17", "Resuelve un GC distinto de quien lo pidió y de la persona.")
    return concede("C17")


@registrar("C18")
def c18(s: Solicitud, rol: str):
    """AC·O7c CR: TC ∈ R_vig(responsable(d)) ∧ cuenta(responsable(d)).estado = ACTIVA."""
    responsable = del_contexto(s, "responsable")
    if "TC" not in dato(responsable, "roles", "responsable"):
        return niega("C18", "El responsable no tiene el rol TC vigente.")
    if dato(responsable, "estado", "responsable") != ACTIVA:
        return niega("C18", "La cuenta del responsable no está ACTIVA.")
    return concede("C18")


@registrar("C21")
def c21(s: Solicitud, rol: str):
    """
    Todos·O7a L y Todos·O7d CR: recurso.usuario = s (lo ajeno: 404).
    Después del primer factor, registrar otro exige mfa (RG6: reto).
    """
    _, operacion = destino(s)
    r = recurso(s)
    dueno = dato(r, "id") if r.get("tipo") == "usuario" else dato(r, "usuario")
    if dueno != s.sujeto_id:
        return fuera_de_alcance("C21")
    if operacion == "CR" and dato(s.contexto, "tiene_factor", "contexto") and not s.mfa:
        return reautenticar("C21")
    return concede("C21")


@registrar("C22")
def c22(s: Solicitud, rol: str):
    """GC·O9 RS: alerta.sujeto ≠ s (V14). El motivo lo valida el servicio."""
    a = recurso(s)
    if dato(a, "estado") != "ABIERTA":
        return niega("C22", "La alerta ya está resuelta.")
    if dato(a, "sujeto") == s.sujeto_id:
        return niega("C22", "Nadie resuelve una alerta sobre sí mismo (V14).")
    return concede("C22")
