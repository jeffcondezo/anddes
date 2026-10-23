from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_preguntas_vinculos(apps, schema_editor):
    """Sembrar respuestas PENDIENTE para preguntas activas en vínculos existentes."""
    from collections import defaultdict

    Pregunta = apps.get_model("maestro", "Pregunta")
    EmpresaDocumentoCriterio = apps.get_model("maestro", "EmpresaDocumentoCriterio")
    EmpresaDocumentoPregunta = apps.get_model("maestro", "EmpresaDocumentoPregunta")

    por_criterio: dict[int, list] = defaultdict(list)
    for p in Pregunta.objects.filter(activo=True).order_by("orden", "codigo"):
        por_criterio[p.criterio_id].append(p)

    to_create = []
    for vinculo in EmpresaDocumentoCriterio.objects.all().iterator():
        preguntas = por_criterio.get(vinculo.criterio_id, [])
        for pregunta in preguntas:
            to_create.append(
                EmpresaDocumentoPregunta(
                    vinculo_criterio_id=vinculo.pk,
                    revision_id=vinculo.revision_id,
                    pregunta_id=pregunta.pk,
                    estado_revision="PENDIENTE",
                )
            )
        # Criterio ya APROBADO sin preguntas: queda incompleto (regla 2A).
        if not preguntas and vinculo.estado_revision == "APROBADO":
            EmpresaDocumentoCriterio.objects.filter(pk=vinculo.pk).update(
                estado_revision="PENDIENTE"
            )

    if to_create:
        EmpresaDocumentoPregunta.objects.bulk_create(to_create, ignore_conflicts=True)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("maestro", "0039_catalogo_activo"),
    ]

    operations = [
        migrations.CreateModel(
            name="Pregunta",
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
                ("codigo", models.CharField(max_length=32, unique=True)),
                ("texto", models.TextField()),
                (
                    "orden",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Orden de presentación dentro del criterio.",
                    ),
                ),
                (
                    "activo",
                    models.BooleanField(
                        default=True,
                        help_text="Si está deshabilitada, no se siembra en revisiones nuevas.",
                    ),
                ),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "criterio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="preguntas",
                        to="maestro.criterio",
                    ),
                ),
            ],
            options={
                "verbose_name": "Pregunta",
                "verbose_name_plural": "Preguntas",
                "ordering": ["criterio__codigo", "orden", "codigo"],
            },
        ),
        migrations.CreateModel(
            name="EmpresaDocumentoPregunta",
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
                (
                    "estado_revision",
                    models.CharField(
                        choices=[
                            ("PENDIENTE", "Pendiente"),
                            ("CUMPLE", "Cumple"),
                            ("NO_CUMPLE", "No cumple"),
                        ],
                        default="PENDIENTE",
                        help_text="CUMPLE suma al % del criterio; al 100% el criterio se aprueba.",
                        max_length=16,
                    ),
                ),
                (
                    "observacion",
                    models.TextField(
                        blank=True,
                        help_text="Observación del consultor cuando la pregunta no cumple.",
                    ),
                ),
                ("revisado_en", models.DateTimeField(blank=True, null=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "pregunta",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="respuestas_empresa",
                        to="maestro.pregunta",
                    ),
                ),
                (
                    "revisado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="preguntas_documento_revisadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "revision",
                    models.ForeignKey(
                        help_text="Denormalizado para unicidad pregunta–revisión.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="preguntas_documento",
                        to="maestro.revision",
                    ),
                ),
                (
                    "vinculo_criterio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="preguntas",
                        to="maestro.empresadocumentocriterio",
                    ),
                ),
            ],
            options={
                "verbose_name": "Respuesta a pregunta",
                "verbose_name_plural": "Respuestas a preguntas",
                "ordering": ["pregunta__orden", "pregunta__codigo"],
            },
        ),
        migrations.AddConstraint(
            model_name="empresadocumentopregunta",
            constraint=models.UniqueConstraint(
                fields=("revision", "pregunta"),
                name="maestro_empresadocpreg_revision_pregunta_uniq",
            ),
        ),
        migrations.RunPython(backfill_preguntas_vinculos, noop_reverse),
    ]
