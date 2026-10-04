# Generated manually for Actividad

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("maestro", "0016_documentomensaje"),
    ]

    operations = [
        migrations.CreateModel(
            name="Actividad",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("titulo", models.CharField(max_length=255)),
                (
                    "descripcion",
                    models.TextField(blank=True, help_text="Tenor / agenda / notas de la actividad."),
                ),
                (
                    "alcance",
                    models.CharField(
                        choices=[
                            ("PERSONAL", "Solo consultor"),
                            ("CON_CLIENTE", "Con empresa / cliente"),
                        ],
                        default="PERSONAL",
                        max_length=16,
                    ),
                ),
                ("inicio", models.DateTimeField()),
                ("fin", models.DateTimeField(blank=True, null=True)),
                (
                    "enlace_reunion",
                    models.URLField(blank=True, help_text="Enlace externo (Teams, Meet, Zoom, etc.)."),
                ),
                (
                    "enviar_correo",
                    models.BooleanField(
                        default=False,
                        help_text="Si es con cliente, notificar por correo a los usuarios cliente de la empresa.",
                    ),
                ),
                ("correo_enviado_en", models.DateTimeField(blank=True, null=True)),
                (
                    "estado",
                    models.CharField(
                        choices=[
                            ("PROGRAMADA", "Programada"),
                            ("COMPLETADA", "Completada"),
                            ("CANCELADA", "Cancelada"),
                        ],
                        default="PROGRAMADA",
                        max_length=16,
                    ),
                ),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "creado_por",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="actividades_creadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "documentos",
                    models.ManyToManyField(
                        blank=True,
                        related_name="actividades",
                        to="maestro.empresadocumento",
                    ),
                ),
                (
                    "empresa",
                    models.ForeignKey(
                        blank=True,
                        help_text="Obligatoria si la actividad es con el cliente.",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="actividades",
                        to="maestro.empresa",
                    ),
                ),
            ],
            options={
                "verbose_name": "Actividad",
                "verbose_name_plural": "Actividades",
                "ordering": ["inicio"],
            },
        ),
    ]
