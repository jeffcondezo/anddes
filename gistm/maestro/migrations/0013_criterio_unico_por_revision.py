from django.db import migrations, models
import django.db.models.deletion


def rellenar_y_deduplicar(apps, schema_editor):
    EmpresaDocumentoCriterio = apps.get_model("maestro", "EmpresaDocumentoCriterio")

    for vinculo in EmpresaDocumentoCriterio.objects.select_related("empresa_documento").all():
        EmpresaDocumentoCriterio.objects.filter(pk=vinculo.pk).update(
            revision_id=vinculo.empresa_documento.revision_id
        )

    vistos = set()
    for vinculo in EmpresaDocumentoCriterio.objects.order_by("id"):
        key = (vinculo.revision_id, vinculo.criterio_id)
        if key in vistos:
            vinculo.delete()
        else:
            vistos.add(key)


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0012_revision_criterio_desactivado"),
    ]

    operations = [
        migrations.AddField(
            model_name="empresadocumentocriterio",
            name="revision",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="vinculos_criterio",
                to="maestro.revision",
                help_text="Denormalizado desde el documento para garantizar un criterio por revisión.",
            ),
        ),
        migrations.RunPython(rellenar_y_deduplicar, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="empresadocumentocriterio",
            name="revision",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="vinculos_criterio",
                to="maestro.revision",
                help_text="Denormalizado desde el documento para garantizar un criterio por revisión.",
            ),
        ),
        migrations.AddConstraint(
            model_name="empresadocumentocriterio",
            constraint=models.UniqueConstraint(
                fields=("revision", "criterio"),
                name="maestro_empresadoccrit_revision_criterio_uniq",
            ),
        ),
    ]
