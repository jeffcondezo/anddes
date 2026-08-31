# Generated manually for Revision model

import django.db.models.deletion
from django.db import migrations, models


def crear_revisiones_y_asignar(apps, schema_editor):
    Empresa = apps.get_model("maestro", "Empresa")
    Revision = apps.get_model("maestro", "Revision")
    EmpresaDocumento = apps.get_model("maestro", "EmpresaDocumento")

    for empresa in Empresa.objects.all():
        revision, _ = Revision.objects.get_or_create(
            empresa_id=empresa.pk,
            anio=2026,
            defaults={"nombre": "Migración inicial", "activo": True},
        )
        EmpresaDocumento.objects.filter(empresa_id=empresa.pk).update(revision_id=revision.pk)


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0006_empresa_documento"),
    ]

    operations = [
        migrations.CreateModel(
            name="Revision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("anio", models.PositiveSmallIntegerField()),
                ("nombre", models.CharField(blank=True, max_length=255)),
                ("activo", models.BooleanField(default=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "empresa",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="revisiones",
                        to="maestro.empresa",
                    ),
                ),
            ],
            options={
                "verbose_name": "Revisión",
                "verbose_name_plural": "Revisiones",
                "ordering": ["-anio", "-creado_en"],
            },
        ),
        migrations.AddConstraint(
            model_name="revision",
            constraint=models.UniqueConstraint(
                fields=("empresa", "anio"),
                name="maestro_revision_empresa_anio_uniq",
            ),
        ),
        migrations.AddField(
            model_name="empresadocumento",
            name="revision",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="documentos",
                to="maestro.revision",
            ),
        ),
        migrations.RunPython(crear_revisiones_y_asignar, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="empresadocumento",
            name="maestro_empresadoc_empresa_codigo_uniq",
        ),
        migrations.RemoveConstraint(
            model_name="empresadocumento",
            name="maestro_empresadoc_empresa_documento_uniq",
        ),
        migrations.RemoveField(
            model_name="empresadocumento",
            name="empresa",
        ),
        migrations.AlterField(
            model_name="empresadocumento",
            name="revision",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="documentos",
                to="maestro.revision",
            ),
        ),
        migrations.AddConstraint(
            model_name="empresadocumento",
            constraint=models.UniqueConstraint(
                fields=("revision", "codigo"),
                name="maestro_empresadoc_revision_codigo_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="empresadocumento",
            constraint=models.UniqueConstraint(
                condition=models.Q(("documento__isnull", False)),
                fields=("revision", "documento"),
                name="maestro_empresadoc_revision_documento_uniq",
            ),
        ),
    ]
