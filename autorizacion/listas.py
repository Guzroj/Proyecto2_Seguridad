"""
Listas filtradas (PEP, lado Django): cada sujeto solo ve en una lista lo que el
motor le dejaría leer objeto por objeto (p. ej., PF solo lo suyo).

    listar(usuario, "fincas")  ->  QuerySet

1. El motor decide "<objeto>:listar": la celda L de la matriz, las condiciones
   del sujeto (C5 para AE), H1 (cuenta como una lectura), H2 y la suspensión.
   Si niega, se lanza la misma excepción que en una lectura.
2. Con los roles que concedieron, la consulta se filtra por la unión de sus
   alcances. El alcance de un rol es la conjunción de las condiciones de
   CONDICIONES_DE_ALCANCE de su celda, traducidas aquí a filtros de la base.
   Una condición sin traducción para esa lista no deja ver nada (fallo cerrado).

Las traducciones repiten las condiciones de autorizacion.condiciones; la prueba
de consistencia compara, objeto por objeto, la lista con la decisión del motor.
Certificados (O6): el PF ve los suyos EMITIDO o REVOCADO (C4); GC y AE todos.
Bitácora (O8): GC y AE (C5 la decide el motor); nadie más.
"""

from dataclasses import dataclass
from typing import Callable

from django.db.models import Q

from . import cliente, matriz

NADA = Q(pk__in=[])


def _fincas_asignadas(s):
    """C6: asignación vigente del técnico con el período no CERRADO."""
    from mediciones.models import Asignacion, EstadoPeriodo

    return (Asignacion.objects.filter(tecnico_id=s, vigente_hasta__isnull=True)
            .exclude(periodo__estado=EstadoPeriodo.CERRADO).values("finca_id"))


def _fincas_verificadas(s):
    """C9 (O1a): fincas de una medición donde s es verificador o actor."""
    from mediciones.models import Medicion, MedicionActor

    return (Q(pk__in=Medicion.objects.filter(verificador_id=s).values("parcela__finca_id"))
            | Q(pk__in=MedicionActor.objects.filter(usuario_id=s).values("medicion__parcela__finca_id")))


def _mediciones_verificadas(s):
    """C9 (O4): m.verificador = s ∨ s ∈ actores(m)."""
    from mediciones.models import MedicionActor

    return Q(verificador_id=s) | Q(pk__in=MedicionActor.objects.filter(usuario_id=s).values("medicion_id"))


def _por_finca(filtro_fincas):
    """Para parcelas: el mismo alcance que su finca."""
    from catalogo.models import Finca

    return lambda s: Q(finca_id__in=Finca.objects.filter(filtro_fincas(s)).values("pk"))


def _aprobadas_de(s):
    from mediciones.models import EstadoMedicion

    return Q(parcela__finca__propietario_id=s, estado=EstadoMedicion.APROBADA)


_FINCA = {
    "C4": lambda s: Q(propietario_id=s),
    "C6": lambda s: Q(pk__in=_fincas_asignadas(s)),
    "C9": _fincas_verificadas,
}


@dataclass(frozen=True)
class Lista:
    modelo: str                                   # "app.Modelo"
    accion: str                                   # "<objeto>:listar"
    alcance: dict[str, Callable[[int], Q]]        # condición -> filtro con el id del sujeto

    def queryset(self):
        from django.apps import apps

        return apps.get_model(self.modelo).objects.all()


LISTAS: dict[str, Lista] = {
    "fincas": Lista("catalogo.Finca", "finca:listar", _FINCA),
    "parcelas": Lista("catalogo.Parcela", "parcela:listar", {c: _por_finca(f) for c, f in _FINCA.items()}),
    "propietarios": Lista("catalogo.DatosPropietario", "propietario:listar", {"C4": lambda s: Q(usuario_id=s)}),
    "periodos": Lista("mediciones.Periodo", "periodo:listar", {}),
    "asignaciones": Lista("mediciones.Asignacion", "asignacion:listar", {"C8": lambda s: Q(tecnico_id=s)}),
    "mediciones": Lista("mediciones.Medicion", "medicion:listar", {
        "C4": _aprobadas_de, "C10": lambda s: Q(autor_id=s), "C9": _mediciones_verificadas}),
    "enmiendas": Lista("mediciones.Enmienda", "enmienda:listar", {"C12": lambda s: Q(solicitante_id=s)}),
    "cuentas": Lista("cuentas.Usuario", "cuenta:listar", {"C21": lambda s: Q(pk=s)}),
    "sesiones": Lista("cuentas.Sesion", "cuenta:listar", {"C21": lambda s: Q(usuario_id=s)}),
    "ventanas": Lista("cuentas.VentanaAuditoria", "cuenta:listar", {}),  # C21 no aplica: solo GC, AC y AE
    "roles": Lista("cuentas.RolUsuario", "rol:listar", {}),
    "solicitudes_rol": Lista("cuentas.SolicitudRol", "rol:listar", {}),
    "dispositivos": Lista("cuentas.DispositivoCampo", "dispositivo:listar", {}),
    "restablecimientos": Lista("cuentas.RestablecimientoFactor", "factor:listar", {
        "C17": lambda s: Q(estado="SOLICITADO") & ~Q(solicitado_por_id=s) & ~Q(usuario_id=s)}),
    "alertas": Lista("cuentas.Alerta", "alerta:listar", {}),
    "certificados": Lista("certificados.Certificado", "certificado:listar", {
        "C4": lambda s: Q(propietario_id=s, estado__in=["EMITIDO", "REVOCADO"])}),
    "bitacora": Lista("bitacora.Evento", "bitacora:listar", {}),
}


def alcance_de_rol(lista: Lista, rol: str, sujeto_id: int) -> Q:
    """Filtro de lo que `rol` ve en la lista: Q() = todo; NADA si falta una traducción."""
    objeto, operacion = matriz.LISTAS[lista.accion]
    permiso = matriz.permiso(rol, objeto, operacion)
    if permiso is None:
        return NADA
    filtro = Q()
    for condicion in permiso.condiciones:
        if condicion not in matriz.CONDICIONES_DE_ALCANCE:
            continue  # la evaluó el motor
        traduccion = lista.alcance.get(condicion)
        if traduccion is None:
            return NADA
        filtro &= traduccion(sujeto_id)
    return filtro


def listar(usuario, nombre: str):
    """QuerySet de `nombre` filtrado al alcance del sujeto. Lanza si el motor niega la lista."""
    lista = LISTAS[nombre]
    roles = cliente.autorizar_lista(usuario, lista.accion)
    alcances = [alcance_de_rol(lista, rol, usuario.pk) for rol in roles]
    if any(not filtro for filtro in alcances):  # Q() vacío: algún rol ve todo (Q() | x no sirve)
        return lista.queryset()
    filtro = NADA
    for alcance in alcances:
        filtro |= alcance
    return lista.queryset().filter(filtro).distinct()
