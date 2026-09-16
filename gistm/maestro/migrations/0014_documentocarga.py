# Generated manually for DocumentoCarga

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import maestro.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("maestro", "0013_criterio_unico_por_revision"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentoCarga",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("archivo", models.FileField(upload_to=maestro.models.documento_carga_upload_to)),
                ("nombre_original", models.CharField(blank=True, max_length=255)),
                ("observaciones", models.TextField(blank=True)),
                (
                    "activo",
                    models.BooleanField(
                        default=True,
                        help_text="La carga vigente es la más reciente con activo=True.",
                    ),
                ),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                (
                    "empresa_documento",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cargas",
                        to="maestro.empresadocumento",
                    ),
                ),
                (
                    "subido_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="documentos_cargados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Carga de documento",
                "verbose_name_plural": "Cargas de documento",
                "ordering": ["-creado_en"],
            },
        ),
    ]
