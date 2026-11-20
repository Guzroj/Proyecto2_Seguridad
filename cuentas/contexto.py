"""Procesador de contexto: los roles vigentes para armar el menú (leídos de la base en cada petición)."""

from .selectores import roles_vigentes


def menu(request):
    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return {"menu_roles": frozenset()}
    return {"menu_roles": frozenset(roles_vigentes(usuario))}
