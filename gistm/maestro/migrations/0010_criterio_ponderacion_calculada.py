from decimal import Decimal

from django.db import migrations, models


def recalcular_ponderaciones(apps, schema_editor):
    Requisito = apps.get_model("maestro", "Requisito")
    Principio = apps.get_model("maestro", "Principio")
    Criterio = apps.get_model("maestro", "Criterio")

    for requisito in Requisito.objects.all():
        qs = Criterio.objects.filter(requisito_id=requisito.pk)
        n = qs.count()
        if n == 0:
            continue
        peso = (Decimal("1") / Decimal(n)).quantize(Decimal("0.0001"))
        qs.update(ponderacion=peso)

    for principio in Principio.objects.all():
        total = Decimal("0")
        for valor in Criterio.objects.filter(principio_id=principio.pk).values_list(
            "ponderacion", flat=True
        ):
            total += valor or Decimal("0")
        Principio.objects.filter(pk=principio.pk).update(suma_ponderacion=total)


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0009_requisito"),
    ]

    operations = [
        migrations.AlterField(
            model_name="criterio",
            name="ponderacion",
            field=models.DecimalField(
                decimal_places=4,
                default=Decimal("1"),
                help_text="Calculada automáticamente como 1 / N criterios del mismo requisito.",
                max_digits=8,
            ),
        ),
        migrations.RunPython(recalcular_ponderaciones, migrations.RunPython.noop),
    ]
