"""
Matriz de control de acceso como datos (78 celdas: 6 roles × 13 objetos).

Python puro, sin Django: la usa el motor (PDP) dentro de su propio contenedor.

  MATRIZ[rol][objeto] = {operación: Permiso(condiciones, hbac)}
  Una celda vacía ({}) es la denegación explícita "—" de la matriz.

`condiciones` son las Cx que deben cumplirse todas para conceder.
`hbac` son los controles de comportamiento de la celda: H1 (frecuencia), H2 (‡,
fuera de horario: alerta sin cambiar la decisión), H3 (exportación), H4
(secuencia). El motor los evalúa con los contadores que le manda el PEP.

ACCIONES traduce el vocabulario de nucleo.acciones ("<objeto>:<operación>") a
(columna, operación) de la matriz. Una acción que no está aquí se niega (RG1).

"<objeto>:listar" es la operación L sobre una lista: el motor revisa la celda y
las condiciones del sujeto (C5); las de CONDICIONES_DE_ALCANCE las aplica el PEP
como filtro de la consulta (autorizacion.listas), objeto por objeto.
"""

from dataclasses import dataclass

from .roles import AC, AE, GC, PF, ROLES, TC, VF

# ---------------------------------------------------------------------------
# Objetos (columnas) y operaciones
# ---------------------------------------------------------------------------
O1A, O1B, O2, O3, O4, O5, O6 = "O1a", "O1b", "O2", "O3", "O4", "O5", "O6"
O7A, O7B, O7C, O7D, O8, O9 = "O7a", "O7b", "O7c", "O7d", "O8", "O9"
OBJETOS = (O1A, O1B, O2, O3, O4, O5, O6, O7A, O7B, O7C, O7D, O8, O9)

OPERACIONES = {
    "L": "leer", "X": "exportar", "CR": "crear", "MO": "modificar", "EV": "enviar a verificación",
    "AP": "aprobar", "DV": "devolver con motivo", "SO": "solicitar", "AU": "autorizar",
    "RE": "rechazar solicitud", "AB": "abrir período", "TV": "pasar a verificación",
    "CI": "cerrar período", "BA": "dar de baja", "AR": "asignar rol", "RR": "retirar rol",
    "RV": "revocar dispositivo", "RS": "resolver alerta", "SI": "sincronización final",
}
CONTROLES_HBAC = ("H1", "H2", "H3", "H4")


@dataclass(frozen=True)
class Permiso:
    condiciones: tuple[str, ...] = ()
    hbac: tuple[str, ...] = ()


def _p(*etiquetas: str) -> Permiso:
    """_p("C6", "H1", "H2") separa condiciones (Cx) de controles HBAC (Hx)."""
    return Permiso(
        condiciones=tuple(e for e in etiquetas if e.startswith("C")),
        hbac=tuple(e for e in etiquetas if e.startswith("H")),
    )


# AE: en todas las columnas L [C5] y X [C5, H3].
_AE = {"L": _p("C5"), "X": _p("C5", "H3")}

# ‡ en la matriz = H2 (alerta si ocurre fuera de horario).
MATRIZ: dict[str, dict[str, dict[str, Permiso]]] = {
    TC: {
        O1A: {"L": _p("C6", "H1", "H2")},
        O1B: {},
        O2: {"L": _p()},
        O3: {"L": _p("C8")},
        O4: {"L": _p("C10", "H1", "H2"), "CR": _p("C1", "H4"), "MO": _p("C1", "H4"), "EV": _p("C1", "H4")},
        O5: {"SO": _p("C11"), "L": _p("C12")},
        O6: {},
        O7A: {"L": _p("C21")},
        O7B: {},
        O7C: {},
        O7D: {"CR": _p("C21")},
        O8: {},
        O9: {},
    },
    VF: {
        O1A: {"L": _p("C9", "H1", "H2")},
        O1B: {},
        O2: {"L": _p()},
        O3: {},
        O4: {"L": _p("C9", "H1", "H2"), "AP": _p("C2", "H4"), "DV": _p("C2", "H4")},
        O5: {},
        O6: {},
        O7A: {"L": _p("C21")},
        O7B: {},
        O7C: {},
        O7D: {"CR": _p("C21")},
        O8: {},
        O9: {},
    },
    GC: {
        O1A: {"L": _p("H1", "H2")},
        O1B: {},
        O2: {"L": _p(), "AB": _p("C13"), "TV": _p("C13"), "CI": _p("C13")},
        O3: {"L": _p()},
        O4: {"L": _p("H1", "H2"), "SI": _p("C16")},
        O5: {"L": _p(), "AU": _p("C3"), "RE": _p("C3")},
        O6: {"L": _p("H2")},
        O7A: {"L": _p(), "AU": _p("C14")},
        O7B: {"L": _p(), "AU": _p("C7"), "RE": _p("C7")},
        O7C: {},
        # Corrección 3: GC lee (C17) los restablecimientos que puede resolver.
        O7D: {"L": _p("C17"), "CR": _p("C21"), "AU": _p("C17"), "RE": _p("C17")},
        O8: {"L": _p()},
        O9: {"L": _p(), "RS": _p("C22")},
    },
    AC: {
        O1A: {"L": _p("H1", "H2"), "CR": _p("C20"), "MO": _p("C20"), "BA": _p("C20")},
        O1B: {"L": _p("H1", "H2"), "CR": _p("C20"), "MO": _p("C20")},
        O2: {"L": _p()},
        O3: {"L": _p(), "CR": _p("C19"), "MO": _p("C19")},
        O4: {},
        O5: {},
        O6: {},
        O7A: {"L": _p(), "CR": _p("C15"), "MO": _p("C15"), "BA": _p("C15")},
        O7B: {"L": _p(), "AR": _p("C7"), "RR": _p("C7")},
        O7C: {"L": _p(), "CR": _p("C18"), "RV": _p()},
        O7D: {"L": _p(), "SO": _p("C17"), "CR": _p("C21")},  # L solo de metadatos
        O8: {},
        O9: {},
    },
    AE: {objeto: dict(_AE) for objeto in OBJETOS},  # O7d: L solo de metadatos
    PF: {
        O1A: {"L": _p("C4")},
        O1B: {"L": _p("C4")},
        O2: {},
        O3: {},
        O4: {"L": _p("C4")},
        O5: {},
        O6: {"L": _p("C4")},
        O7A: {"L": _p("C21")},
        O7B: {},
        O7C: {},
        O7D: {"CR": _p("C21")},
        O8: {},
        O9: {},
    },
}

# ---------------------------------------------------------------------------
# Vocabulario de acciones -> (columna, operación)
# ---------------------------------------------------------------------------
_LECTURAS = {
    "finca": O1A, "parcela": O1A, "propietario": O1B, "periodo": O2, "asignacion": O3,
    "medicion": O4, "enmienda": O5, "certificado": O6, "cuenta": O7A, "rol": O7B,
    "dispositivo": O7C, "factor": O7D, "bitacora": O8, "alerta": O9,
}

LISTAS: dict[str, tuple[str, str]] = {f"{nombre}:listar": (objeto, "L") for nombre, objeto in _LECTURAS.items()}

# Condiciones de L que dependen del objeto (quién es dueño, autor, asignado...).
# En una lista se aplican como filtro; las demás (C5) las evalúa el motor.
CONDICIONES_DE_ALCANCE = frozenset({"C4", "C6", "C8", "C9", "C10", "C12", "C17", "C21"})

ACCIONES: dict[str, tuple[str, str]] = {
    **{f"{nombre}:leer": (objeto, "L") for nombre, objeto in _LECTURAS.items()},
    **LISTAS,
    **{f"{nombre}:exportar": (objeto, "X") for nombre, objeto in _LECTURAS.items()},
    # O1a / O1b
    "finca:crear": (O1A, "CR"), "finca:modificar": (O1A, "MO"), "finca:baja": (O1A, "BA"),
    "parcela:crear": (O1A, "CR"), "parcela:modificar_geometria": (O1A, "MO"), "parcela:baja": (O1A, "BA"),
    "propietario:registrar_datos": (O1B, "MO"),
    # O2
    "periodo:abrir": (O2, "AB"), "periodo:pasar_verificacion": (O2, "TV"), "periodo:cerrar": (O2, "CI"),
    # O3
    "asignacion:crear": (O3, "CR"), "asignacion:terminar": (O3, "MO"),
    # O4
    "medicion:crear": (O4, "CR"), "medicion:modificar": (O4, "MO"), "medicion:enviar": (O4, "EV"),
    "medicion:aprobar": (O4, "AP"), "medicion:devolver": (O4, "DV"), "medicion:sincronizar": (O4, "SI"),
    # O5
    "enmienda:solicitar": (O5, "SO"), "enmienda:autorizar": (O5, "AU"), "enmienda:rechazar": (O5, "RE"),
    # O7a
    "cuenta:crear": (O7A, "CR"), "cuenta:modificar": (O7A, "MO"), "cuenta:baja": (O7A, "BA"),
    "ventana:autorizar": (O7A, "AU"),
    # O7b
    "rol:asignar": (O7B, "AR"), "rol:retirar": (O7B, "RR"),
    "rol:autorizar": (O7B, "AU"), "rol:rechazar": (O7B, "RE"),
    # O7c
    "dispositivo:registrar": (O7C, "CR"), "dispositivo:revocar": (O7C, "RV"),
    # O7d
    "factor:registrar": (O7D, "CR"),
    "factor:solicitar_restablecimiento": (O7D, "SO"),
    "factor:autorizar_restablecimiento": (O7D, "AU"),
    "factor:rechazar_restablecimiento": (O7D, "RE"),
    # O9
    "alerta:resolver": (O9, "RS"),
}

# Acciones de procesos automáticos: no son de ninguna persona, el motor las niega
# siempre a cualquier sujeto (los procesos no pasan por autorizar()).
ACCIONES_DE_PROCESO = frozenset({"cuenta:activar", "alerta:crear"})


def permiso(rol: str, objeto: str, operacion: str) -> Permiso | None:
    return MATRIZ.get(rol, {}).get(objeto, {}).get(operacion)


assert set(MATRIZ) == set(ROLES), "La matriz debe cubrir los 6 roles."
assert all(set(fila) == set(OBJETOS) for fila in MATRIZ.values()), "Cada rol cubre los 13 objetos."
