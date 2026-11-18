"""
Exportaciones (operación X de la matriz: AE en las 13 columnas, [C5, H3]).

    exportar(usuario, "mediciones") -> Exportacion(nombre_archivo, contenido, registros, sha256)

Orden (fallo cerrado en cada paso):
  1. El alcance: solo filas de los roles del sujeto que tienen X en esa columna, con sus
     condiciones de alcance traducidas a filtros (hoy el AE no tiene ninguna: ve todo); si
     ningún rol tiene X, la consulta queda vacía. RLS vuelve a filtrar en la base.
  2. N = cantidad REAL de registros de ese alcance.
  3. autorizar(usuario, "<objeto>:exportar", None, {"registros": N}): el motor aplica la celda
     (RG1), C5 (ventana del AE) y H3 (N(s) en 24 h > 500 exige segundo factor reciente;
     fuera de horario, alerta). Si niega, no se lee ningún dato.
  4. Se leen exactamente N filas (solo los campos de la lista blanca: nunca hashes de
     contraseña, secretos TOTP, llaves privadas ni el texto cifrado de un factor). Si la
     cantidad cambió entre el conteo y la lectura, se aborta: N siempre es lo exportado.
  5. El archivo CSV lleva la marca de agua de cuentas.hbac.marca_de_agua(usuario) en la
     primera línea, en una columna de cada fila y en la última línea, junto con su SHA-256.
  6. El evento de la exportación (registros, formato, huella del archivo) entra a la
     bitácora en la misma transacción; si no se puede registrar, no hay archivo (RG4).
Las negaciones quedan en la bitácora fuera de la transacción revertida.

Las celdas de texto que empiezan con = + - @ se prefijan con ' (inyección de fórmulas CSV).
"""

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from autorizacion import listas, matriz
from cuentas.hbac import marca_de_agua
from cuentas.selectores import roles_vigentes
from nucleo import ganchos
from nucleo.errores import AccesoDenegado, DatoInvalido, FueraDeAlcance, ReglaNegocio, TransicionInvalida

LIMITE_FILAS = 50_000  # tope duro por archivo (capa gratuita de Cloud Run y Neon)


@dataclass(frozen=True)
class Conjunto:
    titulo: str
    accion: str            # "<objeto>:exportar"
    lista: str             # nombre en autorizacion.listas.LISTAS (modelo y traducciones de alcance)
    campos: tuple[str, ...]
    orden: str = "pk"


CONJUNTOS: dict[str, Conjunto] = {
    "fincas": Conjunto("Fincas (O1a)", "finca:exportar", "fincas",
                       ("id", "nombre", "ubicacion", "propietario_id", "estado", "creada_en", "creada_por_id")),
    "parcelas": Conjunto("Parcelas (O1a)", "parcela:exportar", "parcelas",
                         ("id", "finca_id", "codigo", "estado", "creada_en")),
    "propietarios": Conjunto("Datos personales de propietarios (O1b)", "propietario:exportar", "propietarios",
                             ("usuario_id", "nombre_completo", "identificacion", "telefono", "correo",
                              "actualizado_en")),
    "periodos": Conjunto("Períodos (O2)", "periodo:exportar", "periodos",
                         ("id", "nombre", "fecha_inicio", "fecha_fin", "estado", "abierto_por_id", "abierto_en",
                          "en_verificacion_por_id", "en_verificacion_en", "cerrado_por_id", "cerrado_en")),
    "asignaciones": Conjunto("Asignaciones (O3)", "asignacion:exportar", "asignaciones",
                             ("id", "tecnico_id", "finca_id", "periodo_id", "vigente_desde", "vigente_hasta",
                              "creada_por_id", "terminada_por_id")),
    "mediciones": Conjunto("Mediciones (O4)", "medicion:exportar", "mediciones",
                           ("id", "parcela_id", "parcela_version_id", "periodo_id", "autor_id", "estado",
                            "verificador_id", "version_de_id", "numero_arboles", "dap_promedio_cm",
                            "altura_promedio_m", "toneladas_co2", "motivo_devolucion_id", "creada_en",
                            "enviada_en", "aprobada_en"), "creada_en"),
    "enmiendas": Conjunto("Enmiendas (O5)", "enmienda:exportar", "enmiendas",
                          ("id", "medicion_original_id", "solicitante_id", "justificacion", "estado", "solicitada_en",
                           "numero_arboles", "dap_promedio_cm", "altura_promedio_m", "autorizador_id",
                           "resuelta_en", "motivo_rechazo", "version_id")),
    "certificados": Conjunto("Certificados (O6)", "certificado:exportar", "certificados",
                             ("id", "parcela_id", "periodo_id", "parcela_version_id", "medicion_id", "propietario_id",
                              "toneladas_co2", "estado", "emitido_en", "reemplaza_a_id", "revocado_en",
                              "revocado_por_medicion_id", "motivo_revocacion", "huella_clave", "firma"), "emitido_en"),
    "cuentas": Conjunto("Cuentas (O7a)", "cuenta:exportar", "cuentas",
                        ("id", "username", "estado", "estado_desde", "is_active", "date_joined", "last_login")),
    "ventanas": Conjunto("Ventanas de auditoría (O7a)", "cuenta:exportar", "ventanas",
                         ("id", "auditor_id", "inicio", "fin", "estado", "solicitada_por_id", "solicitada_en",
                          "aprobada_por_id", "aprobada_en")),
    "roles": Conjunto("Roles con vigencia (O7b)", "rol:exportar", "roles",
                      ("id", "usuario_id", "rol", "vigente_desde", "vigente_hasta", "creado_en")),
    "solicitudes_rol": Conjunto("Solicitudes de rol (O7b)", "rol:exportar", "solicitudes_rol",
                                ("id", "usuario_id", "rol", "tipo", "estado", "solicitada_por_id", "solicitada_en",
                                 "resuelta_por_id", "resuelta_en", "motivo")),
    "dispositivos": Conjunto("Dispositivos de campo (O7c)", "dispositivo:exportar", "dispositivos",
                             ("id", "responsable_id", "etiqueta", "algoritmo", "huella", "estado", "registrado_por_id",
                              "registrado_en", "revocado_por_id", "revocado_en", "motivo_revocacion")),
    "restablecimientos": Conjunto("Restablecimientos del segundo factor (O7d, metadatos)", "factor:exportar",
                                  "restablecimientos",
                                  ("id", "usuario_id", "estado", "solicitado_por_id", "solicitado_en",
                                   "resuelto_por_id", "resuelto_en", "metodo_verificacion", "verificado_en")),
    "bitacora": Conjunto("Bitácora (O8)", "bitacora:exportar", "bitacora",
                         ("secuencia", "momento", "actor_id", "actor_nombre", "accion", "recurso_tipo", "recurso_id",
                          "resultado", "antes", "despues", "detalle", "origen", "hash_anterior", "hash"), "secuencia"),
    "alertas": Conjunto("Alertas (O9)", "alerta:exportar", "alertas",
                        ("id", "sujeto_id", "control", "prioridad", "estado", "detalle", "creada_en", "resuelta_por_id",
                         "resuelta_en", "motivo_resolucion")),
}


@dataclass(frozen=True)
class Exportacion:
    nombre_archivo: str
    contenido: bytes
    registros: int
    sha256: str
    marca: str


def _alcance(usuario, conjunto: Conjunto) -> Q | None:
    """Unión de los alcances de los roles del sujeto con X en la columna; None = nada."""
    objeto, _ = matriz.ACCIONES[conjunto.accion]
    lista = listas.LISTAS[conjunto.lista]
    filtros = []
    for rol in sorted(roles_vigentes(usuario)):
        permiso = matriz.permiso(rol, objeto, "X")
        if permiso is None:
            continue
        filtro = Q()
        for condicion in permiso.condiciones:
            if condicion not in matriz.CONDICIONES_DE_ALCANCE:
                continue  # la evalúa el motor (C5)
            traduccion = lista.alcance.get(condicion)
            if traduccion is None:
                return None  # sin traducción: fallo cerrado
            filtro &= traduccion(usuario.pk)
        filtros.append(filtro)
    if not filtros:
        return None
    total = filtros[0]
    for f in filtros[1:]:
        total |= f
    return total


def _celda(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (int, Decimal)):
        return str(valor)
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, (dict, list)):
        valor = json.dumps(valor, ensure_ascii=False, sort_keys=True)
    texto = str(valor)
    if texto[:1] in ("=", "+", "-", "@", "\t", "\r"):
        texto = "'" + texto  # OWASP: inyección de fórmulas en hojas de cálculo
    return texto


def _csv(conjunto: Conjunto, nombre: str, filas: list, marca: str, accion: str) -> bytes:
    salida = io.StringIO()
    escritor = csv.writer(salida, lineterminator="\n")
    escritor.writerow([f"# {marca}"])
    escritor.writerow([f"# Verdeazul Carbono S.A. · {conjunto.titulo} · {len(filas)} registros · {accion}"])
    escritor.writerow([*conjunto.campos, "marca_de_agua"])
    for fila in filas:
        escritor.writerow([*(_celda(v) for v in fila), marca])
    escritor.writerow([f"# Fin de la exportación de {nombre}: {len(filas)} registros. {marca}"])
    return salida.getvalue().encode("utf-8")


def _registrar_negacion(usuario, conjunto: Conjunto, nombre: str, exc: Exception) -> None:
    ganchos.registrar_evento(actor=usuario, accion=conjunto.accion, recurso_tipo=nombre, recurso_id="",
                             resultado="DENEGADO", detalle=f"{type(exc).__name__}: {exc}"[:500])


def exportar(usuario, nombre: str) -> Exportacion:
    conjunto = CONJUNTOS.get(nombre)
    if conjunto is None:
        raise FueraDeAlcance("No encontrado.")
    try:
        with transaction.atomic():
            alcance = _alcance(usuario, conjunto)
            consulta = listas.LISTAS[conjunto.lista].queryset()
            consulta = consulta.filter(alcance) if alcance is not None else consulta.none()
            consulta = consulta.order_by(conjunto.orden, "pk") if conjunto.orden != "pk" else consulta.order_by("pk")
            registros = consulta.count()
            if registros > LIMITE_FILAS:
                raise DatoInvalido(f"La exportación supera el máximo de {LIMITE_FILAS} registros por archivo.")
            # H3 y C5: el motor decide con la cantidad REAL de registros (sin ella, niega).
            ganchos.autorizar(usuario, conjunto.accion, None, {"registros": registros})
            filas = list(consulta.values_list(*conjunto.campos)[:registros])
            if len(filas) != registros:
                raise TransicionInvalida("Los datos cambiaron durante la exportación; intente de nuevo.")
            momento = timezone.now()
            marca = marca_de_agua(usuario, momento)
            contenido = _csv(conjunto, nombre, filas, marca, conjunto.accion)
            huella = hashlib.sha256(contenido).hexdigest()
            ganchos.registrar_evento(
                actor=usuario, accion=conjunto.accion, recurso_tipo=nombre, recurso_id="", resultado="OK",
                despues={"registros": registros, "formato": "csv", "sha256": huella, "campos": list(conjunto.campos)},
                detalle=marca,
            )
    except (AccesoDenegado, FueraDeAlcance, ReglaNegocio) as exc:
        _registrar_negacion(usuario, conjunto, nombre, exc)
        raise
    archivo = f"verdeazul-{nombre}-{timezone.localtime(momento):%Y%m%d-%H%M%S}.csv"
    return Exportacion(nombre_archivo=archivo, contenido=contenido, registros=registros, sha256=huella, marca=marca)
