from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("maestro", "0018_actividad_tipo_padre"),
    ]

    operations = [
        migrations.CreateModel(
            name="Acuerdo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("titulo", models.CharField(max_length=255)),
                (
                    "descripcion",
                    models.TextField(blank=True, help_text="Qué se acordó realizar."),
                ),
                ("fecha_compromiso", models.DateTimeField(blank=True, null=True)),
                (
                    "estado",
                    models.CharField(
                        choices=[
                            ("ABIERTO", "Abierto"),
                            ("CUMPLIDO", "Cumplido"),
                            ("CANCELADO", "Cancelado"),
                        ],
                        default="ABIERTO",
                        max_length=16,
                    ),
                ),
                ("correo_enviado_en", models.DateTimeField(blank=True, null=True)),
                ("ultimo_recordatorio_en", models.DateTimeField(blank=True, null=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "actividad",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="acuerdos",
                        to="maestro.actividad",
                    ),
                ),
                (
                    "creado_por",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="acuerdos_creados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "documentos",
                    models.ManyToManyField(
                        blank=True,
                        related_name="acuerdos",
                        to="maestro.empresadocumento",
                    ),
                ),
                (
                    "empresa",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="acuerdos",
                        to="maestro.empresa",
                    ),
                ),
            ],
            options={
                "verbose_name": "Acuerdo",
                "verbose_name_plural": "Acuerdos",
                "ordering": ["-creado_en"],
            },
        ),
    ]
