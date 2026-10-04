from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0017_actividad"),
    ]

    operations = [
        migrations.AddField(
            model_name="actividad",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("GENERAL", "General"),
                    ("ACUERDO", "Acuerdo"),
                    ("RECORDATORIO", "Recordatorio"),
                ],
                default="GENERAL",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="actividad",
            name="actividad_padre",
            field=models.ForeignKey(
                blank=True,
                help_text="Actividad origen (reunión) de la que nace un acuerdo o recordatorio.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="hijas",
                to="maestro.actividad",
            ),
        ),
    ]
