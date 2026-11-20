"""
Roles y pares de roles excluyentes (E). Python puro, sin Django: lo usan tanto
Django (cuentas) como el servicio autorizador, que no tiene Django ni base.

Pares excluyentes (no se pueden tener a la vez, con vigencias que se traslapan):
    TC⊕VF · AC⊕GC · AC⊕TC · AC⊕VF · AE⊕{TC, VF, GC, AC}
PF es compatible con cualquier rol; las restricciones de propietario se aplican
por objeto (C2, C3, C19), no por rol.
"""

from collections.abc import Iterable

TC, VF, GC, AC, AE, PF = "TC", "VF", "GC", "AC", "AE", "PF"

ROLES: dict[str, str] = {
    TC: "Técnico de campo",
    VF: "Verificador",
    GC: "Gerente de certificación",
    AC: "Administrador de catálogo y cuentas",
    AE: "Auditor externo",
    PF: "Propietario de finca",
}

# Roles cuya asignación no tiene efecto hasta que otro GC la autoriza (C7).
ROLES_CON_DOBLE_APROBACION = frozenset({GC, AE})

# Roles que exigen segundo factor al ingresar.
ROLES_CON_MFA = frozenset({TC, VF, GC, AC})

EXCLUYENTES: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({TC, VF}),
        frozenset({AC, GC}),
        frozenset({AC, TC}),
        frozenset({AC, VF}),
        frozenset({AE, TC}),
        frozenset({AE, VF}),
        frozenset({AE, GC}),
        frozenset({AE, AC}),
    }
)


def es_rol(valor) -> bool:
    return isinstance(valor, str) and valor in ROLES


def son_excluyentes(a: str, b: str) -> bool:
    return frozenset({a, b}) in EXCLUYENTES


def conflictos(rol: str, roles_actuales: Iterable[str]) -> set[str]:
    """Roles de `roles_actuales` que no pueden convivir con `rol`."""
    return {r for r in roles_actuales if son_excluyentes(rol, r)}
