"""
Verifica un archivo de certificado con las huellas confiables configuradas.

    python manage.py verificar_certificado certificado.json

Fuera del servidor, el mismo algoritmo corre sin Django ni base de datos:
    python certificados/formato.py certificado.json --huella <huella publicada>
"""

from django.core.management.base import BaseCommand, CommandError

from certificados import firma, formato


class Command(BaseCommand):
    help = "Verifica la firma Ed25519 de un certificado y que la clave sea de Verdeazul."

    def add_arguments(self, parser):
        parser.add_argument("archivo")

    def handle(self, *args, archivo, **opciones):
        with open(archivo, "rb") as f:
            datos = f.read(formato.TAMANO_MAXIMO + 1)
        r = formato.verificar(datos, firma.huellas_confiables())
        if r["valido"]:
            self.stdout.write(self.style.SUCCESS("VÁLIDO: firma correcta y clave de Verdeazul."))
            return
        for error in r["errores"]:
            self.stderr.write(f"- {error}")
        raise CommandError("NO VÁLIDO")
