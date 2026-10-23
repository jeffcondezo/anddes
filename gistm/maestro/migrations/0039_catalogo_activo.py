from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0038_revision_fase"),
    ]

    operations = [
        migrations.AddField(
            model_name="tema",
            name="activo",
            field=models.BooleanField(
                default=True,
                help_text="Si está deshabilitado, sus criterios no se incluyen en revisiones nuevas.",
            ),
        ),
        migrations.AddField(
            model_name="principio",
            name="activo",
            field=models.BooleanField(
                default=True,
                help_text="Si está deshabilitado, sus criterios no se incluyen en revisiones nuevas.",
            ),
        ),
        migrations.AddField(
            model_name="requisito",
            name="activo",
            field=models.BooleanField(
                default=True,
                help_text="Si está deshabilitado, sus criterios no se incluyen en revisiones nuevas.",
            ),
        ),
        migrations.AddField(
            model_name="criterio",
            name="activo",
            field=models.BooleanField(
                default=True,
                help_text="Si está deshabilitado, no se incluye en revisiones nuevas.",
            ),
        ),
    ]
