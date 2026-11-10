"""
Decorador común para todas las operaciones que modifican datos.

  - Ejecuta la operación en una transacción: o se hace todo o nada.
  - Si la operación se niega (regla de negocio o autorización), registra el evento
    DENEGADO en la bitácora FUERA de la transacción revertida (RG4: toda negación se
    registra) y vuelve a lanzar la excepción para que la vista responda.
"""

from functools import wraps

from django.core.exceptions import ValidationError
from django.db import transaction

from . import ganchos
from .errores import AccesoDenegado, FueraDeAlcance, ReglaNegocio


def transicion(accion: str, recurso_tipo: str):
    def decorador(funcion):
        @wraps(funcion)
        def envoltura(usuario, *args, **kwargs):
            try:
                with transaction.atomic():
                    return funcion(usuario, *args, **kwargs)
            except (ReglaNegocio, AccesoDenegado, FueraDeAlcance) as exc:
                objetivo = args[0] if args else None
                ganchos.registrar_evento(
                    actor=usuario,
                    accion=accion,
                    recurso_tipo=recurso_tipo,
                    recurso_id=str(getattr(objetivo, "pk", "") or ""),
                    resultado="DENEGADO",
                    detalle=f"{type(exc).__name__}: {exc}",
                )
                raise

        return envoltura

    return decorador


def bloquear(modelo, objeto_o_pk):
    """Relee el objeto de la BD con SELECT ... FOR UPDATE (nunca confiar en el que llega)."""
    pk = getattr(objeto_o_pk, "pk", objeto_o_pk)
    try:
        return modelo.objects.select_for_update().get(pk=pk)
    except (modelo.DoesNotExist, ValueError, TypeError, ValidationError):
        raise FueraDeAlcance("No encontrado.")
