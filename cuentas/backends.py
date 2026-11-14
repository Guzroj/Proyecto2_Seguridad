"""
Backend de autenticación compatible con RLS.

Con RLS el rol de la aplicación no puede leer cuentas sin un sujeto. Para el
login: 1) se obtiene solo el id de la cuenta ACTIVA con cuentas_id_para_login,
2) se fija ese id como sujeto, 3) se lee la fila y se verifica la contraseña
(Argon2id). Si falla, el contexto vuelve a anónimo.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from . import rls


class BackendRLS(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        Usuario = get_user_model()
        if username is None:
            username = kwargs.get(Usuario.USERNAME_FIELD)
        if username is None or password is None:
            return None

        usuario_id = rls.id_para_login(username)
        if usuario_id is None:
            # Mismo costo que una contraseña real: no revela si la cuenta existe.
            Usuario().set_password(password)
            return None

        rls.fijar_contexto(usuario_id=usuario_id)
        usuario = Usuario._default_manager.filter(pk=usuario_id).first()
        if (
            usuario is not None
            and usuario.check_password(password)
            and self.user_can_authenticate(usuario)
        ):
            return usuario
        rls.fijar_contexto()
        return None
