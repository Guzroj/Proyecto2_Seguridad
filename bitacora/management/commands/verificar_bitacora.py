"""
Recalcula la cadena de hashes de la bitácora y la compara con los sellos firmados.

    python manage.py verificar_bitacora

Corre con los permisos de la conexión: con app_runtime, en la fase 'sistema' (verificación
diaria de la Arquitectura §4.5); un auditor puede correr bitacora_verificar() con sus propias
credenciales de lectura.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bitacora import sellos
from bitacora.models import Evento, SelloBitacora
from bitacora.servicios import verificar_cadena
from cuentas import rls


class Command(BaseCommand):
    help = "Verifica la integridad de la bitácora encadenada y sus sellos."

    def handle(self, *args, **opciones):
        with transaction.atomic():
            rls.fijar_contexto(fase="sistema")
            try:
                problemas = verificar_cadena() + sellos.verificar()
                total, sellados = Evento.objects.count(), SelloBitacora.objects.count()
            finally:
                rls.fijar_contexto()
        if problemas:
            for secuencia, problema in sorted(problemas):
                self.stderr.write(f"- evento #{secuencia}: {problema}")
            raise CommandError(f"Bitácora ALTERADA: {len(problemas)} problema(s) en {total} eventos.")
        self.stdout.write(self.style.SUCCESS(
            f"Bitácora íntegra: {total} eventos encadenados y {sellados} sellos firmados que coinciden."))
