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
