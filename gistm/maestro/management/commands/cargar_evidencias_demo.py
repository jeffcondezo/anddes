from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction

from maestro.models import DocumentoCarga, EmpresaDocumento, Revision, TipoUsuario
from maestro.progress import calcular_progreso_revision, revision_activa_empresa

User = get_user_model()

# PDF mínimo válido para demos locales.
_MINI_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 300 144]/Parent 2 0 R>>endobj\n"
    b"trailer<</Size 4/Root 1 0 R>>\n"
    b"%%EOF\n"
)


class Command(BaseCommand):
    help = (
        "Carga evidencias de prueba en revisiones activas "
        "(sin borrar cargas existentes salvo --reset)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--pct",
            type=int,
            default=40,
            help="Porcentaje de documentos activos a marcar como cargados (default 40).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Elimina cargas demo previas de las revisiones afectadas antes de cargar.",
        )
        parser.add_argument(
            "--empresa",
            type=int,
            action="append",
            dest="empresas",
            help="ID de empresa (repetible). Por defecto: todas con revisión activa y docs.",
        )

    def handle(self, *args, **options):
        pct = max(1, min(100, options["pct"]))
        empresa_ids = options.get("empresas")

        revisiones = (
            Revision.objects.filter(activo=True)
            .select_related("empresa")
            .order_by("empresa__nombre", "-anio", "-creado_en")
        )
        if empresa_ids:
            revisiones = revisiones.filter(empresa_id__in=empresa_ids)

        # Una revisión activa “canónica” por empresa (la más reciente).
        vistas: dict[int, Revision] = {}
        for rev in revisiones:
            if rev.empresa_id not in vistas:
                # Preferir la misma regla del portal
                activa = revision_activa_empresa(rev.empresa_id)
                if activa:
                    vistas[rev.empresa_id] = activa

        if not vistas:
            self.stdout.write(self.style.WARNING("No hay revisiones activas para cargar."))
            return

        with transaction.atomic():
            for empresa_id, revision in vistas.items():
                self._seed_revision(revision, pct=pct, reset=options["reset"])

        self.stdout.write(self.style.SUCCESS("Evidencias de prueba cargadas."))

    def _seed_revision(self, revision: Revision, *, pct: int, reset: bool) -> None:
        docs = list(
            EmpresaDocumento.objects.filter(revision=revision, activo=True).order_by("codigo")
        )
        if not docs:
            self.stdout.write(f"  · {revision.empresa.nombre}: sin documentos, omitida.")
            return

        if reset:
            deleted, _ = DocumentoCarga.objects.filter(
                empresa_documento__revision=revision,
                observaciones__startswith="[demo]",
            ).delete()
            self.stdout.write(f"  · {revision.empresa.nombre}: reset demo ({deleted} cargas).")

        uploader = (
            User.objects.filter(
                perfil__tipo=TipoUsuario.CLIENTE,
                perfil__empresa_id=revision.empresa_id,
                perfil__activo=True,
            )
            .order_by("id")
            .first()
        )
        if uploader is None:
            uploader = (
                User.objects.filter(perfil__tipo=TipoUsuario.ADMINISTRADOR)
                .order_by("id")
                .first()
            )

        # Documentos sin carga vigente
        pendientes = [
            d
            for d in docs
            if not DocumentoCarga.objects.filter(empresa_documento=d, activo=True).exists()
        ]
        ya = len(docs) - len(pendientes)
        objetivo = max(1, round(len(docs) * pct / 100))
        a_crear = max(0, objetivo - ya)
        seleccion = pendientes[:a_crear]

        creadas = 0
        for doc in seleccion:
            nombre = f"{doc.codigo}_evidencia_demo.pdf"
            carga = DocumentoCarga(
                empresa_documento=doc,
                observaciones="[demo] Evidencia de prueba para revisión visual del seguimiento.",
                subido_por=uploader,
                activo=True,
                estado_revision="APROBADO",
                nombre_original=nombre,
            )
            carga.archivo.save(nombre, ContentFile(_MINI_PDF), save=False)
            carga.save()
            creadas += 1

        prog = calcular_progreso_revision(revision)
        self.stdout.write(
            f"  · {revision.empresa.nombre} · {revision.etiqueta}: "
            f"+{creadas} cargas → docs {prog.docs_cargados}/{prog.docs_total} "
            f"({prog.docs_pct}%), criterios {prog.criterios_completos}/{prog.criterios_total} "
            f"({prog.criterios_pct}%)"
        )
