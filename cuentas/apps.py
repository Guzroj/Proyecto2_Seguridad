from django.apps import AppConfig


class CuentasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cuentas'

    def ready(self):
        from . import mfa, signals  # noqa: F401  (conecta los receptores de login/logout/MFA)
