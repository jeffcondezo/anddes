from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0022_empresadocumento_prioritario"),
    ]

    operations = [
        migrations.CreateModel(
            name="GlosarioTermino",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("concepto", models.CharField(max_length=255, unique=True)),
                ("descripcion", models.TextField()),
                ("activo", models.BooleanField(default=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Término de glosario",
                "verbose_name_plural": "Términos de glosario",
                "ordering": ["concepto"],
            },
        ),
    ]
