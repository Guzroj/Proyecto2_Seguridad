"""
Funciones de atributos (solo lectura) que usan los servicios y el motor de
autorización (autorizacion/pep.py). Son las funciones de la notación de la matriz:

  actores(m)            usuarios que crearon, enviaron, aprobaron, devolvieron, ... m
  actores_cadena(m)     actores de la original y de todas sus versiones enmendadas
  tecnicos(f, p)        TODOS los técnicos con asignación (vigente o terminada) para (f, p)
  asignacion_vigente(s, f, p)
  fincas_asignadas(s)   fincas con asignación vigente de s en un período no cerrado (C6)
  propietario_id(f)

Siempre leen de la base de datos, nunca de la petición (F3 de la Arquitectura).
Devuelven IDs de usuario (int) para comparar directamente con usuario.pk.
"""

from .models import (
    Asignacion,
    EstadoEnmienda,
    EstadoMedicion,
    EstadoPeriodo,
    Enmienda,
    Medicion,
    MedicionActor,
)


def raiz(medicion: Medicion) -> Medicion:
    """La medición original de una cadena (version_de siempre apunta a la original)."""
    return medicion.version_de if medicion.version_de_id else medicion


def ids_cadena(medicion: Medicion) -> list:
    original = raiz(medicion)
    return [original.pk, *Medicion.objects.filter(version_de=original).values_list("pk", flat=True)]


def actores(medicion: Medicion) -> set[int]:
    return set(
        MedicionActor.objects.filter(medicion=medicion).values_list("usuario_id", flat=True)
    )


def actores_cadena(medicion: Medicion) -> set[int]:
    return set(
        MedicionActor.objects.filter(medicion_id__in=ids_cadena(medicion)).values_list(
            "usuario_id", flat=True
        )
    )


def tecnicos(finca_id: int, periodo_id: int) -> set[int]:
    return set(
        Asignacion.objects.filter(finca_id=finca_id, periodo_id=periodo_id).values_list(
            "tecnico_id", flat=True
        )
    )


def asignacion_vigente(usuario_id: int, finca_id: int, periodo_id: int) -> bool:
    return Asignacion.objects.filter(
        tecnico_id=usuario_id, finca_id=finca_id, periodo_id=periodo_id, vigente_hasta__isnull=True
    ).exists()


def fincas_asignadas(usuario_id: int) -> set[int]:
    return set(
        Asignacion.objects.filter(tecnico_id=usuario_id, vigente_hasta__isnull=True)
        .exclude(periodo__estado=EstadoPeriodo.CERRADO)
        .values_list("finca_id", flat=True)
    )


def propietario_id(medicion: Medicion) -> int:
    return medicion.parcela.finca.propietario_id


def ultima_aprobada(medicion: Medicion) -> Medicion | None:
    """La versión aprobada más reciente de la cadena (la que se enmienda, C11)."""
    return (
        Medicion.objects.filter(pk__in=ids_cadena(medicion), estado=EstadoMedicion.APROBADA)
        .order_by("-aprobada_en")
        .first()
    )


def enmienda_en_curso(medicion: Medicion) -> Enmienda | None:
    return Enmienda.objects.filter(
        medicion_original_id__in=ids_cadena(medicion),
        estado__in=[EstadoEnmienda.SOLICITADA, EstadoEnmienda.AUTORIZADA],
    ).first()
