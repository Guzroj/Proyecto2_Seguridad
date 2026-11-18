"""
Genera una clave Ed25519 nueva para firmar certificados.

    python manage.py generar_clave_certificados            # muestra la huella y la clave privada
    python manage.py generar_clave_certificados --archivo ruta   # escribe la privada en un archivo (0600)

La clave privada es un SECRETO: cárguela en Secret Manager (verdeazul-certificado-clave)
o en .env (local) y no la pegue en el repositorio, en tickets ni en chats. Publique la
huella (README y /certificados/clave-publica/) y agréguela a CERTIFICADO_CLAVES_CONFIABLES
sin quitar las anteriores, para que los certificados viejos sigan verificando.
"""

import os

from django.core.management.base import BaseCommand

from certificados import formato
from certificados.firma import nueva_clave


class Command(BaseCommand):
    help = "Genera una clave Ed25519 para firmar certificados (privada + huella pública)."

    def add_arguments(self, parser):
        parser.add_argument("--archivo", help="Escribir la clave privada en este archivo (permisos 0600).")

    def handle(self, *args, archivo=None, **opciones):
        privada, huella = nueva_clave()
        self.stdout.write(f"Huella pública (publicar): {huella}")
        self.stdout.write(f"                           {formato.huella_legible(huella)}")
        if archivo:
            descriptor = os.open(archivo, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii") as f:
                f.write(privada + "\n")
            self.stdout.write(f"Clave privada escrita en {archivo} (no la versione).")
        else:
            self.stdout.write("CERTIFICADO_CLAVE_PRIVADA (SECRETO, solo para Secret Manager o .env):")
            self.stdout.write(privada)
