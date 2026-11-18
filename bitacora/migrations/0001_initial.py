import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Evento",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("secuencia", models.BigIntegerField(editable=False, null=True, unique=True)),
                ("momento", models.DateTimeField(editable=False, help_text="Hora del servidor de base de datos (UTC).",
                                                 null=True)),
                ("hash_anterior", models.CharField(blank=True, editable=False, max_length=64)),
                ("hash", models.CharField(blank=True, editable=False, max_length=64)),
                ("actor_nombre", models.CharField(blank=True, max_length=150)),
                ("roles", models.JSONField(blank=True, default=list)),
                ("accion", models.CharField(max_length=64)),
                ("recurso_tipo", models.CharField(blank=True, max_length=64)),
                ("recurso_id", models.CharField(blank=True, max_length=64)),
                ("resultado", models.CharField(choices=[("OK", "Cambio realizado"), ("CONCEDIDO", "Lectura concedida"),
                                                        ("DENEGADO", "Denegado")], max_length=10)),
                ("antes", models.JSONField(blank=True, null=True)),
                ("despues", models.JSONField(blank=True, null=True)),
                ("detalle", models.CharField(blank=True, max_length=1000)),
                ("origen", models.CharField(blank=True, help_text="Dirección IP de la petición, si la hubo.",
                                            max_length=45)),
                ("actor", models.ForeignKey(
                    blank=True, help_text="Cuenta que actuó; vacío para procesos del sistema (tarea diaria, motor HBAC).",
                    null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "evento de bitácora",
                "verbose_name_plural": "eventos de bitácora",
                "ordering": ["-secuencia"],
                "indexes": [
                    models.Index(fields=["accion"], name="bitacora_evento_accion"),
                    models.Index(fields=["recurso_tipo", "recurso_id"], name="bitacora_evento_recurso"),
                    models.Index(fields=["actor"], name="bitacora_evento_actor"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("hash_anterior",), name="bitacora_sin_bifurcacion"),
                    models.UniqueConstraint(fields=("hash",), name="bitacora_hash_unico"),
                    models.CheckConstraint(condition=models.Q(("resultado__in", ["OK", "CONCEDIDO", "DENEGADO"])),
                                           name="bitacora_resultado_valido"),
                ],
            },
        ),
    ]
