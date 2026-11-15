"""
Contexto de Row Level Security (RLS) en PostgreSQL.

Las políticas de las tablas leen dos variables de la conexión:
  app.usuario_id  sujeto autenticado de la petición (vacío = anónimo)
  app.fase        'sistema' para procesos sin usuario (tareas, carga inicial);
                  'preingreso' cuando la contraseña ya se verificó pero falta el
                  segundo factor: el sujeto solo ve su propia cuenta y sus factores

Django reutiliza conexiones (conn_max_age), así que el contexto se fija al
empezar CADA petición y se limpia al terminar. Si no se puede limpiar, se cierra
la conexión para que el siguiente uso no herede un sujeto (fallo cerrado).

Limitación conocida: el rol de la aplicación puede fijar estas variables; RLS
protege contra errores de la aplicación (consultas sin filtro, objetos fuera de
alcance), no contra un atacante que ya ejecuta SQL arbitrario con ese rol.
"""

import logging

from allauth.account.internal.stagekit import LOGIN_SESSION_KEY
from django.contrib.auth import SESSION_KEY
from django.db import DatabaseError, connection

log = logging.getLogger("verdeazul.cuentas")


def _postgres() -> bool:
    return connection.vendor == "postgresql"


def fijar_contexto(usuario_id=None, fase: str | None = None) -> None:
    if not _postgres():
        return
    valor_id = str(usuario_id) if usuario_id is not None and str(usuario_id).isdigit() else ""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.usuario_id', %s, false), set_config('app.fase', %s, false)",
            [valor_id, fase or ""],
        )


def limpiar_contexto() -> None:
    try:
        fijar_contexto()
    except DatabaseError:
        log.warning("No se pudo limpiar el contexto RLS: se cierra la conexión.")
        connection.close()


def id_para_login(username: str) -> int | None:
    """
    Id de la cuenta ACTIVA con ese username, o None. Usa la función de la base
    cuentas_id_para_login (SECURITY DEFINER), la única vía para buscar una cuenta
    antes de conocer al sujeto. Devuelve solo el id, nunca el hash.
    """
    if not isinstance(username, str) or not username or len(username) > 150:
        return None
    if not _postgres():
        from .models import EstadoCuenta, Usuario

        return (
            Usuario.objects.filter(username=username, estado=EstadoCuenta.ACTIVA)
            .values_list("pk", flat=True)
            .first()
        )
    with connection.cursor() as cursor:
        cursor.execute("SELECT cuentas_id_para_login(%s)", [username])
        fila = cursor.fetchone()
    return fila[0] if fila else None


FASE_PREINGRESO = "preingreso"


class ContextoRLSMiddleware:
    """
    Va después de AuthenticationMiddleware. El id sale de la sesión del servidor
    (nunca de la petición). Si la sesión ya no corresponde a un usuario válido
    (cuenta desactivada, contraseña cambiada), el contexto vuelve a anónimo.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario_id = request.session.get(SESSION_KEY)
        if usuario_id:
            fijar_contexto(usuario_id=usuario_id)
            if not request.user.is_authenticated:
                fijar_contexto()
        else:
            fijar_contexto(usuario_id=self._pendiente(request), fase=FASE_PREINGRESO)
        try:
            return self.get_response(request)
        finally:
            limpiar_contexto()

    @staticmethod
    def _pendiente(request):
        """Usuario con login a medias (contraseña verificada, falta el segundo factor)."""
        pendiente = request.session.get(LOGIN_SESSION_KEY)
        if not isinstance(pendiente, dict) or not pendiente.get("user_pk"):
            return None
        from allauth.account.utils import url_str_to_user_pk

        try:
            # allauth guarda la pk codificada (base36 para enteros).
            return url_str_to_user_pk(pendiente["user_pk"])
        except (ValueError, TypeError):
            return None
