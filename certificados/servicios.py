"""
Emisión de certificados (proceso SYS-EMI de la matriz). Implementa el gancho:

    VERDEAZUL_GANCHOS["on_medicion_aprobada"] = "certificados.servicios.emitir"

Contrato (nucleo/ganchos.py): corre DENTRO de la transacción de aprobación, con la medición
ya APROBADA y bloqueada; si lanza, la aprobación completa se revierte. No modifica la
medición (H4 lo rechazaría).

  Medición original:   emite un certificado. Si ya hay uno EMITIDO para la parcela y el
                       período, se niega y se revierte todo (V6).
  Versión enmendada (P2):
      toneladas distintas a las del certificado vigente -> lo revoca y emite uno nuevo que
                       lo referencia (reemplaza_a), en la misma transacción;
      toneladas iguales -> el vigente se mantiene; la corrección queda en la bitácora;
      sin certificado vigente -> emite uno, como en el flujo normal.

Concurrencia: un candado consultivo de transacción por (parcela, período) serializa las
emisiones de la misma parcela y período; el vigente se lee con SELECT ... FOR UPDATE; y el
índice único parcial de la base impide dos EMITIDO aunque todo lo demás fallara.

Datos del propietario: solo por autorizacion.procesos.datos_propietario_para_certificado
(el VF no tiene O1b; la función de la base solo los entrega a quien aprobó).
La firma usa la clave de certificados.firma (Secret Manager -> variable de entorno).
"""

import hashlib
from contextlib import contextmanager

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from autorizacion import procesos
from nucleo import ganchos
from nucleo.acciones import Accion
from nucleo.errores import FueraDeAlcance, TransicionInvalida

from . import firma, formato
from .models import Certificado, EstadoCertificado

FASE_EMISION = "emision"
METODO = "Chave et al. (2014), árbol promedio; carbono 47 % (IPCC 2006); CO2 = C·44/12"


@contextmanager
def fase_emision():
    """Fija app.fase='emision' solo mientras dura la emisión (RLS de certificados) y la restaura."""
    if connection.vendor != "postgresql":
        yield
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.fase', true)")
        previa = cursor.fetchone()[0] or ""
        cursor.execute("SELECT set_config('app.fase', %s, true)", [FASE_EMISION])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.fase', %s, true)", [previa])


def _candado(parcela_id, periodo_id) -> None:
    if connection.vendor != "postgresql":
        return
    clave = int.from_bytes(hashlib.sha256(f"certificado:{parcela_id}:{periodo_id}".encode()).digest()[:8],
                           "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [clave])


def _iso_utc(valor) -> str | None:
    if valor is None:
        return None
    from datetime import timezone as tz

    return valor.astimezone(tz.utc).isoformat().replace("+00:00", "Z")


def _contenido(certificado_id, m, propietario: dict, emitido_en, reemplaza, huella: str) -> dict:
    """Lo que se firma. Solo texto, enteros y decimales como texto (determinista)."""
    parcela, finca, periodo, version = m.parcela, m.parcela.finca, m.periodo, m.parcela_version
    return {
        "certificado": {"id": str(certificado_id), "emitido_en": _iso_utc(emitido_en),
                        "reemplaza_a": str(reemplaza.pk) if reemplaza else None},
        "emisor": {"nombre": formato.EMISOR, "huella_clave": huella},
        "propietario": {"cuenta": finca.propietario_id, "nombre_completo": propietario["nombre_completo"],
                        "identificacion": propietario["identificacion"]},
        "finca": {"id": finca.pk, "nombre": finca.nombre, "ubicacion": finca.ubicacion},
        "parcela": {"id": parcela.pk, "codigo": parcela.codigo, "version_geometria": version.numero,
                    "area_ha": str(version.area_ha),
                    "huella_geometria": hashlib.sha256(formato.canonico(version.geometria)).hexdigest()},
        "periodo": {"id": periodo.pk, "nombre": periodo.nombre, "inicio": periodo.fecha_inicio.isoformat(),
                    "fin": periodo.fecha_fin.isoformat()},
        "medicion": {"id": str(m.pk), "version_de": str(m.version_de_id) if m.version_de_id else None,
                     "aprobada_en": _iso_utc(m.aprobada_en), "numero_arboles": m.numero_arboles,
                     "dap_promedio_cm": str(m.dap_promedio_cm), "altura_promedio_m": str(m.altura_promedio_m)},
        "toneladas_co2": str(m.toneladas_co2),
        "metodo": METODO,
    }


def _emitir_nuevo(m, reemplaza=None) -> Certificado:
    try:
        clave = firma.clave_privada()  # fallo cerrado: sin clave, la aprobación se revierte
    except firma.ClaveNoDisponible:
        raise TransicionInvalida("No se pudo firmar el certificado: la clave de firma no está disponible.")
    huella = formato.huella(formato.publica_cruda(clave.public_key()))
    try:
        propietario = procesos.datos_propietario_para_certificado(m)
    except FueraDeAlcance:
        raise TransicionInvalida("El propietario de la finca no tiene datos registrados (O1b): un AC debe "
                                 "registrarlos antes de certificar.")
    certificado = Certificado(parcela_id=m.parcela_id, periodo_id=m.periodo_id,
                              parcela_version_id=m.parcela_version_id, medicion_id=m.pk,
                              propietario_id=m.parcela.finca.propietario_id, toneladas_co2=m.toneladas_co2,
                              emitido_en=timezone.now(), reemplaza_a=reemplaza)
    contenido = _contenido(certificado.pk, m, propietario, certificado.emitido_en, reemplaza, huella)
    documento = formato.firmar(contenido, clave)
    certificado.contenido = formato.canonico(contenido).decode("utf-8")
    certificado.firma = documento["firma"]["valor"]
    certificado.clave_publica = documento["firma"]["clave_publica"]
    certificado.huella_clave = huella
    try:
        with transaction.atomic():
            certificado.save(force_insert=True)
    except IntegrityError:
        # V6: el índice único parcial ya tiene un EMITIDO para esta parcela y período.
        raise TransicionInvalida("Ya hay un certificado vigente para esta parcela y período.")
    return certificado


def _evento(m, accion, certificado, antes=None, despues=None, detalle=""):
    ganchos.registrar_evento(actor=m.verificador, accion=accion, recurso_tipo="certificado",
                             recurso_id=str(certificado.pk), antes=antes, despues=despues, detalle=detalle)


def _foto(c: Certificado) -> dict:
    return {"estado": c.estado, "parcela": c.parcela_id, "periodo": c.periodo_id, "medicion": str(c.medicion_id),
            "toneladas_co2": str(c.toneladas_co2), "huella_clave": c.huella_clave,
            "reemplaza_a": str(c.reemplaza_a_id) if c.reemplaza_a_id else None}


def emitir(medicion) -> Certificado:
    """Gancho on_medicion_aprobada. Devuelve el certificado vigente tras aplicar P2."""
    from mediciones.models import EstadoMedicion, Medicion

    m = Medicion.objects.select_related("parcela__finca", "parcela_version", "periodo").get(
        pk=getattr(medicion, "pk", medicion))
    if m.estado != EstadoMedicion.APROBADA or m.toneladas_co2 is None:
        raise TransicionInvalida("Solo se certifica una medición APROBADA.")
    with fase_emision():
        _candado(m.parcela_id, m.periodo_id)
        vigente = (Certificado.objects.select_for_update()
                   .filter(parcela_id=m.parcela_id, periodo_id=m.periodo_id, estado=EstadoCertificado.EMITIDO)
                   .first())

        if m.version_de_id is None:
            if vigente is not None:
                raise TransicionInvalida("Ya hay un certificado vigente para esta parcela y período.")
            nuevo = _emitir_nuevo(m)
            _evento(m, Accion.CERTIFICADO_EMITIR, nuevo, despues=_foto(nuevo), detalle="aprobación de la medición")
            return nuevo

        # Versión enmendada (P2): lo decide el sistema con las toneladas recalculadas, no quien pide.
        if vigente is not None and vigente.toneladas_co2 == m.toneladas_co2:
            _evento(m, Accion.CERTIFICADO_MANTENER, vigente, despues=_foto(vigente),
                    detalle=f"enmienda aplicada sin cambio de toneladas (versión {m.pk})")
            return vigente
        if vigente is not None:
            antes = _foto(vigente)
            vigente.estado = EstadoCertificado.REVOCADO
            vigente.revocado_en = timezone.now()
            vigente.revocado_por_medicion = m
            vigente.motivo_revocacion = (f"P2: la enmienda cambia las toneladas de {vigente.toneladas_co2} "
                                         f"a {m.toneladas_co2}.")
            vigente.save(update_fields=["estado", "revocado_en", "revocado_por_medicion", "motivo_revocacion"])
            _evento(m, Accion.CERTIFICADO_REVOCAR, vigente, antes=antes, despues=_foto(vigente),
                    detalle=vigente.motivo_revocacion)
        nuevo = _emitir_nuevo(m, reemplaza=vigente)
        _evento(m, Accion.CERTIFICADO_EMITIR, nuevo, despues=_foto(nuevo),
                detalle=f"versión enmendada {m.pk}" + (f"; reemplaza a {vigente.pk}" if vigente else ""))
        return nuevo
