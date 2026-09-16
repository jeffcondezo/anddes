from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0010_criterio_ponderacion_calculada"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="revision",
            name="maestro_revision_empresa_anio_uniq",
        ),
    ]
