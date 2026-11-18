"""
Pantallas de exportación (X). La lista de conjuntos es solo informativa: cada exportación
la decide el motor (AE·X [C5, H3]); cualquier otro rol recibe 403 aunque llame la URL.

La exportación se pide con POST (con CSRF): un GET de terceros no puede gastar la cuota de
H3 del auditor ni generar alertas a su nombre. Si H3 exige segundo factor reciente, el reto
(RG6) vuelve a la página de confirmación con GET.
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from nucleo.errores import FueraDeAlcance

from . import servicios


def _sin_cache(respuesta):
    respuesta["Cache-Control"] = "no-store"
    return respuesta


@login_required
def exportaciones(request):
    return _sin_cache(render(request, "exportaciones/exportaciones.html", {
        "conjuntos": sorted(servicios.CONJUNTOS.items(), key=lambda par: par[1].titulo),
        "limite_h3": 500,
    }))


@login_required
@require_http_methods(["GET", "POST"])
def exportar(request, nombre):
    conjunto = servicios.CONJUNTOS.get(nombre)
    if conjunto is None:
        raise FueraDeAlcance("No encontrado.")
    if request.method == "GET":
        return _sin_cache(render(request, "cuentas/gestion/confirmar.html", {
            "titulo": f"Exportar: {conjunto.titulo}",
            "boton": "Exportar CSV",
            "aviso": "El archivo lleva una marca de agua con su cuenta y la fecha, y la exportación queda en "
                     "la bitácora. Más de 500 registros en 24 horas exigen segundo factor reciente (H3).",
        }))
    exportacion = servicios.exportar(request.user, nombre)
    respuesta = HttpResponse(exportacion.contenido, content_type="text/csv; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="{exportacion.nombre_archivo}"'
    respuesta["X-Exportacion-Registros"] = str(exportacion.registros)
    respuesta["X-Exportacion-SHA256"] = exportacion.sha256
    respuesta["X-Content-Type-Options"] = "nosniff"
    return _sin_cache(respuesta)
