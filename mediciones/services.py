"""
Operaciones del flujo de negocio (Persona 2). ÚNICA vía para modificar períodos,
asignaciones, mediciones y enmiendas: ninguna vista debe hacer .save() directo.

Cada operación sigue el mismo orden:
  1. Relee y bloquea el objeto de la BD (SELECT ... FOR UPDATE).
  2. ganchos.autorizar(...)  -> Persona 3 decide rol, alcance, MFA y HBAC.
  3. Verifica invariantes de dominio (estado, H4, separación de funciones). Estas se
     aplican siempre, aunque el motor de autorización tuviera un error (defensa en
     profundidad; la BD además las revalida con restricciones y disparadores).
  4. Cambia el estado, registra el actor (actores(m)) y el evento de bitácora.

Referencias entre corchetes: condiciones de la matriz de la Parte 1 (P1_Matriz).
"""

import secrets
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from catalogo.models import EstadoCatalogo, Finca, Parcela
from nucleo import ganchos
from nucleo.acciones import Accion
from nucleo.errores import (
    DatoInvalido,
    SeparacionDeFunciones,
    SinAsignacion,
    SinVerificadorDisponible,
    TransicionInvalida,
)
from nucleo.transicion import bloquear, transicion

from . import selectores
from .carbono import calcular_toneladas_co2
from .models import (
    AccionActor,
    Asignacion,
    Enmienda,
    EstadoEnmienda,
    EstadoMedicion,
    EstadoPeriodo,
    Medicion,
    MedicionActor,
    MotivoDevolucion,
    Periodo,
)

_azar = secrets.SystemRandom()  # aleatoriedad criptográfica para escoger verificador
CAMPOS_DE_CAMPO = ("numero_arboles", "dap_promedio_cm", "altura_promedio_m", "observaciones")


# ===========================================================================
# Utilidades internas
# ===========================================================================
def _evento(usuario, accion, objeto, antes=None, despues=None, detalle=""):
    ganchos.registrar_evento(
        actor=usuario,
        accion=accion,
        recurso_tipo=objeto._meta.model_name,
        recurso_id=str(objeto.pk),
        resultado="OK",
        antes=antes,
        despues=despues,
        detalle=detalle,
    )


def _foto_medicion(m: Medicion) -> dict:
    return {
        "estado": m.estado,
        "autor": m.autor_id,
        "verificador": m.verificador_id,
        "version_de": str(m.version_de_id) if m.version_de_id else None,
        "numero_arboles": m.numero_arboles,
        "dap_promedio_cm": str(m.dap_promedio_cm),
        "altura_promedio_m": str(m.altura_promedio_m),
        "toneladas_co2": str(m.toneladas_co2) if m.toneladas_co2 is not None else None,
        "motivo_devolucion": m.motivo_devolucion_id,
    }


def _actor(medicion, usuario, accion):
    MedicionActor.objects.create(medicion=medicion, usuario=usuario, accion=accion)


def _decimal(valor, nombre, maximo) -> Decimal:
    if isinstance(valor, bool) or valor is None:
        raise DatoInvalido(f"{nombre}: valor requerido.")
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        raise DatoInvalido(f"{nombre}: no es un número.")
    if not d.is_finite() or not (0 < d <= maximo):
        raise DatoInvalido(f"{nombre}: fuera de rango (0, {maximo}].")
    return d.quantize(Decimal("0.01"))


def _validar_datos_campo(numero_arboles, dap_promedio_cm, altura_promedio_m, observaciones=""):
    """Lista blanca de los datos de campo: tipo y rango físico plausible."""
    if isinstance(numero_arboles, bool) or not isinstance(numero_arboles, int):
        raise DatoInvalido("numero_arboles: debe ser un entero.")
    if not (0 < numero_arboles <= 1_000_000):
        raise DatoInvalido("numero_arboles: fuera de rango.")
    if not isinstance(observaciones, str) or len(observaciones) > 5000:
        raise DatoInvalido("observaciones: texto de hasta 5000 caracteres.")
    return {
        "numero_arboles": numero_arboles,
        "dap_promedio_cm": _decimal(dap_promedio_cm, "dap_promedio_cm", 500),
        "altura_promedio_m": _decimal(altura_promedio_m, "altura_promedio_m", 120),
        "observaciones": observaciones,
    }


def _escoger_verificador(medicion: Medicion, excluir: set[int]):
    """
    Verificador al azar entre los VF activos, excluyendo [C2]: actores de la cadena,
    técnicos (vigentes o pasados) de la finca y período, y el propietario de la finca.
    """
    excluidos = (
        set(excluir)
        | selectores.actores_cadena(medicion)
        | selectores.tecnicos(medicion.parcela.finca_id, medicion.periodo_id)
        | {medicion.autor_id, selectores.propietario_id(medicion)}
    )
    candidatos = list(ganchos.usuarios_con_rol("VF").exclude(pk__in=excluidos))
    if not candidatos:
        raise SinVerificadorDisponible("No hay verificador elegible para esta medición.")
    return _azar.choice(candidatos)


def _exigir_independencia(usuario, medicion: Medicion, quien: str):
    """[C2]/[C3] Nadie aprueba ni autoriza algo en lo que participó o de lo que es dueño."""
    if usuario.pk in selectores.actores_cadena(medicion):
        raise SeparacionDeFunciones(f"El {quien} ya participó en esta medición.")
    if usuario.pk in selectores.tecnicos(medicion.parcela.finca_id, medicion.periodo_id):
        raise SeparacionDeFunciones(f"El {quien} estuvo asignado como técnico a esta finca y período.")
    if usuario.pk == selectores.propietario_id(medicion):
        raise SeparacionDeFunciones(f"El {quien} es el propietario de la finca.")


# ===========================================================================
# O2 · Períodos  [C13]
# ===========================================================================
@transicion(Accion.PERIODO_ABRIR, "periodo")
def abrir_periodo(usuario, nombre: str, fecha_inicio, fecha_fin) -> Periodo:
    ganchos.autorizar(usuario, Accion.PERIODO_ABRIR, None, {"nombre": nombre})
    nombre = (nombre or "").strip()
    if not nombre or len(nombre) > 100:
        raise DatoInvalido("nombre: requerido, hasta 100 caracteres.")
    if fecha_fin < fecha_inicio:
        raise DatoInvalido("La fecha de fin es anterior a la de inicio.")
    if Periodo.objects.filter(nombre=nombre).exists():
        raise DatoInvalido("Ya existe un período con ese nombre.")
    periodo = Periodo.objects.create(
        nombre=nombre, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, abierto_por=usuario
    )
    _evento(usuario, Accion.PERIODO_ABRIR, periodo, despues={"estado": periodo.estado})
    return periodo


@transicion(Accion.PERIODO_PASAR_VERIFICACION, "periodo")
def pasar_a_verificacion(usuario, periodo) -> Periodo:
    periodo = bloquear(Periodo, periodo)
    ganchos.autorizar(usuario, Accion.PERIODO_PASAR_VERIFICACION, periodo)
    if periodo.estado != EstadoPeriodo.ABIERTO:
        raise TransicionInvalida("Solo un período ABIERTO pasa a verificación.")
    # Nota: C13 también exige que no haya paquetes de trabajo vigentes de la PWA sin
    # conexión; la PWA no se implementa en la Parte 2 (ver README, desviaciones).
    periodo.estado = EstadoPeriodo.EN_VERIFICACION
    periodo.en_verificacion_por = usuario
    periodo.en_verificacion_en = timezone.now()
    periodo.save()
    _evento(usuario, Accion.PERIODO_PASAR_VERIFICACION, periodo,
            antes={"estado": EstadoPeriodo.ABIERTO}, despues={"estado": periodo.estado})
    return periodo


@transicion(Accion.PERIODO_CERRAR, "periodo")
def cerrar_periodo(usuario, periodo) -> Periodo:
    periodo = bloquear(Periodo, periodo)
    ganchos.autorizar(usuario, Accion.PERIODO_CERRAR, periodo)
    if periodo.estado != EstadoPeriodo.EN_VERIFICACION:
        raise TransicionInvalida("Solo un período EN_VERIFICACION se cierra (nadie reabre uno cerrado).")
    # [C13] No se cierra con mediciones pendientes. Una versión enmendada devuelta no
    # bloquea: su enmienda quedó DESCARTADA y no vuelve a editarse.
    pendientes = Medicion.objects.filter(periodo=periodo).filter(
        Q(estado=EstadoMedicion.ENVIADA) | Q(estado=EstadoMedicion.DEVUELTA, version_de__isnull=True)
    )
    if pendientes.exists():
        raise TransicionInvalida("Hay mediciones ENVIADA o DEVUELTA en el período.")
    periodo.estado = EstadoPeriodo.CERRADO
    periodo.cerrado_por = usuario
    periodo.cerrado_en = timezone.now()
    periodo.save()
    _evento(usuario, Accion.PERIODO_CERRAR, periodo,
            antes={"estado": EstadoPeriodo.EN_VERIFICACION}, despues={"estado": periodo.estado})
    return periodo


# ===========================================================================
# O3 · Asignaciones  [C19]
# ===========================================================================
@transicion(Accion.ASIGNACION_CREAR, "asignacion")
def crear_asignacion(usuario, tecnico, finca, periodo) -> Asignacion:
    periodo = bloquear(Periodo, periodo)
    finca = bloquear(Finca, finca)
    ganchos.autorizar(usuario, Accion.ASIGNACION_CREAR, None,
                      {"tecnico": tecnico, "finca": finca, "periodo": periodo})
    if periodo.estado == EstadoPeriodo.CERRADO:
        raise TransicionInvalida("No se asigna en un período CERRADO.")
    if finca.estado != EstadoCatalogo.ACTIVA:
        raise TransicionInvalida("La finca está de baja.")
    if not ganchos.usuarios_con_rol("TC").filter(pk=tecnico.pk).exists():
        raise DatoInvalido("El usuario destino no tiene rol de técnico vigente.")
    if tecnico.pk == finca.propietario_id:
        raise SeparacionDeFunciones("Nadie mide una finca de la que es propietario.")
    if tecnico.pk == usuario.pk:
        raise SeparacionDeFunciones("Nadie se asigna fincas a sí mismo.")
    try:
        with transaction.atomic():
            asignacion = Asignacion.objects.create(
                tecnico=tecnico, finca=finca, periodo=periodo, creada_por=usuario
            )
    except IntegrityError:
        raise DatoInvalido("Ese técnico ya tiene una asignación vigente para esa finca y período.")
    _evento(usuario, Accion.ASIGNACION_CREAR, asignacion,
            despues={"tecnico": tecnico.pk, "finca": finca.pk, "periodo": periodo.pk})
    return asignacion


@transicion(Accion.ASIGNACION_TERMINAR, "asignacion")
def terminar_asignacion(usuario, asignacion) -> Asignacion:
    asignacion = bloquear(Asignacion, asignacion)
    ganchos.autorizar(usuario, Accion.ASIGNACION_TERMINAR, asignacion)
    if asignacion.vigente_hasta is not None:
        raise TransicionInvalida("La asignación ya terminó.")
    asignacion.vigente_hasta = timezone.now()
    asignacion.terminada_por = usuario
    asignacion.save()
    _evento(usuario, Accion.ASIGNACION_TERMINAR, asignacion,
            despues={"vigente_hasta": asignacion.vigente_hasta.isoformat()})
    return asignacion


# ===========================================================================
# O4 · Mediciones
# ===========================================================================
def _exigir_editable_por_autor(usuario, m: Medicion, periodo: Periodo):
    """[C1] + [H4] Condiciones para que el TC modifique o envíe su medición."""
    if m.autor_id != usuario.pk:
        raise SeparacionDeFunciones("Solo el autor modifica o envía su medición.")
    if m.version_de_id is not None:
        raise TransicionInvalida("Una versión enmendada no se edita; pasa por verificación.")
    if m.estado == EstadoMedicion.APROBADA or periodo.estado == EstadoPeriodo.CERRADO:
        raise TransicionInvalida("H4: medición aprobada o de período cerrado; solo se corrige por enmienda.")
    if not selectores.asignacion_vigente(usuario.pk, m.parcela.finca_id, periodo.pk):
        raise SinAsignacion("Sin asignación vigente para esta finca y período.")
    borrador_ok = m.estado == EstadoMedicion.BORRADOR and periodo.estado == EstadoPeriodo.ABIERTO
    devuelta_ok = m.estado == EstadoMedicion.DEVUELTA and periodo.estado in (
        EstadoPeriodo.ABIERTO, EstadoPeriodo.EN_VERIFICACION)
    if not (borrador_ok or devuelta_ok):
        raise TransicionInvalida(f"No se edita una medición {m.estado} con el período {periodo.estado}.")


@transicion(Accion.MEDICION_CREAR, "medicion")
def crear_medicion(usuario, parcela, periodo, *, numero_arboles, dap_promedio_cm,
                   altura_promedio_m, observaciones="") -> Medicion:
    parcela = bloquear(Parcela, parcela)
    periodo = bloquear(Periodo, periodo)
    ganchos.autorizar(usuario, Accion.MEDICION_CREAR, parcela, {"periodo": periodo})
    if periodo.estado != EstadoPeriodo.ABIERTO:
        raise TransicionInvalida("Solo se registran mediciones en un período ABIERTO.")
    if parcela.estado != EstadoCatalogo.ACTIVA or parcela.finca.estado != EstadoCatalogo.ACTIVA:
        raise TransicionInvalida("La parcela o la finca está de baja.")
    if not selectores.asignacion_vigente(usuario.pk, parcela.finca_id, periodo.pk):
        raise SinAsignacion("Sin asignación vigente para esta finca y período.")
    version = parcela.version_vigente()
    if version is None:
        raise TransicionInvalida("La parcela no tiene geometría vigente.")
    datos = _validar_datos_campo(numero_arboles, dap_promedio_cm, altura_promedio_m, observaciones)
    m = Medicion.objects.create(
        parcela=parcela,
        parcela_version=version,
        periodo=periodo,
        autor=usuario,  # el servidor fija el autor; nunca viene de la petición [C1]
        toneladas_co2=calcular_toneladas_co2(
            datos["numero_arboles"], datos["dap_promedio_cm"], datos["altura_promedio_m"]),
        **datos,
    )
    _actor(m, usuario, AccionActor.CREAR)
    _evento(usuario, Accion.MEDICION_CREAR, m, despues=_foto_medicion(m))
    return m


@transicion(Accion.MEDICION_MODIFICAR, "medicion")
def modificar_medicion(usuario, medicion, **cambios) -> Medicion:
    m = bloquear(Medicion, medicion)
    periodo = bloquear(Periodo, m.periodo_id)
    ganchos.autorizar(usuario, Accion.MEDICION_MODIFICAR, m)
    _exigir_editable_por_autor(usuario, m, periodo)
    no_permitidos = set(cambios) - set(CAMPOS_DE_CAMPO)
    if no_permitidos:  # lista blanca: evita asignación masiva (estado, autor, verificador...)
        raise DatoInvalido(f"Campos no modificables: {sorted(no_permitidos)}")
    antes = _foto_medicion(m)
    nuevos = {c: cambios.get(c, getattr(m, c)) for c in CAMPOS_DE_CAMPO}
    datos = _validar_datos_campo(**nuevos)
    for campo, valor in datos.items():
        setattr(m, campo, valor)
    m.toneladas_co2 = calcular_toneladas_co2(m.numero_arboles, m.dap_promedio_cm, m.altura_promedio_m)
    m.save()
    _actor(m, usuario, AccionActor.MODIFICAR)
    _evento(usuario, Accion.MEDICION_MODIFICAR, m, antes=antes, despues=_foto_medicion(m))
    return m


@transicion(Accion.MEDICION_ENVIAR, "medicion")
def enviar_medicion(usuario, medicion) -> Medicion:
    m = bloquear(Medicion, medicion)
    periodo = bloquear(Periodo, m.periodo_id)
    ganchos.autorizar(usuario, Accion.MEDICION_ENVIAR, m)
    _exigir_editable_por_autor(usuario, m, periodo)
    antes = _foto_medicion(m)
    # Se escoge un verificador NUEVO en cada envío: quien devolvió quedó en actores(m)
    # y C2 le impide aprobar (ojos frescos tras cada devolución).
    m.verificador = _escoger_verificador(m, excluir=set())
    m.estado = EstadoMedicion.ENVIADA
    m.enviada_en = timezone.now()
    m.motivo_devolucion = None
    m.comentario_devolucion = ""
    m.save()
    _actor(m, usuario, AccionActor.ENVIAR)
    _evento(usuario, Accion.MEDICION_ENVIAR, m, antes=antes, despues=_foto_medicion(m))
    return m


def _exigir_verificable(usuario, m: Medicion, periodo: Periodo):
    """[C2] Estado, verificador asignado e independencia."""
    if m.estado != EstadoMedicion.ENVIADA:
        raise TransicionInvalida("Solo se aprueba o devuelve una medición ENVIADA.")
    if m.version_de_id is None:
        if periodo.estado not in (EstadoPeriodo.ABIERTO, EstadoPeriodo.EN_VERIFICACION):
            raise TransicionInvalida("El período está CERRADO.")
    else:
        enmienda = getattr(m, "enmienda_origen", None)
        if enmienda is None or enmienda.estado != EstadoEnmienda.AUTORIZADA:
            raise TransicionInvalida("La versión no tiene una enmienda AUTORIZADA.")
    if m.verificador_id != usuario.pk:
        raise SeparacionDeFunciones("Solo el verificador asignado por el sistema aprueba o devuelve.")
    _exigir_independencia(usuario, m, "verificador")


@transicion(Accion.MEDICION_APROBAR, "medicion")
def aprobar_medicion(usuario, medicion) -> Medicion:
    m = bloquear(Medicion, medicion)
    periodo = bloquear(Periodo, m.periodo_id)
    ganchos.autorizar(usuario, Accion.MEDICION_APROBAR, m)
    _exigir_verificable(usuario, m, periodo)
    antes = _foto_medicion(m)
    # El servidor recalcula con los datos crudos: nunca se confía en un valor previo.
    m.toneladas_co2 = calcular_toneladas_co2(m.numero_arboles, m.dap_promedio_cm, m.altura_promedio_m)
    m.estado = EstadoMedicion.APROBADA
    m.aprobada_en = timezone.now()
    try:
        with transaction.atomic():
            m.save()
    except IntegrityError:
        # V6: índice único parcial, a lo sumo una original aprobada por parcela y período.
        raise TransicionInvalida("Ya existe una medición aprobada para esta parcela y período.")
    _actor(m, usuario, AccionActor.APROBAR)
    if m.version_de_id is not None:
        enmienda = bloquear(Enmienda, m.enmienda_origen.pk)
        enmienda.estado = EstadoEnmienda.APLICADA
        enmienda.save()
    _evento(usuario, Accion.MEDICION_APROBAR, m, antes=antes, despues=_foto_medicion(m))
    # Persona 4: emisión del certificado en ESTA transacción (si falla, todo se revierte).
    ganchos.on_medicion_aprobada(m)
    return m


@transicion(Accion.MEDICION_DEVOLVER, "medicion")
def devolver_medicion(usuario, medicion, motivo, comentario: str = "") -> Medicion:
    m = bloquear(Medicion, medicion)
    periodo = bloquear(Periodo, m.periodo_id)
    ganchos.autorizar(usuario, Accion.MEDICION_DEVOLVER, m)
    _exigir_verificable(usuario, m, periodo)
    motivo_id = getattr(motivo, "pk", motivo)
    motivo = MotivoDevolucion.objects.filter(pk=motivo_id, activo=True).first()
    if motivo is None:
        raise DatoInvalido("El motivo debe ser uno del catálogo.")
    if not isinstance(comentario, str) or len(comentario) > 2000:
        raise DatoInvalido("comentario: texto de hasta 2000 caracteres.")
    antes = _foto_medicion(m)
    m.estado = EstadoMedicion.DEVUELTA
    m.motivo_devolucion = motivo
    m.comentario_devolucion = comentario
    m.save()
    _actor(m, usuario, AccionActor.DEVOLVER)
    if m.version_de_id is not None:  # [P3] devolver una versión descarta la enmienda
        enmienda = bloquear(Enmienda, m.enmienda_origen.pk)
        enmienda.estado = EstadoEnmienda.DESCARTADA
        enmienda.save()
    _evento(usuario, Accion.MEDICION_DEVOLVER, m, antes=antes, despues=_foto_medicion(m))
    return m


# ===========================================================================
# O5 · Enmiendas  [C11, C3, P2, P3]
# ===========================================================================
def _exigir_enmendable(m: Medicion):
    """[C11] Se enmienda la original (aprobada o de período cerrado) o la versión aprobada más reciente."""
    ultima = selectores.ultima_aprobada(m)
    if m.version_de_id is None:
        if ultima is not None and ultima.pk != m.pk:
            raise TransicionInvalida("Ya hay una versión aprobada: se enmienda la más reciente.")
        if not (m.estado == EstadoMedicion.APROBADA or m.periodo.estado == EstadoPeriodo.CERRADO):
            raise TransicionInvalida("Solo se enmienda una medición aprobada o de un período cerrado.")
    elif ultima is None or ultima.pk != m.pk:
        raise TransicionInvalida("Solo se enmienda la versión aprobada más reciente.")
    if selectores.enmienda_en_curso(m) is not None:
        raise TransicionInvalida("Ya hay una enmienda en curso para esta medición.")


@transicion(Accion.ENMIENDA_SOLICITAR, "medicion")
def solicitar_enmienda(usuario, medicion, *, justificacion: str, numero_arboles,
                       dap_promedio_cm, altura_promedio_m, observaciones="") -> Enmienda:
    m = bloquear(Medicion, medicion)
    ganchos.autorizar(usuario, Accion.ENMIENDA_SOLICITAR, m)
    if selectores.raiz(m).autor_id != usuario.pk:
        raise SeparacionDeFunciones("Solo el autor de la medición original solicita la enmienda.")
    justificacion = (justificacion or "").strip()
    if not justificacion or len(justificacion) > 5000:
        raise DatoInvalido("justificacion: requerida, hasta 5000 caracteres.")
    _exigir_enmendable(m)
    datos = _validar_datos_campo(numero_arboles, dap_promedio_cm, altura_promedio_m, observaciones)
    try:
        with transaction.atomic():
            enmienda = Enmienda.objects.create(
                medicion_original=m, solicitante=usuario, justificacion=justificacion, **datos
            )
    except IntegrityError:
        raise TransicionInvalida("Ya hay una enmienda en curso para esta medición.")
    _actor(m, usuario, AccionActor.SOLICITAR_ENMIENDA)
    _evento(usuario, Accion.ENMIENDA_SOLICITAR, enmienda,
            despues={"medicion": str(m.pk), "estado": enmienda.estado})
    return enmienda


def _exigir_resoluble(usuario, enmienda: Enmienda, m: Medicion):
    """[C3] Doble control e independencia del GC que resuelve."""
    if enmienda.estado != EstadoEnmienda.SOLICITADA:
        raise TransicionInvalida("La enmienda ya fue resuelta.")
    if enmienda.solicitante_id == usuario.pk:
        raise SeparacionDeFunciones("Quien solicita una enmienda no la resuelve.")
    _exigir_independencia(usuario, m, "autorizador")


@transicion(Accion.ENMIENDA_AUTORIZAR, "enmienda")
def autorizar_enmienda(usuario, enmienda) -> Enmienda:
    enmienda = bloquear(Enmienda, enmienda)
    m = bloquear(Medicion, enmienda.medicion_original_id)
    ganchos.autorizar(usuario, Accion.ENMIENDA_AUTORIZAR, enmienda)
    _exigir_resoluble(usuario, enmienda, m)
    original = selectores.raiz(m)
    actores_previos = selectores.actores_cadena(m)

    # [P3] La versión nace ENVIADA, con autor = solicitante y un verificador nuevo.
    version = Medicion(
        parcela=m.parcela,
        parcela_version=m.parcela_version,  # misma geometría que usó la medición enmendada
        periodo=m.periodo,
        autor=enmienda.solicitante,
        estado=EstadoMedicion.ENVIADA,
        version_de=original,
        numero_arboles=enmienda.numero_arboles,
        dap_promedio_cm=enmienda.dap_promedio_cm,
        altura_promedio_m=enmienda.altura_promedio_m,
        observaciones=enmienda.observaciones,
        toneladas_co2=calcular_toneladas_co2(
            enmienda.numero_arboles, enmienda.dap_promedio_cm, enmienda.altura_promedio_m),
        enviada_en=timezone.now(),
    )
    version.verificador = _escoger_verificador(version, excluir={usuario.pk, enmienda.solicitante_id})
    version.save()

    # actores(m') ⊇ actores(cadena) ∪ {solicitante, GC autorizador}
    MedicionActor.objects.bulk_create(
        [MedicionActor(medicion=version, usuario_id=uid, accion=AccionActor.HEREDADO)
         for uid in actores_previos]
        + [
            MedicionActor(medicion=version, usuario_id=enmienda.solicitante_id,
                          accion=AccionActor.SOLICITAR_ENMIENDA),
            MedicionActor(medicion=version, usuario=usuario, accion=AccionActor.AUTORIZAR_ENMIENDA),
        ]
    )
    _actor(m, usuario, AccionActor.AUTORIZAR_ENMIENDA)

    enmienda.estado = EstadoEnmienda.AUTORIZADA
    enmienda.autorizador = usuario
    enmienda.resuelta_en = timezone.now()
    enmienda.version = version
    enmienda.save()
    _evento(usuario, Accion.ENMIENDA_AUTORIZAR, enmienda,
            despues={"estado": enmienda.estado, "version": str(version.pk)})
    return enmienda


@transicion(Accion.ENMIENDA_RECHAZAR, "enmienda")
def rechazar_enmienda(usuario, enmienda, motivo: str) -> Enmienda:
    enmienda = bloquear(Enmienda, enmienda)
    m = bloquear(Medicion, enmienda.medicion_original_id)
    ganchos.autorizar(usuario, Accion.ENMIENDA_RECHAZAR, enmienda)
    _exigir_resoluble(usuario, enmienda, m)
    motivo = (motivo or "").strip()
    if not motivo or len(motivo) > 2000:
        raise DatoInvalido("motivo: requerido, hasta 2000 caracteres.")
    enmienda.estado = EstadoEnmienda.RECHAZADA
    enmienda.autorizador = usuario
    enmienda.resuelta_en = timezone.now()
    enmienda.motivo_rechazo = motivo
    enmienda.save()
    _evento(usuario, Accion.ENMIENDA_RECHAZAR, enmienda, despues={"estado": enmienda.estado})
    return enmienda
