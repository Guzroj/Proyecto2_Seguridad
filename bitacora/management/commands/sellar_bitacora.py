"""
Sella la cabeza de la cadena de la bitácora (Arquitectura §4.5).

    python manage.py sellar_bitacora

Corre cada hora como Cloud Run Job (verdeazul-sellar-bitacora, Cloud Scheduler) con el rol de
la aplicación en la fase 'sistema'. Firma con la clave de certificados (CERTIFICADO_CLAVE_PRIVADA,
desde Secret Manager), guarda el sello en la base y lo imprime como una línea JSON: en Cloud Run
esa salida queda en Cloud Logging, una copia que quien administre la base no puede tocar.

Antes de sellar verifica la cadena y los sellos anteriores (la verificación periódica de §4.5):
si encuentra una alteración, NO sella (sellaría una cadena manipulada) y termina con error, así
el Job queda fallido y visible en Cloud Run.
"""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bitacora import sellos
from bitacora.servicios import verificar_cadena
from certificados.firma import ClaveNoDisponible
from cuentas import rls
from nucleo import ganchos
from nucleo.acciones import Accion


class Command(BaseCommand):
    help = "Firma la cabeza de la cadena de la bitácora y la publica en la salida (Cloud Logging)."

    def handle(self, *args, **opciones):
        with transaction.atomic():
            rls.fijar_contexto(fase="sistema")
            try:
                problemas = sorted(verificar_cadena() + sellos.verificar())
                if problemas:
                    for secuencia, problema in problemas:
                        self.stderr.write(f"- evento #{secuencia}: {problema}")
                    raise CommandError(f"Bitácora ALTERADA ({len(problemas)} problema(s)): no se sella.")
                sello = sellos.sellar()
                if sello is not None:
                    ganchos.registrar_evento(
                        actor=None, accion=Accion.BITACORA_SELLAR, recurso_tipo="sello", recurso_id=str(sello.pk),
                        despues={"secuencia": sello.secuencia, "hash": sello.hash,
                                 "huella_clave": sello.huella_clave},
                        detalle="sello horario de la cabeza de la cadena")
            except ClaveNoDisponible as exc:
                raise CommandError(f"No se pudo sellar: {exc}") from None
            finally:
                rls.fijar_contexto()
        if sello is None:
            self.stdout.write("Bitácora vacía: nada que sellar.")
            return
        self.stdout.write(json.dumps({
            "tipo": "verdeazul.sello_bitacora", "id": str(sello.pk), "secuencia": sello.secuencia,
            "hash": sello.hash, "momento": sello.momento.isoformat(), "huella_clave": sello.huella_clave,
            "contenido": sello.contenido, "firma": sello.firma,
        }, ensure_ascii=False))
