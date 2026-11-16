"""
Respuestas de error de las pantallas (las vistas no deciden permisos: lo hace el
motor; aquí solo se traducen sus excepciones a páginas):

  ReautenticacionRequerida  -> la maneja ExigirMFAMiddleware (reto, RG6)
  AccesoDenegado            -> 403
  FueraDeAlcance            -> 404 (RG3: igual que si no existiera)
  DatoInvalido              -> 400
  ReglaNegocio              -> 409 (conflicto con el estado)

Va ANTES de ExigirMFAMiddleware en MIDDLEWARE: process_exception corre de abajo
hacia arriba, así que el reto se resuelve primero.
"""

from django.shortcuts import render

from nucleo.errores import (
    AccesoDenegado,
    DatoInvalido,
    FueraDeAlcance,
    ReautenticacionRequerida,
    ReglaNegocio,
)


def pagina_error(request, estado: int, titulo: str, mensaje: str = ""):
    return render(request, "cuentas/error.html", {"titulo": titulo, "mensaje": mensaje}, status=estado)


class ErroresMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, ReautenticacionRequerida):
            return None
        if isinstance(exception, FueraDeAlcance):
            return pagina_error(request, 404, "No encontrado")
        if isinstance(exception, AccesoDenegado):
            return pagina_error(request, 403, "Acceso denegado", str(exception))
        if isinstance(exception, DatoInvalido):
            return pagina_error(request, 400, "Dato inválido", str(exception))
        if isinstance(exception, ReglaNegocio):
            return pagina_error(request, 409, "No se puede realizar", str(exception))
        return None
