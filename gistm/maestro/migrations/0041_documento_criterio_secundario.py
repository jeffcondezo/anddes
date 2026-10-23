from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0040_pregunta"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentocriterio",
            name="documento_secundario",
            field=models.ForeignKey(
                blank=True,
                help_text="Documento complementario opcional (segunda fuente de evidencia).",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="criterios_estandar_secundarios",
                to="maestro.documento",
            ),
        ),
        migrations.AddField(
            model_name="documentocriterio",
            name="rol",
            field=models.CharField(
                choices=[
                    ("PRINCIPAL", "Principal"),
                    ("COMPLEMENTARIO", "Complementario"),
                ],
                default="PRINCIPAL",
                help_text="Principal = contenido en el documento; Complementario = buscar también en secundario.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="empresadocumentocriterio",
            name="empresa_documento_secundario",
            field=models.ForeignKey(
                blank=True,
                help_text="Documento complementario opcional en esta revisión.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="vinculos_criterio_secundarios",
                to="maestro.empresadocumento",
            ),
        ),
        migrations.AddField(
            model_name="empresadocumentocriterio",
            name="rol",
            field=models.CharField(
                choices=[
                    ("PRINCIPAL", "Principal"),
                    ("COMPLEMENTARIO", "Complementario"),
                ],
                default="PRINCIPAL",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="documentocriterio",
            name="documento",
            field=models.ForeignKey(
                help_text="Documento principal donde se busca primero la evidencia del criterio.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="criterios_estandar",
                to="maestro.documento",
            ),
        ),
        migrations.AlterField(
            model_name="empresadocumentocriterio",
            name="empresa_documento",
            field=models.ForeignKey(
                help_text="Documento principal de la evidencia del criterio.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="vinculos_criterio",
                to="maestro.empresadocumento",
            ),
        ),
        migrations.AlterField(
            model_name="empresadocumentocriterio",
            name="revision",
            field=models.ForeignKey(
                help_text="Denormalizado: un criterio solo una vez por revisión.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="vinculos_criterio",
                to="maestro.revision",
            ),
        ),
        migrations.AlterModelOptions(
            name="documentocriterio",
            options={
                "ordering": ["criterio__codigo", "documento__codigo"],
                "verbose_name": "Relación estándar documento-criterio",
                "verbose_name_plural": "Relaciones estándar documento-criterio",
            },
        ),
        migrations.AddConstraint(
            model_name="documentocriterio",
            constraint=models.UniqueConstraint(
                fields=("criterio",),
                name="maestro_documentocriterio_criterio_uniq",
            ),
        ),
    ]
