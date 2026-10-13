from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0021_revision_fechas_periodo"),
    ]

    operations = [
        migrations.AddField(
            model_name="empresadocumento",
            name="prioritario",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Si está marcado, aparece en Documentos prioritarios "
                    "del dashboard de Indicadores."
                ),
            ),
        ),
    ]
