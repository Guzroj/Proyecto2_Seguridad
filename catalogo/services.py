"""
Operaciones sobre el catálogo (O1a fincas/parcelas, O1b datos del propietario).
Mismo patrón que mediciones/services.py: bloquear -> autorizar -> invariantes -> evento.

[RG1] Nada se borra: las bajas cambian el estado.
[C20] La geometría se versiona: modificarla cierra la versión vigente y crea otra.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from nucleo import ganchos
from nucleo.acciones import Accion
from nucleo.errores import DatoInvalido, TransicionInvalida
from nucleo.transicion import bloquear, transicion

from .models import (
    DatosPropietario,
    EstadoCatalogo,
    Finca,
    Parcela,
    ParcelaVersion,
    validar_poligono_geojson,
)


def _evento(usuario, accion, objeto, antes=None, despues=None):
    ganchos.registrar_evento(
        actor=usuario, accion=accion, recurso_tipo=objeto._meta.model_name,
        recurso_id=str(objeto.pk), resultado="OK", antes=antes, despues=despues,
    )


def _texto(valor, nombre, maximo, requerido=True) -> str:
    if not isinstance(valor, str):
        raise DatoInvalido(f"{nombre}: debe ser texto.")
    valor = valor.strip()
    if (requerido and not valor) or len(valor) > maximo:
        raise DatoInvalido(f"{nombre}: requerido, hasta {maximo} caracteres.")
    return valor


def _area(valor) -> Decimal:
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        raise DatoInvalido("area_ha: no es un número.")
    if not d.is_finite() or not (0 < d <= 1_000_000):
        raise DatoInvalido("area_ha: fuera de rango.")
    return d.quantize(Decimal("0.0001"))


def _geometria(valor) -> dict:
    try:
        validar_poligono_geojson(valor)
    except ValidationError as exc:
        raise DatoInvalido(f"geometria: {exc.messages[0]}")
    # PENDIENTE (C20): rechazar traslape con otra parcela vigente -> requiere PostGIS.
    return valor


# ---------------------------------------------------------------------------
# Fincas
# ---------------------------------------------------------------------------
@transicion(Accion.FINCA_CREAR, "finca")
def crear_finca(usuario, nombre: str, ubicacion: str, propietario) -> Finca:
    ganchos.autorizar(usuario, Accion.FINCA_CREAR, None, {"propietario": propietario})
    if not ganchos.usuarios_con_rol("PF").filter(pk=propietario.pk).exists():
        raise DatoInvalido("El propietario debe tener rol de propietario de finca (PF).")
    finca = Finca.objects.create(
        nombre=_texto(nombre, "nombre", 150),
        ubicacion=_texto(ubicacion, "ubicacion", 255),
        propietario=propietario,
        creada_por=usuario,
    )
    _evento(usuario, Accion.FINCA_CREAR, finca, despues={"nombre": finca.nombre, "propietario": propietario.pk})
    return finca


@transicion(Accion.FINCA_MODIFICAR, "finca")
def modificar_finca(usuario, finca, *, nombre: str | None = None, ubicacion: str | None = None) -> Finca:
    finca = bloquear(Finca, finca)
    ganchos.autorizar(usuario, Accion.FINCA_MODIFICAR, finca)
    if finca.estado != EstadoCatalogo.ACTIVA:
        raise TransicionInvalida("La finca está de baja.")
    antes = {"nombre": finca.nombre, "ubicacion": finca.ubicacion}
    if nombre is not None:
        finca.nombre = _texto(nombre, "nombre", 150)
    if ubicacion is not None:
        finca.ubicacion = _texto(ubicacion, "ubicacion", 255)
    finca.save()
    _evento(usuario, Accion.FINCA_MODIFICAR, finca, antes=antes,
            despues={"nombre": finca.nombre, "ubicacion": finca.ubicacion})
    return finca


@transicion(Accion.FINCA_BAJA, "finca")
def dar_baja_finca(usuario, finca) -> Finca:
    finca = bloquear(Finca, finca)
    ganchos.autorizar(usuario, Accion.FINCA_BAJA, finca)
    if finca.estado != EstadoCatalogo.ACTIVA:
        raise TransicionInvalida("La finca ya está de baja.")
    finca.estado = EstadoCatalogo.BAJA
    finca.save()
    for parcela in finca.parcelas.select_for_update().filter(estado=EstadoCatalogo.ACTIVA):
        _baja_parcela(parcela)
    _evento(usuario, Accion.FINCA_BAJA, finca, despues={"estado": finca.estado})
    return finca


@transicion(Accion.PROPIETARIO_REGISTRAR_DATOS, "datospropietario")
def registrar_datos_propietario(usuario, propietario, *, nombre_completo: str, identificacion: str,
                                telefono: str = "", correo: str = "") -> DatosPropietario:
    ganchos.autorizar(usuario, Accion.PROPIETARIO_REGISTRAR_DATOS, None, {"propietario": propietario})
    if not ganchos.usuarios_con_rol("PF").filter(pk=propietario.pk).exists():
        raise DatoInvalido("Solo se registran datos de propietarios del catálogo (C20).")
    valores = {
        "nombre_completo": _texto(nombre_completo, "nombre_completo", 200),
        "identificacion": _texto(identificacion, "identificacion", 30),
        "telefono": _texto(telefono, "telefono", 30, requerido=False),
        "correo": _texto(correo, "correo", 254, requerido=False),
    }
    try:
        with transaction.atomic():
            datos, creado = DatosPropietario.objects.update_or_create(usuario=propietario, defaults=valores)
    except IntegrityError:
        raise DatoInvalido("Esa identificación ya está registrada para otra persona.")
    # Sin datos personales en la bitácora (Ley 8968): solo qué cambió, no el valor.
    _evento(usuario, Accion.PROPIETARIO_REGISTRAR_DATOS, datos,
            despues={"creado": creado, "campos": sorted(valores)})
    return datos


# ---------------------------------------------------------------------------
# Parcelas y versiones de geometría [C20]
# ---------------------------------------------------------------------------
@transicion(Accion.PARCELA_CREAR, "parcela")
def crear_parcela(usuario, finca, *, codigo: str, area_ha, geometria: dict) -> Parcela:
    finca = bloquear(Finca, finca)
    ganchos.autorizar(usuario, Accion.PARCELA_CREAR, finca)
    if finca.estado != EstadoCatalogo.ACTIVA:
        raise TransicionInvalida("La finca está de baja.")
    codigo = _texto(codigo, "codigo", 30)
    if Parcela.objects.filter(finca=finca, codigo=codigo).exists():
        raise DatoInvalido("Ya existe una parcela con ese código en la finca.")
    parcela = Parcela.objects.create(finca=finca, codigo=codigo)
    ParcelaVersion.objects.create(
        parcela=parcela, numero=1, area_ha=_area(area_ha), geometria=_geometria(geometria),
        vigente_desde=timezone.now(), creada_por=usuario,
    )
    _evento(usuario, Accion.PARCELA_CREAR, parcela, despues={"codigo": codigo, "version": 1})
    return parcela


@transicion(Accion.PARCELA_MODIFICAR_GEOMETRIA, "parcela")
def modificar_geometria(usuario, parcela, *, area_ha, geometria: dict) -> ParcelaVersion:
    parcela = bloquear(Parcela, parcela)
    ganchos.autorizar(usuario, Accion.PARCELA_MODIFICAR_GEOMETRIA, parcela)
    if parcela.estado != EstadoCatalogo.ACTIVA:
        raise TransicionInvalida("La parcela está de baja.")
    actual = ParcelaVersion.objects.select_for_update().get(parcela=parcela, vigente_hasta__isnull=True)
    area, geo = _area(area_ha), _geometria(geometria)
    ahora = timezone.now()
    actual.vigente_hasta = ahora  # se cierra, nunca se sobrescribe
    actual.save(update_fields=["vigente_hasta"])
    nueva = ParcelaVersion.objects.create(
        parcela=parcela, numero=actual.numero + 1, area_ha=area, geometria=geo,
        vigente_desde=ahora, creada_por=usuario,
    )
    _evento(usuario, Accion.PARCELA_MODIFICAR_GEOMETRIA, parcela,
            antes={"version": actual.numero, "area_ha": str(actual.area_ha)},
            despues={"version": nueva.numero, "area_ha": str(nueva.area_ha)})
    return nueva


def _baja_parcela(parcela: Parcela):
    parcela.estado = EstadoCatalogo.BAJA
    parcela.save(update_fields=["estado"])
    ParcelaVersion.objects.filter(parcela=parcela, vigente_hasta__isnull=True).update(
        vigente_hasta=timezone.now()
    )


@transicion(Accion.PARCELA_BAJA, "parcela")
def dar_baja_parcela(usuario, parcela) -> Parcela:
    parcela = bloquear(Parcela, parcela)
    ganchos.autorizar(usuario, Accion.PARCELA_BAJA, parcela)
    if parcela.estado != EstadoCatalogo.ACTIVA:
        raise TransicionInvalida("La parcela ya está de baja.")
    _baja_parcela(parcela)
    _evento(usuario, Accion.PARCELA_BAJA, parcela, despues={"estado": parcela.estado})
    return parcela
