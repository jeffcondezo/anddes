# Generated manually for DocumentoMensaje

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("maestro", "0015_documentocarga_revision"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentoMensaje",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "es_consultor",
                    models.BooleanField(
                        default=False,
                        help_text="True si el mensaje lo escribe admin/gestor; False si lo escribe el cliente.",
                    ),
                ),
                (
                    "tipo",
                    models.CharField(
                        choices=[
                            ("RECHAZO", "Rechazo"),
                            ("OBSERVACION", "Observación"),
                            ("RESPUESTA", "Respuesta / nueva carga"),
                            ("ACUERDO", "Acuerdo"),
                            ("SISTEMA", "Sistema"),
                        ],
                        default="OBSERVACION",
                        max_length=16,
                    ),
                ),
                ("texto", models.TextField()),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                (
                    "autor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="mensajes_documento",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "carga",
                    models.ForeignKey(
                        blank=True,
                        help_text="Carga asociada, si el mensaje nace de un rechazo o una nueva evidencia.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="mensajes",
                        to="maestro.documentocarga",
                    ),
                ),
                (
                    "empresa_documento",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mensajes",
                        to="maestro.empresadocumento",
                    ),
                ),
            ],
            options={
                "verbose_name": "Mensaje de documento",
                "verbose_name_plural": "Mensajes de documento",
                "ordering": ["creado_en"],
            },
        ),
    ]
