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
