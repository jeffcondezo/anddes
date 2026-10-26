# Generated manually for cliente lectura/carga + revisiones permitidas

from django.db import migrations, models


def migrar_clientes(apps, schema_editor):
    PerfilUsuario = apps.get_model("maestro", "PerfilUsuario")
    Revision = apps.get_model("maestro", "Revision")
    Through = PerfilUsuario.revisiones.through

    for perfil in PerfilUsuario.objects.filter(tipo="CLIENTE").iterator():
        perfil.tipo = "CLIENTE_CARGA"
        perfil.save(update_fields=["tipo"])

    # Clientes existentes conservan acceso a todas las revisiones de su empresa.
    filas = []
    for perfil in PerfilUsuario.objects.filter(
        tipo__in=["CLIENTE_CARGA", "CLIENTE_LECTURA"],
        empresa_id__isnull=False,
    ).iterator():
        for rev_id in Revision.objects.filter(empresa_id=perfil.empresa_id).values_list(
            "pk", flat=True
        ):
            filas.append(
                Through(perfilusuario_id=perfil.pk, revision_id=rev_id)
            )
    if filas:
        Through.objects.bulk_create(filas, ignore_conflicts=True)


def revertir_clientes(apps, schema_editor):
    PerfilUsuario = apps.get_model("maestro", "PerfilUsuario")
    PerfilUsuario.objects.filter(
        tipo__in=["CLIENTE_CARGA", "CLIENTE_LECTURA"]
    ).update(tipo="CLIENTE")


class Migration(migrations.Migration):

    dependencies = [
        ("maestro", "0048_documentocarga_url_evidencia"),
    ]

    operations = [
        migrations.AlterField(
            model_name="perfilusuario",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("ADMINISTRADOR", "Administrador"),
                    ("GESTOR", "Gestor"),
                    ("CLIENTE", "Cliente"),
                    ("CLIENTE_LECTURA", "Cliente (solo lectura)"),
                    ("CLIENTE_CARGA", "Cliente (con carga)"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="perfilusuario",
            name="revisiones",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "Revisiones a las que puede acceder el cliente. "
                    "Si está vacío, no ve ninguna revisión de la empresa."
                ),
                related_name="clientes_autorizados",
                to="maestro.revision",
            ),
        ),
        migrations.RunPython(migrar_clientes, revertir_clientes),
        migrations.AlterField(
            model_name="perfilusuario",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("ADMINISTRADOR", "Administrador"),
                    ("GESTOR", "Gestor"),
                    ("CLIENTE_LECTURA", "Cliente (solo lectura)"),
                    ("CLIENTE_CARGA", "Cliente (con carga)"),
                ],
                max_length=20,
            ),
        ),
    ]
