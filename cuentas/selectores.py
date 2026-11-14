"""
Funciones de atributos de cuentas (solo lectura). Siempre leen de la base de
datos, nunca de la sesión ni de la petición (los roles no se copian en la sesión).

  roles_vigentes(s)   R_vig(s): roles con vigencia en el instante dado
"""

from django.db.models import Q
from django.utils import timezone

from .models import RolUsuario


def vigentes_en(instante=None) -> Q:
    """Filtro de RolUsuario vigente en `instante` (por defecto, ahora)."""
    instante = instante or timezone.now()
    return Q(vigente_desde__lte=instante) & (
        Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gt=instante)
    )


def roles_vigentes(usuario, en=None) -> set[str]:
    """R_vig(s). Acepta usuario o pk. No mira el estado de la cuenta."""
    usuario_id = getattr(usuario, "pk", usuario)
    return set(
        RolUsuario.objects.filter(vigentes_en(en), usuario_id=usuario_id).values_list("rol", flat=True)
    )


def tiene_rol(usuario, rol: str, en=None) -> bool:
    return rol in roles_vigentes(usuario, en)
