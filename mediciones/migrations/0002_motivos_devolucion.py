"""
Catálogo cerrado de motivos de devolución (C2: devolver exige un motivo del catálogo).

La tabla tiene RLS con FORCE (cuentas.0023): solo se escribe en la fase 'sistema',
así que la carga la fija para esta transacción (set_config local) y funciona igual
con el dueño de las tablas en Neon, que no es superusuario.
"""

from django.db import migrations

MOTIVOS = [
    ("gps_fuera", "Coordenadas fuera de la parcela"),
    ("datos_incompletos", "Datos de campo incompletos"),
    ("valor_atipico", "Valores atípicos frente a la medición anterior"),
    ("evidencia_insuficiente", "Evidencia fotográfica insuficiente"),
    ("parcela_incorrecta", "Parcela o geometría incorrecta"),
    ("otro", "Otro motivo (detallar en el comentario)"),
]


def _fase_sistema(schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.fase', 'sistema', true)")


def cargar(apps, schema_editor):
    _fase_sistema(schema_editor)
    Motivo = apps.get_model("mediciones", "MotivoDevolucion")
    for codigo, descripcion in MOTIVOS:
        Motivo.objects.get_or_create(codigo=codigo, defaults={"descripcion": descripcion})


def descargar(apps, schema_editor):
    # RG1: nada se borra; al revertir solo se desactivan.
    _fase_sistema(schema_editor)
    apps.get_model("mediciones", "MotivoDevolucion").objects.filter(
        codigo__in=[c for c, _ in MOTIVOS]).update(activo=False)


class Migration(migrations.Migration):
    dependencies = [
        ("mediciones", "0001_initial"),
        ("cuentas", "0023_rls_mediciones_enmiendas"),
    ]

    operations = [migrations.RunPython(cargar, descargar)]
