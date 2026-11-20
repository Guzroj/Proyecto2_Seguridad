"""
Tarea diaria de cuentas (proceso sin usuario, fase 'sistema' de RLS):
activa las cuentas AE cuya ventana aprobada empieza hoy y da de baja las de
ventana vencida (C5, C14).

Debe correr una vez al día, poco después de la medianoche de Costa Rica.
En producción: un Cloud Run Job con este comando, disparado por Cloud Scheduler
(p. ej. "5 0 * * *", zona America/Costa_Rica). En local:

    docker compose exec web python manage.py tarea_diaria_cuentas
"""

from django.core.management.base import BaseCommand

from cuentas import rls, services


class Command(BaseCommand):
    help = "Activa cuentas AE el día de inicio de su ventana y da de baja las de ventana vencida."

    def handle(self, *args, **opciones):
        rls.fijar_contexto(fase="sistema")
        try:
            resultado = services.procesar_ventanas()
        finally:
            rls.limpiar_contexto()
        self.stdout.write(
            self.style.SUCCESS(
                f"Cuentas AE activadas: {resultado['activadas']}. Dadas de baja: {resultado['dadas_de_baja']}."
            )
        )
