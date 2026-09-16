from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0011_revision_allow_multiple_per_year"),
    ]

    operations = [
        migrations.CreateModel(
            name="RevisionCriterioDesactivado",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "motivo",
                    models.TextField(
                        help_text="Motivo por el que el criterio no aplica a esta revisión/empresa.",
                    ),
                ),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "criterio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="desactivaciones_revision",
                        to="maestro.criterio",
                    ),
                ),
                (
                    "revision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="criterios_desactivados",
                        to="maestro.revision",
                    ),
                ),
            ],
            options={
                "verbose_name": "Criterio desactivado en revisión",
                "verbose_name_plural": "Criterios desactivados en revisión",
                "ordering": ["criterio__codigo"],
            },
        ),
        migrations.AddConstraint(
            model_name="revisioncriteriodesactivado",
            constraint=models.UniqueConstraint(
                fields=("revision", "criterio"),
                name="maestro_rev_criterio_desactivado_uniq",
            ),
        ),
    ]
