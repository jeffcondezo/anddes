# Generated manually for splitting estado / fase on Revision

from django.db import migrations, models

FASES = ("PROYECTO", "CONSTRUCCION", "OPERACION", "CIERRE")


def separar_estado_fase(apps, schema_editor):
    Revision = apps.get_model("maestro", "Revision")
    for rev in Revision.objects.filter(estado__in=FASES):
        rev.fase = rev.estado
        rev.estado = ""
        rev.save(update_fields=["fase", "estado"])


def reunir_fase_en_estado(apps, schema_editor):
    Revision = apps.get_model("maestro", "Revision")
    for rev in Revision.objects.exclude(fase="").filter(estado=""):
        rev.estado = rev.fase
        rev.fase = ""
        rev.save(update_fields=["estado", "fase"])


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0037_documento_fecha_limite_entrega"),
    ]

    operations = [
        migrations.AddField(
            model_name="revision",
            name="fase",
            field=models.CharField(
                blank=True,
                choices=[
                    ("PROYECTO", "Proyecto"),
                    ("CONSTRUCCION", "Construcción"),
                    ("OPERACION", "Operación"),
                    ("CIERRE", "Cierre"),
                ],
                default="",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="revision",
            name="estado",
            field=models.CharField(
                blank=True,
                choices=[("NUEVA", "Nueva"), ("EXISTENTE", "Existente")],
                default="",
                max_length=16,
            ),
        ),
        migrations.RunPython(separar_estado_fase, reunir_fase_en_estado),
    ]
