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
(GC·O7a AU) en aprobar_ventana. Estos sí autorizan con el motor y registran el
evento en la bitácora. procesar_ventanas es la tarea diaria (proceso sin
usuario): activa las cuentas AE el día de inicio y da de baja las vencidas.
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

from . import selectores, sesion
from .models import (
    DIAS_MAXIMOS_VENTANA,
    EstadoCuenta,
    EstadoSolicitudRol,
    EstadoVentana,
    MotivoCierre,
    RolUsuario,
    SolicitudRol,
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
