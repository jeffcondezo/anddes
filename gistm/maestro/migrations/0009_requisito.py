from django.db import migrations, models
import django.db.models.deletion


def crear_requisitos_provisionales(apps, schema_editor):
    Principio = apps.get_model("maestro", "Principio")
    Requisito = apps.get_model("maestro", "Requisito")
    Criterio = apps.get_model("maestro", "Criterio")

    for principio in Principio.objects.all():
        criterios = Criterio.objects.filter(principio_id=principio.pk, requisito_id__isnull=True)
        if not criterios.exists():
            continue
        codigo = f"REQ-{principio.codigo}"
        requisito, _ = Requisito.objects.get_or_create(
            codigo=codigo,
            defaults={
                "descripcion": (
                    f"Requisito provisional creado en migración para el principio {principio.codigo}."
                ),
                "principio_id": principio.pk,
            },
        )
        criterios.update(requisito_id=requisito.pk)


def revertir_requisitos(apps, schema_editor):
    Requisito = apps.get_model("maestro", "Requisito")
    Requisito.objects.filter(codigo__startswith="REQ-").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0008_documento_criterio"),
    ]

    operations = [
        migrations.CreateModel(
            name="Requisito",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo", models.CharField(max_length=32, unique=True)),
                ("descripcion", models.TextField()),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "principio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="requisitos",
                        to="maestro.principio",
                    ),
                ),
            ],
            options={
                "verbose_name": "Requisito",
                "verbose_name_plural": "Requisitos",
                "ordering": ["codigo"],
            },
        ),
        migrations.AddField(
            model_name="criterio",
            name="requisito",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="criterios",
                to="maestro.requisito",
            ),
        ),
        migrations.RunPython(crear_requisitos_provisionales, revertir_requisitos),
        migrations.AlterField(
            model_name="criterio",
            name="requisito",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="criterios",
                to="maestro.requisito",
            ),
        ),
        migrations.AlterField(
            model_name="criterio",
            name="principio",
            field=models.ForeignKey(
                help_text="Denormalizado desde el requisito para facilitar consultas.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="criterios",
                to="maestro.principio",
            ),
        ),
    ]
