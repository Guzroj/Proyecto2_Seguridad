from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

RUTA_SQL = Path("db") / "grant_after_migrate.sql"


class Command(BaseCommand):
    help = "Otorga a app_runtime los privilegios DML de db/grant_after_migrate.sql (rol dueño)."

    def handle(self, *args, **opciones):
        ruta = Path(settings.BASE_DIR) / RUTA_SQL
        if not ruta.is_file():
            raise CommandError(f"No existe {ruta}.")
        sql = ruta.read_text(encoding="utf-8")
        # Sin parámetros, psycopg envía el script como una sola consulta simple:
        # el bloque DO de PostgreSQL se ejecuta completo.
        with connection.cursor() as cursor:
            cursor.execute(sql)
        self.stdout.write(self.style.SUCCESS(f"Privilegios de app_runtime actualizados ({RUTA_SQL})."))
