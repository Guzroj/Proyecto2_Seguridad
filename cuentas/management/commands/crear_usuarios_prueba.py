"""
Crea las cuentas de prueba de cada rol, ACTIVAS, con contraseña y secreto TOTP
conocidos, para entregar las credenciales a quien evalúa.

Los secretos NO van en el código ni en la línea de comandos: se leen de las
variables de entorno VZ_PRUEBA_CLAVE y VZ_PRUEBA_TOTP (secreto base32 de al
menos 32 caracteres = 160 bits). Ejemplo:

    read -rs VZ_PRUEBA_CLAVE && export VZ_PRUEBA_CLAVE
    export VZ_PRUEBA_TOTP=$(python3 -c "import base64,secrets;print(base64.b32encode(secrets.token_bytes(20)).decode())")
    docker compose exec -e VZ_PRUEBA_CLAVE -e VZ_PRUEBA_TOTP web python manage.py crear_usuarios_prueba

Todas las cuentas con segundo factor comparten el mismo secreto TOTP (una sola
entrada en la app de autenticación sirve para todas). Los códigos de
recuperación se imprimen una vez. Las cuentas que ya existen no se tocan.
Corre en fase 'sistema' de RLS.
"""

import base64
import binascii
import os

from allauth.mfa.adapter import get_adapter as get_mfa_adapter
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal.auth import TOTP
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from autorizacion import roles
from cuentas import rls, services
from cuentas.models import EstadoCuenta, Usuario

# Sufijo de usuario -> rol. Hay varios VF y GC porque C2/C3/C7 exigen personas
# distintas (verificador nuevo en cada envío, GC independiente, doble aprobación).
CUENTAS = [
    ("tc", roles.TC), ("tc2", roles.TC),
    ("vf", roles.VF), ("vf2", roles.VF), ("vf3", roles.VF),
    ("gc", roles.GC), ("gc2", roles.GC),
    ("ac", roles.AC),
    ("ae", roles.AE),
    ("pf", roles.PF),
]
SIN_SEGUNDO_FACTOR = {roles.PF}
MIN_LARGO_TOTP = 32


def validar_secreto_totp(secreto: str) -> str:
    secreto = (secreto or "").strip().replace(" ", "").upper()
    if len(secreto) < MIN_LARGO_TOTP:
        raise CommandError(f"VZ_PRUEBA_TOTP debe tener al menos {MIN_LARGO_TOTP} caracteres base32.")
    try:
        base64.b32decode(secreto, casefold=True)
    except (binascii.Error, ValueError):
        raise CommandError("VZ_PRUEBA_TOTP no es base32 válido.")
    return secreto


class Command(BaseCommand):
    help = "Crea cuentas de prueba por rol con contraseña y TOTP leídos de VZ_PRUEBA_CLAVE y VZ_PRUEBA_TOTP."

    def add_arguments(self, parser):
        parser.add_argument("--prefijo", default="prueba", help="Prefijo de los usuarios (por defecto: prueba).")

    def handle(self, *args, prefijo, **opciones):
        clave = os.environ.get("VZ_PRUEBA_CLAVE", "")
        if not clave:
            raise CommandError("Defina VZ_PRUEBA_CLAVE en el entorno.")
        secreto = validar_secreto_totp(os.environ.get("VZ_PRUEBA_TOTP", ""))
        try:
            validate_password(clave)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))

        creados, existentes, codigos = [], [], {}
        rls.fijar_contexto(fase="sistema")
        try:
            with transaction.atomic():
                for sufijo, rol in CUENTAS:
                    username = f"{prefijo}_{sufijo}"
                    if Usuario.objects.filter(username=username).exists():
                        existentes.append(username)
                        continue
                    u = Usuario.objects.create_user(username=username, password=clave)
                    services.cambiar_estado(u, EstadoCuenta.ACTIVA)
                    services.agregar_rol(u, rol)
                    if rol not in SIN_SEGUNDO_FACTOR:
                        TOTP.activate(u, secreto)
                        codigos[username] = RecoveryCodes.activate(u).get_unused_codes()
                    creados.append((username, rol))
        finally:
            rls.limpiar_contexto()

        for username, rol in creados:
            self.stdout.write(f"{username:<16} {rol}  {'TOTP' if username in codigos else 'sin 2FA'}")
        for username in existentes:
            self.stdout.write(self.style.WARNING(f"{username:<16} ya existía: no se modificó"))
        if codigos:
            ejemplo = Usuario.objects.get(username=next(iter(codigos)))
            self.stdout.write("\nURI para la app de autenticación (misma para todas):")
            self.stdout.write(get_mfa_adapter().build_totp_url(ejemplo, secreto))
            self.stdout.write("\nCódigos de recuperación (se muestran solo ahora):")
            for username, lista in codigos.items():
                self.stdout.write(f"{username:<16} {' '.join(lista)}")
        self.stdout.write(self.style.SUCCESS(f"\n{len(creados)} cuentas creadas, {len(existentes)} ya existían."))
