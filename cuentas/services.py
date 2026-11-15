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
dar_de_baja y enlace_activacion: estos sí autorizan con el motor y registran el
evento en la bitácora.
"""

from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
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
from .models import EstadoCuenta, MotivoCierre, RolUsuario, Usuario

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
def crear_cuenta(usuario, *, username, rol, first_name="", last_name="", email="") -> Usuario:
    """
    C15 CR. La cuenta nace sin contraseña utilizable: la persona la fija con el
    enlace de activación (enlace_activacion), que AC le entrega por otro canal.
    Con rol GC o AE queda PENDIENTE (sin rol) hasta la doble aprobación (C7) o la
    ventana de auditoría (C14). Con otro rol queda ACTIVA con ese rol.
    """
    if not roles.es_rol(rol):
        raise DatoInvalido(f"Rol desconocido: {rol!r}.")
    ganchos.autorizar(usuario, Accion.CUENTA_CREAR, None, {"rol": rol})
    datos = {
        "username": _username(username),
        "first_name": _texto(first_name, "first_name", 150),
        "last_name": _texto(last_name, "last_name", 150),
        "email": _correo(email),
    }
    cuenta = Usuario.objects.create_user(password=None, **datos)
    if rol not in roles.ROLES_CON_DOBLE_APROBACION:
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
