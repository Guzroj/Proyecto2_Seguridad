"""
Cuentas de usuario (O7a).

Toda cuenta es nominal: una persona, nunca compartida (Políticas §5).

Estado de la cuenta:
    PENDIENTE -> ACTIVA -> EN_BAJA -> INACTIVA
    ACTIVA    -> INACTIVA            (baja sin registros pendientes de sincronizar)
    PENDIENTE -> INACTIVA            (alta rechazada antes de activarse)

  - PENDIENTE: creada, aún sin efecto (p. ej. GC o AE esperando aprobación, C15).
  - ACTIVA:    la única que puede autenticarse.
  - EN_BAJA:   dada de baja con registros sin sincronizar (C15/C16); no ingresa.
  - INACTIVA:  final; nunca se reactiva (una persona que vuelve recibe cuenta nueva).

`is_active` de Django se deriva SIEMPRE del estado (ACTIVA <=> is_active), así
el backend de autenticación y cualquier consulta de Django respetan el estado.
La base de datos lo hace cumplir con una restricción. Los cambios de estado se
hacen solo con cuentas.services.
"""

from datetime import timedelta

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models import Q

from autorizacion import roles


class EstadoCuenta(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente"
    ACTIVA = "ACTIVA", "Activa"
    EN_BAJA = "EN_BAJA", "En baja (sincronización pendiente)"
    INACTIVA = "INACTIVA", "Inactiva"


class UsuarioManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        # El superusuario de mantenimiento nace utilizable; el resto nace PENDIENTE.
        extra_fields.setdefault("estado", EstadoCuenta.ACTIVA)
        return super().create_superuser(username, email, password, **extra_fields)


class Usuario(AbstractUser):
    estado = models.CharField(
        max_length=10, choices=EstadoCuenta.choices, default=EstadoCuenta.PENDIENTE
    )
    estado_desde = models.DateTimeField(
        null=True, blank=True, help_text="Momento del último cambio de estado (hora del servidor)."
    )

    objects = UsuarioManager()

    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"
        constraints = [
            # is_active refleja exactamente el estado ACTIVA.
            models.CheckConstraint(
                condition=(Q(estado=EstadoCuenta.ACTIVA) & Q(is_active=True))
                | (~Q(estado=EstadoCuenta.ACTIVA) & Q(is_active=False)),
                name="usuario_activo_segun_estado",
            ),
        ]

    def __str__(self) -> str:
        return self.get_full_name() or self.username

    def save(self, *args, **kwargs):
        self.is_active = self.estado == EstadoCuenta.ACTIVA
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "estado" in update_fields:
            kwargs["update_fields"] = {*update_fields, "is_active"}
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# O7b · Pertenencia usuario–rol con vigencia
# ---------------------------------------------------------------------------
class Rol(models.TextChoices):
    TC = roles.TC, roles.ROLES[roles.TC]
    VF = roles.VF, roles.ROLES[roles.VF]
    GC = roles.GC, roles.ROLES[roles.GC]
    AC = roles.AC, roles.ROLES[roles.AC]
    AE = roles.AE, roles.ROLES[roles.AE]
    PF = roles.PF, roles.ROLES[roles.PF]


class RolUsuario(models.Model):
    """
    Un usuario tiene el rol `rol` durante [vigente_desde, vigente_hasta).
    vigente_hasta NULL = sin fin. Nunca se borra: retirar un rol cierra su
    vigencia, y el historial queda (p. ej. quien fue TC y hoy es VF, V5).
    Solo representa roles con efecto; las solicitudes de GC/AE pendientes de
    aprobación (C7) se guardan aparte.
    """

    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="roles")
    rol = models.CharField(max_length=2, choices=Rol.choices)
    vigente_desde = models.DateTimeField()
    vigente_hasta = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "rol de usuario"
        verbose_name_plural = "roles de usuario"
        ordering = ["usuario", "vigente_desde"]
        indexes = [models.Index(fields=["rol", "vigente_hasta"], name="rolusuario_rol_hasta")]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "rol"],
                condition=Q(vigente_hasta__isnull=True),
                name="rolusuario_uno_abierto",
            ),
            models.CheckConstraint(
                condition=Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gt=models.F("vigente_desde")),
                name="rolusuario_vigencia_coherente",
            ),
            models.CheckConstraint(
                condition=Q(rol__in=list(roles.ROLES)), name="rolusuario_rol_conocido"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.usuario} · {self.rol}"


class TipoSolicitudRol(models.TextChoices):
    ASIGNAR = "ASIGNAR", "Asignar rol"
    RETIRAR = "RETIRAR", "Retirar rol"


class EstadoSolicitudRol(models.TextChoices):
    SOLICITADA = "SOLICITADA", "Solicitada"
    AUTORIZADA = "AUTORIZADA", "Autorizada"
    RECHAZADA = "RECHAZADA", "Rechazada"


class SolicitudRol(models.Model):
    """
    C7: asignar o retirar GC o AE no tiene efecto hasta que un GC distinto del
    solicitante y de la persona destino lo autorice. Nunca se borra: queda
    como historial de quién pidió y quién resolvió.
    """

    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="solicitudes_rol")
    rol = models.CharField(max_length=2, choices=Rol.choices)
    tipo = models.CharField(max_length=7, choices=TipoSolicitudRol.choices)
    estado = models.CharField(
        max_length=10, choices=EstadoSolicitudRol.choices, default=EstadoSolicitudRol.SOLICITADA
    )
    solicitada_por = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="+")
    solicitada_en = models.DateTimeField(auto_now_add=True)
    resuelta_por = models.ForeignKey(
        Usuario, on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    resuelta_en = models.DateTimeField(null=True, blank=True)
    motivo = models.CharField(max_length=500, blank=True)

    class Meta:
        verbose_name = "solicitud de rol"
        verbose_name_plural = "solicitudes de rol"
        ordering = ["-solicitada_en"]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "rol"],
                condition=Q(estado="SOLICITADA"),
                name="solicitudrol_una_en_curso",
            ),
            models.CheckConstraint(
                condition=Q(rol__in=sorted(roles.ROLES_CON_DOBLE_APROBACION)),
                name="solicitudrol_solo_roles_con_doble_aprobacion",
            ),
            models.CheckConstraint(
                condition=(
                    Q(estado="SOLICITADA", resuelta_por__isnull=True, resuelta_en__isnull=True)
                    | (~Q(estado="SOLICITADA") & Q(resuelta_por__isnull=False, resuelta_en__isnull=False))
                ),
                name="solicitudrol_resolucion_coherente",
            ),
            models.CheckConstraint(
                condition=~Q(resuelta_por=models.F("solicitada_por")) & ~Q(resuelta_por=models.F("usuario")),
                name="solicitudrol_resuelve_otra_persona",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tipo} {self.rol} · {self.usuario} ({self.estado})"


# ---------------------------------------------------------------------------
# O7a · Ventana de auditoría (C5, C14)
# ---------------------------------------------------------------------------
DIAS_MAXIMOS_VENTANA = 30


class EstadoVentana(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente de aprobación"
    APROBADA = "APROBADA", "Aprobada"


class VentanaAuditoria(models.Model):
    """
    Período [inicio, fin] (fechas de Costa Rica, ambos días incluidos) en que un
    auditor externo puede leer y exportar (C5). La pide AC al crear la cuenta AE
    y la aprueba un GC distinto del solicitante y del auditor (C14). Una por
    cuenta: al vencer, la tarea diaria deja la cuenta INACTIVA (estado final) y
    otra auditoría usa otra cuenta.
    """

    auditor = models.OneToOneField(Usuario, on_delete=models.PROTECT, related_name="ventana_auditoria")
    inicio = models.DateField()
    fin = models.DateField()
    estado = models.CharField(max_length=10, choices=EstadoVentana.choices, default=EstadoVentana.PENDIENTE)
    solicitada_por = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="+")
    solicitada_en = models.DateTimeField(auto_now_add=True)
    aprobada_por = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="+", null=True, blank=True)
    aprobada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "ventana de auditoría"
        verbose_name_plural = "ventanas de auditoría"
        ordering = ["-inicio"]
        constraints = [
            models.CheckConstraint(
                condition=Q(fin__gte=models.F("inicio"))
                & Q(fin__lte=models.F("inicio") + timedelta(days=DIAS_MAXIMOS_VENTANA)),
                name="ventana_maximo_30_dias",
            ),
            models.CheckConstraint(
                condition=Q(estado="PENDIENTE", aprobada_por__isnull=True, aprobada_en__isnull=True)
                | Q(estado="APROBADA", aprobada_por__isnull=False, aprobada_en__isnull=False),
                name="ventana_aprobacion_coherente",
            ),
            models.CheckConstraint(
                condition=~Q(aprobada_por=models.F("solicitada_por")) & ~Q(aprobada_por=models.F("auditor"))
                & ~Q(solicitada_por=models.F("auditor")),
                name="ventana_tres_personas",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.auditor} · {self.inicio}–{self.fin} ({self.estado})"


# ---------------------------------------------------------------------------
# O7c · Dispositivos de campo (C18)
# ---------------------------------------------------------------------------
class EstadoDispositivo(models.TextChoices):
    ACTIVO = "ACTIVO", "Activo"
    REVOCADO = "REVOCADO", "Revocado"


class AlgoritmoLlave(models.TextChoices):
    ED25519 = "ED25519", "Ed25519"
    P256 = "P256", "ECDSA P-256"


class DispositivoCampo(models.Model):
    """
    Dispositivo con el que un técnico firma sus registros de campo sin conexión.
    Solo se guarda la llave PÚBLICA (la privada nunca sale del dispositivo, RG2).
    La huella (SHA-256 de la llave) es única para siempre: una llave revocada
    no se vuelve a registrar. Nunca se borra; revocar es definitivo.
    """

    responsable = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="dispositivos")
    etiqueta = models.CharField(max_length=100)
    algoritmo = models.CharField(max_length=7, choices=AlgoritmoLlave.choices)
    llave_publica = models.TextField(help_text="PEM SubjectPublicKeyInfo.")
    huella = models.CharField(max_length=64, unique=True, help_text="SHA-256 (hex) de la llave en DER.")
    estado = models.CharField(max_length=8, choices=EstadoDispositivo.choices, default=EstadoDispositivo.ACTIVO)
    registrado_por = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="+")
    registrado_en = models.DateTimeField(auto_now_add=True)
    revocado_por = models.ForeignKey(
        Usuario, on_delete=models.PROTECT, related_name="+", null=True, blank=True,
        help_text="Vacío si lo revocó un proceso (cierre de la baja, C16).",
    )
    revocado_en = models.DateTimeField(null=True, blank=True)
    motivo_revocacion = models.CharField(max_length=500, blank=True)

    class Meta:
        verbose_name = "dispositivo de campo"
        verbose_name_plural = "dispositivos de campo"
        ordering = ["responsable", "-registrado_en"]
        constraints = [
            models.CheckConstraint(
                condition=Q(estado="ACTIVO", revocado_por__isnull=True, revocado_en__isnull=True)
                | Q(estado="REVOCADO", revocado_en__isnull=False),
                name="dispositivo_revocacion_coherente",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.etiqueta} · {self.responsable} ({self.estado})"


# ---------------------------------------------------------------------------
# O7a · Sesiones propias
# ---------------------------------------------------------------------------
class MotivoCierre(models.TextChoices):
    SALIDA = "SALIDA", "Cerró sesión"
    INACTIVIDAD = "INACTIVIDAD", "Inactividad"
    MAXIMO = "MAXIMO", "Máximo absoluto de 10 h"
    FORZADO = "FORZADO", "Cierre por cambio de privilegios o baja"
    INVALIDA = "INVALIDA", "Sesión sin registro válido"


class Sesion(models.Model):
    """
    Registro del lado del servidor de cada sesión iniciada. Una petición
    autenticada solo se acepta si su sesión tiene aquí una fila abierta; así,
    cerrar las sesiones de un usuario (C7, C15) es marcar sus filas como cerradas.
    Se guarda el hash SHA-256 de la clave de sesión, nunca la clave.
    """

    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="sesiones")
    clave_hash = models.CharField(max_length=64)
    iniciada_en = models.DateTimeField()
    ultima_actividad = models.DateTimeField()
    cerrada_en = models.DateTimeField(null=True, blank=True)
    motivo_cierre = models.CharField(max_length=12, choices=MotivoCierre.choices, blank=True)

    class Meta:
        verbose_name = "sesión"
        verbose_name_plural = "sesiones"
        ordering = ["-iniciada_en"]
        indexes = [models.Index(fields=["usuario", "cerrada_en"], name="sesion_usuario_abierta")]
        constraints = [
            models.UniqueConstraint(
                fields=["clave_hash"], condition=Q(cerrada_en__isnull=True), name="sesion_clave_abierta_unica"
            ),
            models.CheckConstraint(
                condition=Q(cerrada_en__isnull=True, motivo_cierre="")
                | (Q(cerrada_en__isnull=False) & ~Q(motivo_cierre="")),
                name="sesion_cierre_con_motivo",
            ),
        ]

    def __str__(self) -> str:
        return f"Sesión de {self.usuario} desde {self.iniciada_en:%Y-%m-%d %H:%M}"
