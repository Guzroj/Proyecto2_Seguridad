"""
Pantallas de la bitácora (O8). Solo lectura: no existe ninguna vista que cree, modifique o
borre eventos (la tabla además lo impide por disparador y RLS).

  eventos    GC·O8 L, AE·O8 L [C5]: lista filtrada por el motor, con filtros de lista blanca
  evento     detalle: antes/después (ya sin secretos), encadenamiento (hash anterior y propio)
  verificar  recalcula la cadena completa (bitacora_verificar), la compara con los sellos
             firmados (bitacora/sellos.py) y muestra si está íntegra

Cada consulta concedida queda a su vez en la bitácora (RG4) y cada negación también.
"""

import json
import re

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET

from autorizacion.listas import listar
from cuentas.pantallas import autorizar_lectura
from nucleo.acciones import Accion
from nucleo.errores import FueraDeAlcance

from . import sellos
from .models import Evento, Resultado, SelloBitacora
from .servicios import verificar_cadena

ACCION_VALIDA = re.compile(r"^[a-z_]{1,30}(:[a-z_]{0,33})?$")
POR_PAGINA = 100


def _sin_cache(respuesta):
    respuesta["Cache-Control"] = "no-store"
    return respuesta


@login_required
@require_GET
def eventos(request):
    lista = listar(request.user, "bitacora")
    filtros = {}
    accion = (request.GET.get("accion") or "").strip()
    if accion and ACCION_VALIDA.match(accion):
        lista = lista.filter(accion__startswith=accion)
        filtros["accion"] = accion
    resultado = request.GET.get("resultado")
    if resultado in Resultado.values:
        lista = lista.filter(resultado=resultado)
        filtros["resultado"] = resultado
    actor = request.GET.get("actor") or ""
    if actor.isdigit():
        lista = lista.filter(actor_id=int(actor))
        filtros["actor"] = actor
    pagina = Paginator(lista.order_by("-secuencia"), POR_PAGINA).get_page(request.GET.get("pagina"))
    return _sin_cache(render(request, "bitacora/eventos.html", {
        "pagina": pagina, "filtros": filtros, "resultados": Resultado.choices,
    }))


@login_required
@require_GET
def evento(request, pk):
    try:
        e = Evento.objects.filter(pk=pk).first()
    except (ValueError, TypeError):
        e = None
    if e is None:
        raise FueraDeAlcance("No encontrado.")
    autorizar_lectura(request.user, Accion.BITACORA_LEER, e, "evento")
    return _sin_cache(render(request, "bitacora/evento.html", {
        "e": e,
        "antes": json.dumps(e.antes, ensure_ascii=False, indent=2, sort_keys=True) if e.antes is not None else "",
        "despues": json.dumps(e.despues, ensure_ascii=False, indent=2, sort_keys=True) if e.despues is not None else "",
    }))


@login_required
@require_GET
def verificar(request):
    lista = listar(request.user, "bitacora")  # misma autorización que leer la bitácora
    problemas = sorted(verificar_cadena() + sellos.verificar())
    ultimo = lista.order_by("-secuencia").values("secuencia", "hash").first()
    return _sin_cache(render(request, "bitacora/verificar.html", {
        "problemas": problemas, "total": lista.count(), "ultimo": ultimo,
        "sellos": SelloBitacora.objects.count(),
        "ultimo_sello": SelloBitacora.objects.order_by("-secuencia", "-momento").first(),
    }))
