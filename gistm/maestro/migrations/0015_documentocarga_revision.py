# Generated manually for DocumentoCarga revision workflow

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def aprobar_cargas_existentes(apps, schema_editor):
    DocumentoCarga = apps.get_model("maestro", "DocumentoCarga")
    DocumentoCarga.objects.all().update(estado_revision="APROBADO")


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("maestro", "0014_documentocarga"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentocarga",
            name="estado_revision",
            field=models.CharField(
                choices=[
                    ("PENDIENTE", "Pendiente de revisión"),
                    ("APROBADO", "Aprobado"),
                    ("RECHAZADO", "Rechazado"),
                ],
                default="PENDIENTE",
                help_text="Solo las cargas APROBADO cuentan en el avance del cliente.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="documentocarga",
            name="motivo_rechazo",
            field=models.TextField(
                blank=True,
                help_text="Mensaje visible para la minera cuando se rechaza la evidencia.",
            ),
        ),
        migrations.AddField(
            model_name="documentocarga",
            name="revisado_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="documentocarga",
            name="revisado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="documentos_revisados",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(aprobar_cargas_existentes, migrations.RunPython.noop),
    ]
