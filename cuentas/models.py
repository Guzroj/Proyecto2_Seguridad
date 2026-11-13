"""
Cuentas de usuario (O7a).

Modelo MÍNIMO creado por Persona 2 para fijar AUTH_USER_MODEL desde la primera
migración. Persona 3 lo extiende con: roles y su vigencia (O7b), estado de la
cuenta (PENDIENTE/ACTIVA/EN_BAJA/INACTIVA), ventanas de auditoría, MFA, etc.

Toda cuenta es nominal: una persona, nunca compartida (Políticas §5).
"""

from django.contrib.auth.models import AbstractUser


class Usuario(AbstractUser):
    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"

    def __str__(self) -> str:
        return self.get_full_name() or self.username
