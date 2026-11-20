"""
Operaciones sobre cuentas (O7a) y roles (O7b). ÚNICA vía para cambiar el estado
de una cuenta o sus roles.

`cambiar_estado` es la pieza de dominio: valida la transición, relee y bloquea
la cuenta y fija el nuevo estado. No autoriza ni registra el evento: lo hacen
los servicios que la usan (alta, baja, activación de ventanas de auditoría,
tarea diaria), porque cada uno tiene su propio actor y su propia celda de la
matriz.

`agregar_rol` y `terminar_rol` son las piezas de dominio de O7b con el mismo
criterio: aplican las invariantes que valen siempre (rol conocido, sin
duplicados, pares excluyentes E) y dejan la autorización, la doble aprobación
de GC/AE y el cierre de sesiones a los servicios de AR/RR (C7).

C15 (AC·O7a CR, MO, BA) se implementa en crear_cuenta, modificar_cuenta,
dar_de_baja y enlace_activacion; C7 (AC·O7b AR, RR; GC·O7b AU, RE) en
asignar_rol, retirar_rol, autorizar_solicitud y rechazar_solicitud; C14
(GC·O7a AU) en aprobar_ventana; C18 (AC·O7c CR, RV) en registrar_dispositivo y
revocar_dispositivo; C17 (AC·O7d SO; GC·O7d AU, RE) en
solicitar_restablecimiento, autorizar_restablecimiento y
rechazar_restablecimiento; C22 (GC·O9 RS) en resolver_alerta; el cierre de la
baja de C16 (GC·O4 SI) en cerrar_baja_tecnico. Estos sí autorizan con el motor y registran el
evento en la bitácora. procesar_ventanas es la tarea diaria (proceso sin
usuario): activa las cuentas AE el día de inicio y da de baja las vencidas.
crear_alerta es la vía del motor HBAC (proceso) para crear en O9 y
registrar_actividad la de sus contadores (lecturas y exportaciones concedidas).
"""

from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from autorizacion import roles
from nucleo import ganchos
from nucleo.acciones import Accion
from nucleo.errores import DatoInvalido, SeparacionDeFunciones, TransicionInvalida
from nucleo.transicion import bloquear, transicion

from . import llaves, selectores, sesion
from .models import (
    DIAS_MAXIMOS_VENTANA,
    ActividadHBAC,
    Alerta,
    ControlHBAC,
    DispositivoCampo,
    EstadoAlerta,
    EstadoCuenta,
    EstadoDispositivo,
    EstadoRestablecimiento,
    EstadoSolicitudRol,
    EstadoVentana,
    MetodoVerificacion,
    MotivoCierre,
    PrioridadAlerta,
    RestablecimientoFactor,
    RolUsuario,
    SolicitudRol,
    TipoActividad,
    TipoSolicitudRol,
    Usuario,
    VentanaAuditoria,
)

TRANSICIONES: dict[str, frozenset[str]] = {
    EstadoCuenta.PENDIENTE: frozenset({EstadoCuenta.ACTIVA, EstadoCuenta.INACTIVA}),
    EstadoCuenta.ACTIVA: frozenset({EstadoCuenta.EN_BAJA, EstadoCuenta.INACTIVA}),
    EstadoCuenta.EN_BAJA: frozenset({EstadoCuenta.INACTIVA}),
    EstadoCuenta.INACTIVA: frozenset(),
}


def transicion_permitida(actual: str, nuevo: str) -> bool:
    return nuevo in TRANSICIONES.get(actual, frozenset())


def cambiar_estado(cuenta, nuevo: str) -> tuple[Usuario, str]:
    """
    Pasa `cuenta` (objeto o pk) al estado `nuevo`. Devuelve (cuenta, estado_anterior).
    Lanza FueraDeAlcance si no existe y TransicionInvalida si el paso no está permitido.
    """
    if nuevo not in EstadoCuenta.values:
        raise TransicionInvalida(f"Estado de cuenta desconocido: {nuevo!r}.")
    with transaction.atomic():
        cuenta = bloquear(Usuario, cuenta)
        anterior = cuenta.estado
        if not transicion_permitida(anterior, nuevo):
            raise TransicionInvalida(f"Una cuenta {anterior} no pasa a {nuevo}.")
        cuenta.estado = nuevo
        cuenta.estado_desde = timezone.now()
        cuenta.save(update_fields=["estado", "estado_desde"])
    return cuenta, anterior


# ===========================================================================
# O7b · Roles con vigencia y pares excluyentes (E)
# ===========================================================================
def _traslapadas(usuario_id, desde, hasta):
    """Pertenencias del usuario cuya vigencia se cruza con [desde, hasta)."""
    q = RolUsuario.objects.filter(usuario_id=usuario_id).filter(
        Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gt=desde)
    )
    if hasta is not None:
        q = q.filter(vigente_desde__lt=hasta)
    return q


def agregar_rol(usuario, rol: str, *, desde=None, hasta=None) -> RolUsuario:
    """
    Da a `usuario` el rol `rol` durante [desde, hasta) (por defecto desde ahora y sin fin).
    Niega si ya tiene ese rol en un tramo que se cruza, o si tiene un rol excluyente
    con vigencia que se cruza (E), sea vigente hoy o futura.
    """
    if not roles.es_rol(rol):
        raise DatoInvalido(f"Rol desconocido: {rol!r}.")
    desde = desde or timezone.now()
    if hasta is not None and hasta <= desde:
        raise DatoInvalido("La vigencia del rol termina antes de empezar.")
    with transaction.atomic():
        # Bloquear la cuenta serializa las asignaciones concurrentes al mismo usuario:
        # sin esto, dos peticiones simultáneas podrían dar TC y VF a la vez.
        usuario = bloquear(Usuario, usuario)
        cruzados = set(_traslapadas(usuario.pk, desde, hasta).values_list("rol", flat=True))
        if rol in cruzados:
            raise TransicionInvalida(f"El usuario ya tiene el rol {rol} en ese período.")
        choque = roles.conflictos(rol, cruzados)
        if choque:
            raise SeparacionDeFunciones(
                f"El rol {rol} es excluyente con {', '.join(sorted(choque))}."
            )
        return RolUsuario.objects.create(
            usuario=usuario, rol=rol, vigente_desde=desde, vigente_hasta=hasta
        )


def terminar_rol(usuario, rol: str, *, en=None) -> RolUsuario:
    """Cierra en `en` (por defecto ahora) la vigencia del rol que está vigente en ese instante."""
    if not roles.es_rol(rol):
        raise DatoInvalido(f"Rol desconocido: {rol!r}.")
    en = en or timezone.now()
    with transaction.atomic():
        usuario = bloquear(Usuario, usuario)
        pertenencia = (
            RolUsuario.objects.select_for_update()
            .filter(usuario=usuario, rol=rol, vigente_desde__lt=en)
            .filter(Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gt=en))
            .first()
        )
        if pertenencia is None:
            raise TransicionInvalida(f"El usuario no tiene el rol {rol} vigente.")
        pertenencia.vigente_hasta = en
        pertenencia.save(update_fields=["vigente_hasta"])
        return pertenencia


# ===========================================================================
# C15 · AC·O7a CR, MO, BA
# ===========================================================================
CAMPOS_MODIFICABLES = ("first_name", "last_name", "email")


def _evento(usuario, accion, cuenta, antes=None, despues=None, detalle=""):
    # Sin datos personales en la bitácora: solo id, estado, rol y nombres de campos.
    ganchos.registrar_evento(
        actor=usuario,
        accion=accion,
        recurso_tipo="cuenta",
        recurso_id=str(cuenta.pk),
        antes=antes,
        despues=despues,
        detalle=detalle,
    )


def _exigir_otra_cuenta(usuario, cuenta) -> None:
    # C15: u ≠ s.
    if cuenta.pk == usuario.pk:
        raise SeparacionDeFunciones("Nadie gestiona su propia cuenta.")


def _texto(valor, nombre: str, maximo: int) -> str:
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise DatoInvalido(f"{nombre}: debe ser texto.")
    valor = valor.strip()
    if len(valor) > maximo:
        raise DatoInvalido(f"{nombre}: hasta {maximo} caracteres.")
    return valor


def _correo(valor) -> str:
    valor = _texto(valor, "email", 254)
    if valor:
        try:
            validate_email(valor)
        except ValidationError:
            raise DatoInvalido("email: formato inválido.")
    return valor


def _username(valor) -> str:
    valor = _texto(valor, "username", 150)
    if not valor:
        raise DatoInvalido("username: requerido.")
    try:
        Usuario.username_validator(valor)
    except ValidationError:
        raise DatoInvalido("username: solo letras, dígitos y @ . + - _")
    # Sin homónimos que solo difieren en mayúsculas (cuentas nominales inequívocas).
    if Usuario.objects.filter(username__iexact=valor).exists():
        raise DatoInvalido("username: ya existe una cuenta con ese nombre.")
    return valor


@transicion(Accion.CUENTA_CREAR, "cuenta")
def crear_cuenta(usuario, *, username, rol, first_name="", last_name="", email="",
                 inicio=None, fin=None) -> Usuario:
    """
    C15 CR. La cuenta nace sin contraseña utilizable: la persona la fija con el
    enlace de activación (enlace_activacion), que AC le entrega por otro canal.
    Con rol GC queda PENDIENTE (sin rol) con una solicitud que otro GC autoriza
    (C7). Con rol AE exige la ventana [inicio, fin] (fechas, máximo 30 días) y
    queda PENDIENTE hasta que un GC la apruebe y llegue el día de inicio (C14).
    Con otro rol queda ACTIVA con ese rol.
    """
    if not roles.es_rol(rol):
        raise DatoInvalido(f"Rol desconocido: {rol!r}.")
    if rol == roles.AE:
        inicio, fin = _validar_ventana(inicio, fin)
    elif inicio is not None or fin is not None:
        raise DatoInvalido("Solo las cuentas AE llevan ventana de auditoría.")
    ganchos.autorizar(usuario, Accion.CUENTA_CREAR, None, {"rol": rol})
    datos = {
        "username": _username(username),
        "first_name": _texto(first_name, "first_name", 150),
        "last_name": _texto(last_name, "last_name", 150),
        "email": _correo(email),
    }
    cuenta = Usuario.objects.create_user(password=None, **datos)
    if rol == roles.GC:
        # C7: la cuenta se activa cuando otro GC autoriza la solicitud.
        _solicitar(usuario, cuenta, rol, TipoSolicitudRol.ASIGNAR)
    elif rol == roles.AE:
        VentanaAuditoria.objects.create(auditor=cuenta, inicio=inicio, fin=fin, solicitada_por=usuario)
    else:
        cuenta, _ = cambiar_estado(cuenta, EstadoCuenta.ACTIVA)
        agregar_rol(cuenta, rol)
    _evento(usuario, Accion.CUENTA_CREAR, cuenta, despues={"estado": cuenta.estado, "rol": rol})
    return cuenta


@transicion(Accion.CUENTA_MODIFICAR, "cuenta")
def modificar_cuenta(usuario, cuenta, **cambios) -> Usuario:
    """C15 MO: nombre, apellidos y correo. El estado, los roles y la contraseña tienen su propia vía."""
    cuenta = bloquear(Usuario, cuenta)
    ganchos.autorizar(usuario, Accion.CUENTA_MODIFICAR, cuenta)
    _exigir_otra_cuenta(usuario, cuenta)
    ajenos = set(cambios) - set(CAMPOS_MODIFICABLES)
    if ajenos:
        raise DatoInvalido(f"Campos no modificables: {', '.join(sorted(ajenos))}.")
    if cuenta.estado == EstadoCuenta.INACTIVA:
        raise TransicionInvalida("Una cuenta INACTIVA no se modifica.")
    limpios = {
        campo: _correo(valor) if campo == "email" else _texto(valor, campo, 150)
        for campo, valor in cambios.items()
    }
    modificados = sorted(c for c, v in limpios.items() if getattr(cuenta, c) != v)
    if not modificados:
        return cuenta
    for campo in modificados:
        setattr(cuenta, campo, limpios[campo])
    cuenta.save(update_fields=modificados)
    _evento(usuario, Accion.CUENTA_MODIFICAR, cuenta, despues={"campos": modificados})
    return cuenta


@transicion(Accion.CUENTA_BAJA, "cuenta")
def dar_de_baja(usuario, cuenta) -> Usuario:
    """
    C15 BA: EN_BAJA si quedan registros sin sincronizar (luego C16 la pasa a
    INACTIVA); si no, INACTIVA. Cierra todas las sesiones de la cuenta.
    """
    cuenta = bloquear(Usuario, cuenta)
    ganchos.autorizar(usuario, Accion.CUENTA_BAJA, cuenta)
    _exigir_otra_cuenta(usuario, cuenta)
    if cuenta.estado not in (EstadoCuenta.PENDIENTE, EstadoCuenta.ACTIVA):
        # EN_BAJA solo pasa a INACTIVA tras la sincronización final (C16).
        raise TransicionInvalida(f"Una cuenta {cuenta.estado} no se da de baja.")
    pendiente = cuenta.estado == EstadoCuenta.ACTIVA and selectores.registros_sin_sincronizar(cuenta)
    nuevo = EstadoCuenta.EN_BAJA if pendiente else EstadoCuenta.INACTIVA
    cuenta, anterior = cambiar_estado(cuenta, nuevo)
    cerradas = sesion.cerrar_sesiones(cuenta, MotivoCierre.FORZADO)
    _evento(usuario, Accion.CUENTA_BAJA, cuenta, antes={"estado": anterior},
            despues={"estado": nuevo, "sesiones_cerradas": cerradas})
    return cuenta


@transicion(Accion.CUENTA_MODIFICAR, "cuenta")
def enlace_activacion(usuario, cuenta) -> str:
    """
    Ruta de un solo uso para que la persona fije su primera contraseña. Solo se
    emite mientras la cuenta no tenga contraseña: así AC nunca puede tomar una
    cuenta que ya está en uso. Deja de servir al fijar la contraseña o al vencer
    PASSWORD_RESET_TIMEOUT.
    """
    cuenta = bloquear(Usuario, cuenta)
    ganchos.autorizar(usuario, Accion.CUENTA_MODIFICAR, cuenta)
    _exigir_otra_cuenta(usuario, cuenta)
    if cuenta.estado not in (EstadoCuenta.PENDIENTE, EstadoCuenta.ACTIVA):
        raise TransicionInvalida(f"Una cuenta {cuenta.estado} no se activa.")
    if cuenta.has_usable_password():
        raise TransicionInvalida("La cuenta ya tiene contraseña.")
    _evento(usuario, Accion.CUENTA_MODIFICAR, cuenta, detalle="enlace de activación emitido")
    return reverse(
        "cuenta_activar",
        kwargs={"uidb64": urlsafe_base64_encode(force_bytes(cuenta.pk)), "token": default_token_generator.make_token(cuenta)},
    )


def establecer_primera_clave(cuenta, clave: str) -> Usuario:
    """Pieza de dominio del enlace de activación (la vista ya validó el enlace y la contraseña)."""
    with transaction.atomic():
        cuenta = bloquear(Usuario, cuenta)
        if cuenta.has_usable_password() or cuenta.estado not in (EstadoCuenta.PENDIENTE, EstadoCuenta.ACTIVA):
            raise TransicionInvalida("El enlace de activación ya no es válido.")
        cuenta.set_password(clave)
        cuenta.save(update_fields=["password"])
    return cuenta


# ===========================================================================
# C7 · AC·O7b AR, RR; GC·O7b AU, RE
# ===========================================================================
def _evento_rol(usuario, accion, cuenta, rol, **extra):
    ganchos.registrar_evento(
        actor=usuario,
        accion=accion,
        recurso_tipo="rol",
        recurso_id=str(cuenta.pk),
        despues={"rol": rol, **extra},
    )


def _rol_valido(rol) -> str:
    if not roles.es_rol(rol):
        raise DatoInvalido(f"Rol desconocido: {rol!r}.")
    return rol


def _exigir_sin_conflictos(cuenta, rol) -> None:
    # Se valida al pedir y se reevalúa al hacerse efectiva (agregar_rol).
    vigentes = selectores.roles_vigentes(cuenta)
    if rol in vigentes:
        raise TransicionInvalida(f"La cuenta ya tiene el rol {rol}.")
    choque = roles.conflictos(rol, vigentes)
    if choque:
        raise SeparacionDeFunciones(f"El rol {rol} es excluyente con {', '.join(sorted(choque))}.")


def _solicitar(usuario, cuenta, rol, tipo) -> SolicitudRol:
    try:
        with transaction.atomic():
            return SolicitudRol.objects.create(usuario=cuenta, rol=rol, tipo=tipo, solicitada_por=usuario)
    except IntegrityError:
        raise TransicionInvalida(f"Ya hay una solicitud en curso del rol {rol} para esta cuenta.")


@transicion(Accion.ROL_ASIGNAR, "rol")
def asignar_rol(usuario, cuenta, rol) -> RolUsuario | SolicitudRol:
    """
    C7 AR: u ≠ s, cuenta ACTIVA y sin roles excluyentes (E). GC y AE quedan como
    solicitud hasta que un GC la autorice; los demás roles tienen efecto ya y se
    cierran las sesiones de la cuenta.
    """
    _rol_valido(rol)
    cuenta = bloquear(Usuario, cuenta)
    ganchos.autorizar(usuario, Accion.ROL_ASIGNAR, cuenta, {"rol": rol})
    _exigir_otra_cuenta(usuario, cuenta)
    if cuenta.estado != EstadoCuenta.ACTIVA:
        raise TransicionInvalida(f"Solo se asignan roles a cuentas ACTIVA (esta está {cuenta.estado}).")
    _exigir_sin_conflictos(cuenta, rol)
    if rol in roles.ROLES_CON_DOBLE_APROBACION:
        solicitud = _solicitar(usuario, cuenta, rol, TipoSolicitudRol.ASIGNAR)
        _evento_rol(usuario, Accion.ROL_ASIGNAR, cuenta, rol, solicitud=solicitud.pk)
        return solicitud
    pertenencia = agregar_rol(cuenta, rol)
    cerradas = sesion.cerrar_sesiones(cuenta)
    _evento_rol(usuario, Accion.ROL_ASIGNAR, cuenta, rol, sesiones_cerradas=cerradas)
    return pertenencia


@transicion(Accion.ROL_RETIRAR, "rol")
def retirar_rol(usuario, cuenta, rol) -> RolUsuario | SolicitudRol:
    """C7 RR: u ≠ s. GC y AE quedan como solicitud; los demás se retiran ya y se cierran sus sesiones."""
    _rol_valido(rol)
    cuenta = bloquear(Usuario, cuenta)
    ganchos.autorizar(usuario, Accion.ROL_RETIRAR, cuenta, {"rol": rol})
    _exigir_otra_cuenta(usuario, cuenta)
    if rol not in selectores.roles_vigentes(cuenta):
        raise TransicionInvalida(f"La cuenta no tiene el rol {rol} vigente.")
    if rol in roles.ROLES_CON_DOBLE_APROBACION:
        solicitud = _solicitar(usuario, cuenta, rol, TipoSolicitudRol.RETIRAR)
        _evento_rol(usuario, Accion.ROL_RETIRAR, cuenta, rol, solicitud=solicitud.pk)
        return solicitud
    pertenencia = terminar_rol(cuenta, rol)
    cerradas = sesion.cerrar_sesiones(cuenta)
    _evento_rol(usuario, Accion.ROL_RETIRAR, cuenta, rol, sesiones_cerradas=cerradas)
    return pertenencia


def _resolver(usuario, solicitud, accion):
    solicitud = bloquear(SolicitudRol, solicitud)
    ganchos.autorizar(usuario, accion, solicitud)
    if solicitud.estado != EstadoSolicitudRol.SOLICITADA:
        raise TransicionInvalida(f"La solicitud ya está {solicitud.estado}.")
    if usuario.pk in (solicitud.solicitada_por_id, solicitud.usuario_id):
        raise SeparacionDeFunciones("Resuelve un GC distinto de quien la pidió y de la persona destino.")
    return solicitud


def _cerrar_solicitud(usuario, solicitud, estado, motivo="") -> None:
    solicitud.estado = estado
    solicitud.resuelta_por = usuario
    solicitud.resuelta_en = timezone.now()
    solicitud.motivo = motivo
    solicitud.save(update_fields=["estado", "resuelta_por", "resuelta_en", "motivo"])


@transicion(Accion.ROL_AUTORIZAR, "solicitud_rol")
def autorizar_solicitud(usuario, solicitud) -> SolicitudRol:
    """
    C7 AU: el cambio tiene efecto ahora. E se reevalúa (si en el intervalo la
    cuenta recibió un rol excluyente, se niega y la solicitud sigue en curso).
    Una cuenta PENDIENTE creada con rol GC se activa aquí.
    """
    solicitud = _resolver(usuario, solicitud, Accion.ROL_AUTORIZAR)
    cuenta = bloquear(Usuario, solicitud.usuario_id)
    if cuenta.estado not in (EstadoCuenta.PENDIENTE, EstadoCuenta.ACTIVA):
        raise TransicionInvalida(f"La cuenta está {cuenta.estado}.")
    _cerrar_solicitud(usuario, solicitud, EstadoSolicitudRol.AUTORIZADA)
    if solicitud.tipo == TipoSolicitudRol.ASIGNAR:
        _exigir_sin_conflictos(cuenta, solicitud.rol)
        if cuenta.estado == EstadoCuenta.PENDIENTE:
            cuenta, _ = cambiar_estado(cuenta, EstadoCuenta.ACTIVA)
        agregar_rol(cuenta, solicitud.rol)
    else:
        terminar_rol(cuenta, solicitud.rol)
    cerradas = sesion.cerrar_sesiones(cuenta)
    _evento_rol(usuario, Accion.ROL_AUTORIZAR, cuenta, solicitud.rol, solicitud=solicitud.pk,
                tipo=solicitud.tipo, estado_cuenta=cuenta.estado, sesiones_cerradas=cerradas)
    return solicitud


@transicion(Accion.ROL_RECHAZAR, "solicitud_rol")
def rechazar_solicitud(usuario, solicitud, *, motivo="") -> SolicitudRol:
    """C7 RE: la solicitud queda RECHAZADA y nada cambia (una cuenta PENDIENTE sigue PENDIENTE)."""
    solicitud = _resolver(usuario, solicitud, Accion.ROL_RECHAZAR)
    _cerrar_solicitud(usuario, solicitud, EstadoSolicitudRol.RECHAZADA, _texto(motivo, "motivo", 500))
    cuenta = solicitud.usuario
    _evento_rol(usuario, Accion.ROL_RECHAZAR, cuenta, solicitud.rol, solicitud=solicitud.pk, tipo=solicitud.tipo)
    return solicitud


# ===========================================================================
# C14 · GC·O7a AU (ventana de auditoría) y tarea diaria
# ===========================================================================
def _validar_ventana(inicio, fin) -> tuple[date, date]:
    if not isinstance(inicio, date) or not isinstance(fin, date) or isinstance(inicio, datetime) \
            or isinstance(fin, datetime):
        raise DatoInvalido("La ventana de auditoría necesita fechas de inicio y fin.")
    if fin < inicio:
        raise DatoInvalido("La ventana termina antes de empezar.")
    if (fin - inicio).days > DIAS_MAXIMOS_VENTANA:
        raise DatoInvalido(f"La ventana dura como máximo {DIAS_MAXIMOS_VENTANA} días.")
    if fin < selectores.hoy():
        raise DatoInvalido("La ventana ya terminó.")
    return inicio, fin


def comienzo_del_dia(dia: date) -> datetime:
    """00:00 de `dia` en hora de Costa Rica."""
    return timezone.make_aware(datetime.combine(dia, time.min))


@transicion(Accion.VENTANA_AUTORIZAR, "ventana")
def aprobar_ventana(usuario, ventana) -> VentanaAuditoria:
    """
    C14: v.estado = PENDIENTE ∧ (v.fin − v.inicio) ≤ 30 d ∧ s ≠ v.solicitada_por
    ∧ s ≠ v.auditor. El rol AE queda vigente exactamente durante la ventana y la
    cuenta se activa en v.inicio (hoy mismo si ya empezó; si no, la tarea diaria).
    """
    ventana = bloquear(VentanaAuditoria, ventana)
    ganchos.autorizar(usuario, Accion.VENTANA_AUTORIZAR, ventana)
    if ventana.estado != EstadoVentana.PENDIENTE:
        raise TransicionInvalida(f"La ventana ya está {ventana.estado}.")
    if usuario.pk in (ventana.solicitada_por_id, ventana.auditor_id):
        raise SeparacionDeFunciones("Aprueba un GC distinto de quien la pidió y del auditor.")
    if (ventana.fin - ventana.inicio).days > DIAS_MAXIMOS_VENTANA:
        raise TransicionInvalida(f"La ventana dura más de {DIAS_MAXIMOS_VENTANA} días.")
    hoy = selectores.hoy()
    if ventana.fin < hoy:
        raise TransicionInvalida("La ventana ya terminó.")
    cuenta = bloquear(Usuario, ventana.auditor_id)
    if cuenta.estado != EstadoCuenta.PENDIENTE:
        raise TransicionInvalida(f"La cuenta del auditor está {cuenta.estado}.")
    ahora = timezone.now()
    ventana.estado = EstadoVentana.APROBADA
    ventana.aprobada_por = usuario
    ventana.aprobada_en = ahora
    ventana.save(update_fields=["estado", "aprobada_por", "aprobada_en"])
    agregar_rol(
        cuenta, roles.AE,
        desde=max(ahora, comienzo_del_dia(ventana.inicio)),
        hasta=comienzo_del_dia(ventana.fin + timedelta(days=1)),
    )
    if ventana.inicio <= hoy:
        cuenta, _ = cambiar_estado(cuenta, EstadoCuenta.ACTIVA)
    ganchos.registrar_evento(
        actor=usuario, accion=Accion.VENTANA_AUTORIZAR, recurso_tipo="ventana", recurso_id=str(ventana.pk),
        despues={"auditor": cuenta.pk, "inicio": str(ventana.inicio), "fin": str(ventana.fin),
                 "estado_cuenta": cuenta.estado},
    )
    return ventana


def procesar_ventanas(hoy: date | None = None) -> dict[str, int]:
    """
    Tarea diaria (proceso sin usuario; corre en fase 'sistema'). Solo toca
    cuentas con ventana de auditoría:
      - ventana APROBADA que empieza hoy o antes y sigue vigente: activa la cuenta;
      - ventana vencida (fin < hoy), aprobada o no: la cuenta pasa a INACTIVA
        (un AE nunca tiene registros de campo pendientes), se termina su rol AE
        si siguiera abierto y se cierran sus sesiones.
    Es idempotente: correrla dos veces el mismo día no cambia nada más.
    """
    hoy = hoy or selectores.hoy()
    resultado = {"activadas": 0, "dadas_de_baja": 0}
    por_activar = VentanaAuditoria.objects.filter(
        estado=EstadoVentana.APROBADA, inicio__lte=hoy, fin__gte=hoy, auditor__estado=EstadoCuenta.PENDIENTE
    ).values_list("pk", "auditor_id")
    for ventana_id, cuenta_id in list(por_activar):
        with transaction.atomic():
            cuenta, anterior = cambiar_estado(cuenta_id, EstadoCuenta.ACTIVA)
            _evento_tarea(Accion.CUENTA_ACTIVAR, cuenta, anterior, ventana_id)
        resultado["activadas"] += 1
    vencidas = VentanaAuditoria.objects.filter(
        fin__lt=hoy, auditor__estado__in=[EstadoCuenta.PENDIENTE, EstadoCuenta.ACTIVA]
    ).values_list("pk", "auditor_id")
    for ventana_id, cuenta_id in list(vencidas):
        with transaction.atomic():
            cuenta, anterior = cambiar_estado(cuenta_id, EstadoCuenta.INACTIVA)
            if roles.AE in selectores.roles_vigentes(cuenta):
                terminar_rol(cuenta, roles.AE)
            cerradas = sesion.cerrar_sesiones(cuenta)
            _evento_tarea(Accion.CUENTA_BAJA, cuenta, anterior, ventana_id, sesiones_cerradas=cerradas)
        resultado["dadas_de_baja"] += 1
    return resultado


def _evento_tarea(accion, cuenta, anterior, ventana_id, **extra) -> None:
    ganchos.registrar_evento(
        actor=None, accion=accion, recurso_tipo="cuenta", recurso_id=str(cuenta.pk),
        antes={"estado": anterior}, despues={"estado": cuenta.estado, "ventana": ventana_id, **extra},
        detalle="tarea diaria de ventanas de auditoría",
    )


# ===========================================================================
# C18 · AC·O7c CR, RV
# ===========================================================================
def _evento_dispositivo(usuario, accion, d, **extra):
    # Solo la huella: ni la llave completa ni datos personales.
    ganchos.registrar_evento(
        actor=usuario, accion=accion, recurso_tipo="dispositivo", recurso_id=str(d.pk),
        despues={"responsable": d.responsable_id, "huella": d.huella, "estado": d.estado, **extra},
    )


@transicion(Accion.DISPOSITIVO_REGISTRAR, "dispositivo")
def registrar_dispositivo(usuario, responsable, *, etiqueta, llave_publica) -> DispositivoCampo:
    """
    C18: TC ∈ R_vig(responsable(d)) ∧ cuenta(responsable(d)).estado = ACTIVA.
    Solo se guarda la llave pública; su huella no puede repetirse nunca.
    """
    algoritmo, pem, huella = llaves.normalizar_llave_publica(llave_publica)
    etiqueta = _texto(etiqueta, "etiqueta", 100)
    if not etiqueta:
        raise DatoInvalido("etiqueta: requerida.")
    responsable = bloquear(Usuario, responsable)
    ganchos.autorizar(usuario, Accion.DISPOSITIVO_REGISTRAR, None, {"responsable": responsable})
    if responsable.estado != EstadoCuenta.ACTIVA:
        raise TransicionInvalida(f"La cuenta del responsable está {responsable.estado}.")
    if roles.TC not in selectores.roles_vigentes(responsable):
        raise DatoInvalido("El responsable de un dispositivo debe tener el rol TC vigente.")
    try:
        with transaction.atomic():
            d = DispositivoCampo.objects.create(
                responsable=responsable, etiqueta=etiqueta, algoritmo=algoritmo, llave_publica=pem,
                huella=huella, registrado_por=usuario,
            )
    except IntegrityError:
        raise DatoInvalido("Esa llave ya fue registrada (las llaves no se reutilizan).")
    _evento_dispositivo(usuario, Accion.DISPOSITIVO_REGISTRAR, d, algoritmo=algoritmo)
    return d


def _revocar(dispositivo: DispositivoCampo, por, motivo: str) -> DispositivoCampo:
    dispositivo.estado = EstadoDispositivo.REVOCADO
    dispositivo.revocado_por = por
    dispositivo.revocado_en = timezone.now()
    dispositivo.motivo_revocacion = motivo
    dispositivo.save(update_fields=["estado", "revocado_por", "revocado_en", "motivo_revocacion"])
    return dispositivo


@transicion(Accion.DISPOSITIVO_REVOCAR, "dispositivo")
def revocar_dispositivo(usuario, dispositivo, *, motivo="") -> DispositivoCampo:
    """AC·O7c RV, sin condición. Definitivo: los registros firmados después ya no valen."""
    dispositivo = bloquear(DispositivoCampo, dispositivo)
    ganchos.autorizar(usuario, Accion.DISPOSITIVO_REVOCAR, dispositivo)
    if dispositivo.estado != EstadoDispositivo.ACTIVO:
        raise TransicionInvalida("El dispositivo ya está revocado.")
    _revocar(dispositivo, usuario, _texto(motivo, "motivo", 500))
    _evento_dispositivo(usuario, Accion.DISPOSITIVO_REVOCAR, dispositivo)
    return dispositivo


def revocar_dispositivos_de(cuenta, *, por=None, motivo: str) -> int:
    """
    Pieza de dominio para el cierre de la baja (C16): revoca todos los
    dispositivos activos de `cuenta`. No autoriza ni registra el evento: lo hace
    el servicio que la usa.
    """
    with transaction.atomic():
        activos = list(selectores.dispositivos_activos(cuenta).select_for_update())
        for d in activos:
            _revocar(d, por, motivo)
    return len(activos)


# ===========================================================================
# C17 · AC·O7d SO; GC·O7d AU, RE
# ===========================================================================
VIGENCIA_VERIFICACION = timedelta(days=7)


def _evento_restablecimiento(usuario, accion, r, **extra):
    ganchos.registrar_evento(
        actor=usuario, accion=accion, recurso_tipo="restablecimiento_factor", recurso_id=str(r.pk),
        despues={"usuario": r.usuario_id, "estado": r.estado, **extra},
    )


@transicion(Accion.FACTOR_SOLICITAR_RESTABLECIMIENTO, "restablecimiento_factor")
def solicitar_restablecimiento(usuario, cuenta, *, motivo) -> RestablecimientoFactor:
    """C17 SO: u ≠ s ∧ cuenta(u).estado = ACTIVA. Una solicitud en curso por cuenta."""
    motivo = _texto(motivo, "motivo", 500)
    if not motivo:
        raise DatoInvalido("motivo: requerido.")
    cuenta = bloquear(Usuario, cuenta)
    ganchos.autorizar(usuario, Accion.FACTOR_SOLICITAR_RESTABLECIMIENTO, cuenta)
    _exigir_otra_cuenta(usuario, cuenta)
    if cuenta.estado != EstadoCuenta.ACTIVA:
        raise TransicionInvalida(f"La cuenta está {cuenta.estado}.")
    try:
        with transaction.atomic():
            r = RestablecimientoFactor.objects.create(usuario=cuenta, motivo=motivo, solicitado_por=usuario)
    except IntegrityError:
        raise TransicionInvalida("Ya hay un restablecimiento en curso para esta cuenta.")
    _evento_restablecimiento(usuario, Accion.FACTOR_SOLICITAR_RESTABLECIMIENTO, r)
    return r


def _resolver_restablecimiento(usuario, restablecimiento, accion) -> RestablecimientoFactor:
    r = bloquear(RestablecimientoFactor, restablecimiento)
    ganchos.autorizar(usuario, accion, r)
    if r.estado != EstadoRestablecimiento.SOLICITADO:
        raise TransicionInvalida(f"El restablecimiento ya está {r.estado}.")
    if usuario.pk in (r.solicitado_por_id, r.usuario_id):
        raise SeparacionDeFunciones("Resuelve un GC distinto de quien lo pidió y de la persona.")
    return r


def _validar_verificacion(metodo, verificado_en) -> tuple[str, datetime]:
    if metodo not in MetodoVerificacion.values:
        raise DatoInvalido("metodo_verificacion: fuera del catálogo.")
    if not isinstance(verificado_en, datetime) or timezone.is_naive(verificado_en):
        raise DatoInvalido("verificado_en: fecha y hora con zona horaria.")
    ahora = timezone.now()
    if verificado_en > ahora + timedelta(minutes=5):
        raise DatoInvalido("verificado_en: no puede estar en el futuro.")
    if verificado_en < ahora - VIGENCIA_VERIFICACION:
        raise DatoInvalido("verificado_en: la verificación tiene más de 7 días; repítala.")
    return metodo, verificado_en


@transicion(Accion.FACTOR_AUTORIZAR_RESTABLECIMIENTO, "restablecimiento_factor")
def autorizar_restablecimiento(usuario, restablecimiento, *, metodo_verificacion, verificado_en):
    """
    C17 AU: g ≠ solicitante ∧ g ≠ u ∧ verificación de identidad fuera del sistema
    registrada (método y fecha). Efecto: invalida todos los factores de u y
    cierra sus sesiones. Si u tiene un rol que exige segundo factor, al volver a
    entrar se le pide registrar uno nuevo.
    """
    metodo, verificado_en = _validar_verificacion(metodo_verificacion, verificado_en)
    r = _resolver_restablecimiento(usuario, restablecimiento, Accion.FACTOR_AUTORIZAR_RESTABLECIMIENTO)
    # Lectura sin bloqueo: el GC no modifica la cuenta (RLS no le da UPDATE sobre ella).
    cuenta = Usuario.objects.get(pk=r.usuario_id)
    if cuenta.estado != EstadoCuenta.ACTIVA:
        raise TransicionInvalida(f"La cuenta está {cuenta.estado}.")
    from allauth.mfa.models import Authenticator

    r.estado = EstadoRestablecimiento.AUTORIZADO
    r.resuelto_por = usuario
    r.resuelto_en = timezone.now()
    r.metodo_verificacion = metodo
    r.verificado_en = verificado_en
    r.factores_invalidados = [
        {"tipo": m["tipo"], "creado_en": m["creado_en"].isoformat(),
         "usado_en": m["usado_en"].isoformat() if m["usado_en"] else None}
        for m in selectores.metadatos_factores(cuenta)
    ]
    r.save(update_fields=["estado", "resuelto_por", "resuelto_en", "metodo_verificacion", "verificado_en",
                          "factores_invalidados"])
    Authenticator.objects.filter(user_id=cuenta.pk).delete()
    cerradas = sesion.cerrar_sesiones(cuenta)
    _evento_restablecimiento(usuario, Accion.FACTOR_AUTORIZAR_RESTABLECIMIENTO, r, metodo=metodo,
                             factores=[f["tipo"] for f in r.factores_invalidados], sesiones_cerradas=cerradas)
    return r


@transicion(Accion.FACTOR_RECHAZAR_RESTABLECIMIENTO, "restablecimiento_factor")
def rechazar_restablecimiento(usuario, restablecimiento, *, motivo) -> RestablecimientoFactor:
    """C17 RE: queda RECHAZADO y los factores no cambian."""
    motivo = _texto(motivo, "motivo", 500)
    if not motivo:
        raise DatoInvalido("motivo: requerido.")
    r = _resolver_restablecimiento(usuario, restablecimiento, Accion.FACTOR_RECHAZAR_RESTABLECIMIENTO)
    r.estado = EstadoRestablecimiento.RECHAZADO
    r.resuelto_por = usuario
    r.resuelto_en = timezone.now()
    r.motivo_rechazo = motivo
    r.save(update_fields=["estado", "resuelto_por", "resuelto_en", "motivo_rechazo"])
    _evento_restablecimiento(usuario, Accion.FACTOR_RECHAZAR_RESTABLECIMIENTO, r)
    return r


# ===========================================================================
# O9 · Alertas: el motor HBAC crea (proceso); C22 · GC·O9 RS
# ===========================================================================
CLAVES_DETALLE_ALERTA = {"accion", "recurso_tipo", "n", "umbral", "media", "fuera_de_horario", "registros"}
VENTANA_AGRUPACION = timedelta(minutes=60)


def _detalle_alerta(detalle) -> dict:
    """Lista blanca: solo la acción y contadores, nunca datos personales ni del recurso."""
    detalle = detalle or {}
    if not isinstance(detalle, dict) or set(detalle) - CLAVES_DETALLE_ALERTA:
        raise DatoInvalido(f"detalle: solo {', '.join(sorted(CLAVES_DETALLE_ALERTA))}.")
    for clave, valor in detalle.items():
        if isinstance(valor, bool) or isinstance(valor, (int, float)):
            continue
        if isinstance(valor, str) and len(valor) <= 100:
            continue
        raise DatoInvalido(f"detalle.{clave}: número, booleano o texto corto.")
    return detalle


def crear_alerta(sujeto, control, *, detalle=None) -> tuple[Alerta, bool]:
    """
    Motor HBAC (proceso, sin autorización humana): crea una alerta en O9 sobre
    `sujeto` y devuelve (alerta, creada). No duplica:
      - H1: si ya hay una abierta, el sujeto ya está suspendido;
      - H2/H3: si hay una abierta del mismo control creada hace menos de 60 min.
    Cada ocurrencia igual queda en la bitácora con más detalle (H2).
    Quien la llama en una petición negada (H1) debe hacerlo fuera de la
    transacción que se revierte, como el registro de negaciones.
    """
    if control not in ControlHBAC.values:
        raise DatoInvalido(f"Control desconocido: {control!r}.")
    detalle = _detalle_alerta(detalle)
    sujeto_id = getattr(sujeto, "pk", sujeto)
    abiertas = Alerta.objects.filter(sujeto_id=sujeto_id, control=control, estado=EstadoAlerta.ABIERTA)
    if control != ControlHBAC.H1:
        abiertas = abiertas.filter(creada_en__gte=timezone.now() - VENTANA_AGRUPACION)
    existente = abiertas.order_by("-creada_en").first()
    if existente is None:
        prioridad = PrioridadAlerta.NORMAL if control == ControlHBAC.H3 else PrioridadAlerta.ALTA
        try:
            with transaction.atomic():
                alerta = Alerta.objects.create(sujeto_id=sujeto_id, control=control, prioridad=prioridad,
                                               detalle=detalle)
        except IntegrityError:
            # Otra petición simultánea abrió la H1 primero.
            existente = Alerta.objects.get(sujeto_id=sujeto_id, control=control, estado=EstadoAlerta.ABIERTA)
        else:
            ganchos.registrar_evento(
                actor=None, accion=Accion.ALERTA_CREAR, recurso_tipo="alerta", recurso_id=str(alerta.pk),
                despues={"sujeto": sujeto_id, "control": control, "prioridad": prioridad, **detalle},
                detalle="motor HBAC",
            )
            return alerta, True
    ganchos.registrar_evento(
        actor=None, accion=Accion.ALERTA_CREAR, recurso_tipo="alerta", recurso_id=str(existente.pk),
        despues={"sujeto": sujeto_id, "control": control, "repetida": True, **detalle},
        detalle="motor HBAC: ocurrencia agrupada en una alerta abierta",
    )
    return existente, False


def registrar_actividad(sujeto, tipo, objeto: str, *, registros: int = 1) -> ActividadHBAC:
    """
    Motor HBAC (proceso, sin autorización humana): el PEP anota cada lectura
    concedida de O1/O4 (H1) y cada exportación concedida con sus registros (H3).
    """
    if tipo not in TipoActividad.values:
        raise DatoInvalido(f"Tipo de actividad desconocido: {tipo!r}.")
    if not isinstance(objeto, str) or not 2 <= len(objeto) <= 3:
        raise DatoInvalido("objeto: columna de la matriz.")
    if isinstance(registros, bool) or not isinstance(registros, int) or registros < 0:
        raise DatoInvalido("registros: entero no negativo.")
    if tipo == TipoActividad.LECTURA:
        registros = 1
    return ActividadHBAC.objects.create(sujeto_id=getattr(sujeto, "pk", sujeto), tipo=tipo, objeto=objeto,
                                        registros=registros, en=timezone.now())


@transicion(Accion.ALERTA_RESOLVER, "alerta")
def resolver_alerta(usuario, alerta, *, motivo) -> Alerta:
    """
    C22: alerta.sujeto ≠ s ∧ motivo ≠ ''. Si es H1, levanta la suspensión; la
    primera lectura posterior del sujeto exige segundo factor (mfa.exige_mfa_tras_h1).
    """
    motivo = _texto(motivo, "motivo", 500)
    if not motivo:
        raise DatoInvalido("motivo: requerido.")
    alerta = bloquear(Alerta, alerta)
    ganchos.autorizar(usuario, Accion.ALERTA_RESOLVER, alerta)
    if alerta.estado != EstadoAlerta.ABIERTA:
        raise TransicionInvalida("La alerta ya está resuelta.")
    if alerta.sujeto_id == usuario.pk:
        raise SeparacionDeFunciones("Nadie resuelve una alerta sobre sí mismo (V14).")
    alerta.estado = EstadoAlerta.RESUELTA
    alerta.resuelta_por = usuario
    alerta.resuelta_en = timezone.now()
    alerta.motivo_resolucion = motivo
    alerta.save(update_fields=["estado", "resuelta_por", "resuelta_en", "motivo_resolucion"])
    ganchos.registrar_evento(
        actor=usuario, accion=Accion.ALERTA_RESOLVER, recurso_tipo="alerta", recurso_id=str(alerta.pk),
        despues={"sujeto": alerta.sujeto_id, "control": alerta.control, "levanta_suspension": alerta.bloqueante},
    )
    return alerta


# ===========================================================================
# C16 · GC·O4 SI: sincronización final vacía (cierre de la baja de un técnico)
# ===========================================================================
@transicion(Accion.MEDICION_SINCRONIZAR, "cuenta")
def cerrar_baja_tecnico(usuario, tecnico) -> Usuario:
    """
    Sincronización final sin registros pendientes: un GC cierra la baja de un
    técnico EN_BAJA. Se revocan sus dispositivos (ya no hay nada que recibir de
    ellos), la cuenta pasa a INACTIVA y se cierran sus sesiones. El GC queda como
    actor en la bitácora. La sincronización de registros firmados necesita la
    PWA sin conexión, que no forma parte de esta entrega; el motor ya evalúa su
    condición (C16 con registro) para cuando exista.
    """
    tecnico = bloquear(Usuario, tecnico)
    ganchos.autorizar(usuario, Accion.MEDICION_SINCRONIZAR, None, {"tecnico": tecnico})
    _exigir_otra_cuenta(usuario, tecnico)
    if tecnico.estado != EstadoCuenta.EN_BAJA:
        raise TransicionInvalida(f"Solo se cierra la baja de una cuenta EN_BAJA (esta está {tecnico.estado}).")
    revocados = revocar_dispositivos_de(tecnico, por=usuario, motivo="sincronización final")
    tecnico, anterior = cambiar_estado(tecnico, EstadoCuenta.INACTIVA)
    cerradas = sesion.cerrar_sesiones(tecnico)
    ganchos.registrar_evento(
        actor=usuario, accion=Accion.MEDICION_SINCRONIZAR, recurso_tipo="cuenta", recurso_id=str(tecnico.pk),
        antes={"estado": anterior},
        despues={"estado": tecnico.estado, "registros": 0, "dispositivos_revocados": revocados,
                 "sesiones_cerradas": cerradas},
        detalle="sincronización final sin registros pendientes",
    )
    return tecnico
