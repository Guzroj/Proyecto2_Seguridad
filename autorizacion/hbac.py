"""
Controles HBAC H1–H3 (Python puro, sin Django).

El motor los aplica DESPUÉS de que una celda concede, con los controles de esa
celda (Permiso.hbac). Los contadores los lee el PEP de la base y llegan en
`solicitud.hbac`; las reglas (umbrales, horario) se deciden aquí:

  hora                 instante del servidor con su desfase (ISO 8601)
  h1_suspendido        hay una alerta H1 ABIERTA sobre el sujeto
  h1_resuelta_hace     segundos desde que se levantó la última suspensión H1 (o None)
  h1_lecturas          n(s) sin la petición: lecturas concedidas de O1/O4 en 60 min,
                       solo las posteriores a la última resolución H1 si cae en ese lapso
  h1_media_propia      μ(s) con 90 días de historial propio (None si hay menos),
                       sin contar la última hora (la que mide n(s))
  h1_media_rol         {rol: media del rol} para cuando no hay historial propio
  h3_exportados        registros exportados en 24 h, sin la petición
Y en el contexto de una exportación, `registros` (los de esta petición).

H1 Frecuencia (L de O1 y O4 de TC, VF, GC, AC):
  n(s) = h1_lecturas + 1 (incluida la petición). Si n(s) > max(3·μ(s), 25):
  NIEGA y pide al PEP abrir una alerta H1 (bloqueante). Tras resolverse, la
  primera lectura exige un segundo factor posterior a la resolución.
  Suspensión: con una H1 abierta se niega TODA lectura de O1 y O4 del sujeto,
  con cualquier rol (también PF): se suspende a la persona, no un rol. Es una
  prohibición: prevalece sobre cualquier concesión.
H2 Horario (celdas ‡): fuera de horario concede igual y pide una alerta H2.
H3 Exportación (AE·X): N(s) = h3_exportados + registros. Si N(s) > 500 exige
  segundo factor reciente; si además es fuera de horario, concede con alerta H3.
  Toda exportación concedida lleva marca de agua (la pone quien exporta).

Fuera de horario = sábado, domingo o 00:00–05:00, hora de Costa Rica (UTC−6).
Si falta un contador, el motor niega (RG5).
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from . import matriz
from .reglas import NIEGA, PROHIBICIONES, REAUTENTICAR, AtributoFaltante, Decision, Solicitud, niega

COSTA_RICA = timezone(timedelta(hours=-6))
INICIO_HORARIO = 5          # 00:00–05:00 es fuera de horario
FACTOR_H1 = 3
MINIMO_H1 = 25
LIMITE_H3 = 500
LECTURAS_H1 = frozenset({matriz.O1A, matriz.O1B, matriz.O4})
# Roles con H1 en alguna celda (para la media del rol cuando falta historial propio).
ROLES_H1 = frozenset(rol for rol, fila in matriz.MATRIZ.items()
                     if any("H1" in p.hbac for celda in fila.values() for p in celda.values()))


def _hbac(s: Solicitud, clave: str):
    if clave not in s.hbac:
        raise AtributoFaltante(f"hbac.{clave}")
    return s.hbac[clave]


def _entero(valor, nombre: str) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        raise AtributoFaltante(nombre)
    return valor


def _numero(valor, nombre: str) -> float | None:
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor < 0:
        raise AtributoFaltante(nombre)
    return float(valor)


def fuera_de_horario(s: Solicitud) -> bool:
    hora = _hbac(s, "hora")
    try:
        instante = datetime.fromisoformat(hora) if isinstance(hora, str) else None
    except ValueError:
        instante = None
    if instante is None or instante.tzinfo is None:
        raise AtributoFaltante("hbac.hora")
    local = instante.astimezone(COSTA_RICA)
    return local.weekday() >= 5 or local.hour < INICIO_HORARIO


def media_h1(s: Solicitud, rol: str) -> float:
    """μ(s): la propia con 90 días de historial; si no, la del rol; sin historial del rol, 0."""
    propia = _numero(_hbac(s, "h1_media_propia"), "hbac.h1_media_propia")
    if propia is not None:
        return propia
    medias = _hbac(s, "h1_media_rol")
    if not isinstance(medias, dict):
        raise AtributoFaltante("hbac.h1_media_rol")
    return _numero(medias.get(rol), f"hbac.h1_media_rol.{rol}") or 0.0


def _alerta(control: str, **detalle) -> dict:
    return {"control": control, "detalle": detalle}


def suspension_h1(s: Solicitud):
    """Prohibición: con una H1 abierta, ninguna lectura de O1/O4 del sujeto, con ningún rol."""
    objeto, operacion = matriz.ACCIONES.get(s.accion, (None, None))
    if operacion != "L" or objeto not in LECTURAS_H1:
        return None
    suspendido = _hbac(s, "h1_suspendido")
    if not isinstance(suspendido, bool):
        raise AtributoFaltante("hbac.h1_suspendido")
    if suspendido:
        return niega("H1", "Lecturas suspendidas hasta que un GC resuelva la alerta H1.")
    return None


PROHIBICIONES.append(suspension_h1)


def _h1(s: Solicitud, decision: Decision) -> Decision | None:
    n = _entero(_hbac(s, "h1_lecturas"), "hbac.h1_lecturas") + 1
    media = media_h1(s, decision.rol)
    umbral = max(FACTOR_H1 * media, MINIMO_H1)
    if n > umbral:
        return Decision(
            NIEGA, "H1", "Frecuencia de lectura anómala: lecturas suspendidas hasta que un GC lo revise.",
            rol=decision.rol,
            alertas=(_alerta("H1", accion=s.accion, n=n, umbral=round(umbral, 2), media=round(media, 2)),),
        )
    resuelta_hace = _numero(_hbac(s, "h1_resuelta_hace"), "hbac.h1_resuelta_hace")
    if resuelta_hace is not None and (s.mfa_edad is None or s.mfa_edad >= resuelta_hace):
        return Decision(REAUTENTICAR, "H1", "Tras levantarse la suspensión, se requiere segundo factor.")
    return None


def controlar(s: Solicitud, decision: Decision, objeto: str, operacion: str) -> Decision:
    """Aplica H1–H3 de la celda que concedió. Devuelve la decisión final."""
    controles = set(decision.hbac)
    alertas: list[dict] = []
    if "H1" in controles and operacion == "L" and objeto in LECTURAS_H1:
        negada = _h1(s, decision)
        if negada is not None:
            return negada
    fuera = fuera_de_horario(s) if controles & {"H2", "H3"} else False
    if "H3" in controles and operacion == "X":
        registros = _entero(s.contexto.get("registros"), "contexto.registros")
        total = _entero(_hbac(s, "h3_exportados"), "hbac.h3_exportados") + registros
        if total > LIMITE_H3:
            if not s.mfa:
                return Decision(REAUTENTICAR, "H3", "Exportación grande: se requiere segundo factor reciente.")
            if fuera:
                alertas.append(_alerta("H3", accion=s.accion, registros=total, fuera_de_horario=True))
    if "H2" in controles and fuera:
        alertas.append(_alerta("H2", accion=s.accion, fuera_de_horario=True))
    return replace(decision, alertas=tuple(alertas), marca_agua=operacion == "X")
