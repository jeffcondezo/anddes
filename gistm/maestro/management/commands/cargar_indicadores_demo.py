from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from maestro.models import (
    DocumentoCarga,
    EmpresaDocumento,
    EstadoRevisionCarga,
    OrigenEmpresaDocumento,
    Revision,
    TipoUsuario,
)
from maestro.progress import calcular_progreso_revision, revision_activa_empresa

User = get_user_model()

_MINI_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 300 144]/Parent 2 0 R>>endobj\n"
    b"trailer<</Size 4/Root 1 0 R>>\n"
    b"%%EOF\n"
)

_TAG = "[demo-indicadores]"


class Command(BaseCommand):
    help = (
        "Prepara datos de ejemplo para Indicadores: fechas de periodo en la revisión "
        "y cargas con estados/fechas escalonadas (Plan, Real, Forecast, pastel)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--empresa",
            type=int,
            action="append",
            dest="empresas",
            help="ID de empresa (repetible). Por defecto: todas con revisión activa y docs.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Elimina cargas [demo-indicadores] previas antes de cargar.",
        )
        parser.add_argument(
            "--inicio",
            type=str,
            default="",
            help="Fecha inicio YYYY-MM-DD (default: 1 ene del año de la revisión).",
        )
        parser.add_argument(
            "--fin",
            type=str,
            default="",
            help="Fecha fin YYYY-MM-DD (default: 31 dic del año de la revisión).",
        )

    def handle(self, *args, **options):
        empresa_ids = options.get("empresas")
        revisiones = (
            Revision.objects.filter(activo=True)
            .select_related("empresa")
            .order_by("empresa__nombre", "-anio", "-creado_en")
        )
        if empresa_ids:
            revisiones = revisiones.filter(empresa_id__in=empresa_ids)

        vistas: dict[int, Revision] = {}
        for rev in revisiones:
            if rev.empresa_id not in vistas:
                activa = revision_activa_empresa(rev.empresa_id)
                if activa:
                    vistas[rev.empresa_id] = activa

        if not vistas:
            self.stdout.write(self.style.WARNING("No hay revisiones activas."))
            return

        inicio_opt = date.fromisoformat(options["inicio"]) if options["inicio"] else None
        fin_opt = date.fromisoformat(options["fin"]) if options["fin"] else None

        with transaction.atomic():
            for revision in vistas.values():
                self._seed_revision(
                    revision,
                    reset=options["reset"],
                    inicio_opt=inicio_opt,
                    fin_opt=fin_opt,
                )

        self.stdout.write(self.style.SUCCESS("Datos de ejemplo para Indicadores listos."))

    def _seed_revision(
        self,
        revision: Revision,
        *,
        reset: bool,
        inicio_opt: date | None,
        fin_opt: date | None,
    ) -> None:
        anio = revision.anio or timezone.localdate().year
        inicio = inicio_opt or date(anio, 1, 1)
        fin = fin_opt or date(anio, 12, 31)
        if fin < inicio:
            fin = inicio

        revision.fecha_inicio = inicio
        revision.fecha_fin = fin
        revision.save(update_fields=["fecha_inicio", "fecha_fin"])

        if reset:
            deleted, _ = DocumentoCarga.objects.filter(
                empresa_documento__revision=revision,
                observaciones__startswith=_TAG,
            ).delete()
            self.stdout.write(
                f"  - {revision.empresa.nombre}: reset demo-indicadores ({deleted})."
            )

        docs = list(
            EmpresaDocumento.objects.filter(revision=revision, activo=True).order_by(
                "codigo"
            )
        )
        # Prefer catálogo con criterios (evita inflar temas con docs propios demo)
        docs_con = [
            d
            for d in docs
            if d.origen == OrigenEmpresaDocumento.CATALOGO and d.vinculos_criterio.exists()
        ]
        docs_sin = [
            d
            for d in docs
            if d.origen == OrigenEmpresaDocumento.CATALOGO and d not in docs_con
        ]
        pool = docs_con or [
            d for d in docs if d.vinculos_criterio.exists()
        ] or docs
        if not pool:
            self.stdout.write(f"  - {revision.empresa.nombre}: sin documentos.")
            return

        uploader = (
            User.objects.filter(
                perfil__tipo=TipoUsuario.CLIENTE_CARGA,
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
        revisor = (
            User.objects.filter(perfil__tipo=TipoUsuario.GESTOR, perfil__activo=True)
            .order_by("id")
            .first()
        ) or uploader

        # Clear active non-demo cargas that would block our demo mix on target docs
        # Only touch docs we will assign; leave other real cargas alone.
        n = len(pool)
        # Mix aimed at visible pastel + curve:
        # ~45% aprobado (staggered), ~15% pendiente, ~10% rechazado, rest sin carga (iniciado)
        n_aprob = max(3, round(n * 0.45))
        n_pend = max(1, round(n * 0.15))
        n_rech = max(1, round(n * 0.10))
        # Leave room for "iniciado" and keep some sin criterios as no asignado
        n_aprob = min(n_aprob, max(0, n - n_pend - n_rech - max(0, len(docs_sin[:2]))))

        targets_aprob = pool[:n_aprob]
        rest = pool[n_aprob:]
        targets_pend = rest[:n_pend]
        rest = rest[n_pend:]
        targets_rech = rest[:n_rech]

        tz = ZoneInfo(str(timezone.get_current_timezone()))
        hoy = timezone.localdate()
        # Spread approvals from inicio toward hoy (or mid-period if future)
        end_real = min(hoy, fin)
        if end_real < inicio:
            end_real = inicio + timedelta(days=30)

        creadas = {"APROBADO": 0, "PENDIENTE": 0, "RECHAZADO": 0}

        def _ensure_slot(doc: EmpresaDocumento) -> None:
            DocumentoCarga.objects.filter(
                empresa_documento=doc, activo=True
            ).exclude(observaciones__startswith=_TAG).update(activo=False)

        for i, doc in enumerate(targets_aprob):
            _ensure_slot(doc)
            DocumentoCarga.objects.filter(
                empresa_documento=doc, observaciones__startswith=_TAG, activo=True
            ).delete()
            frac = (i + 1) / max(len(targets_aprob), 1)
            day_offset = int((end_real - inicio).days * frac * 0.95)
            d = inicio + timedelta(days=day_offset)
            if d > end_real:
                d = end_real
            when = timezone.make_aware(datetime.combine(d, time(10, 0)), tz)
            nombre = f"{doc.codigo}_ind_demo.pdf"
            carga = DocumentoCarga(
                empresa_documento=doc,
                observaciones=f"{_TAG} Aprobado demo para curva Plan/Real/Forecast.",
                subido_por=uploader,
                revisado_por=revisor,
                activo=True,
                estado_revision=EstadoRevisionCarga.APROBADO,
                nombre_original=nombre,
                revisado_en=when,
            )
            carga.archivo.save(nombre, ContentFile(_MINI_PDF), save=False)
            carga.save()
            # Force creado_en near approval for timeline consistency
            DocumentoCarga.objects.filter(pk=carga.pk).update(
                creado_en=when - timedelta(days=2),
                revisado_en=when,
            )
            creadas["APROBADO"] += 1

        for doc in targets_pend:
            _ensure_slot(doc)
            DocumentoCarga.objects.filter(
                empresa_documento=doc, observaciones__startswith=_TAG, activo=True
            ).delete()
            nombre = f"{doc.codigo}_ind_pend.pdf"
            carga = DocumentoCarga(
                empresa_documento=doc,
                observaciones=f"{_TAG} Pendiente de revisión (Rev0).",
                subido_por=uploader,
                activo=True,
                estado_revision=EstadoRevisionCarga.PENDIENTE,
                nombre_original=nombre,
            )
            carga.archivo.save(nombre, ContentFile(_MINI_PDF), save=False)
            carga.save()
            creadas["PENDIENTE"] += 1

        for doc in targets_rech:
            _ensure_slot(doc)
            DocumentoCarga.objects.filter(
                empresa_documento=doc, observaciones__startswith=_TAG, activo=True
            ).delete()
            when = timezone.make_aware(
                datetime.combine(end_real - timedelta(days=7), time(15, 0)), tz
            )
            nombre = f"{doc.codigo}_ind_rech.pdf"
            carga = DocumentoCarga(
                empresa_documento=doc,
                observaciones=f"{_TAG} Rechazado demo (RevA).",
                subido_por=uploader,
                revisado_por=revisor,
                activo=True,
                estado_revision=EstadoRevisionCarga.RECHAZADO,
                nombre_original=nombre,
                revisado_en=when,
            )
            carga.archivo.save(nombre, ContentFile(_MINI_PDF), save=False)
            carga.save()
            DocumentoCarga.objects.filter(pk=carga.pk).update(revisado_en=when)
            creadas["RECHAZADO"] += 1

        prog = calcular_progreso_revision(revision)
        self.stdout.write(
            f"  - {revision.empresa.nombre} | {revision.etiqueta}: "
            f"periodo {inicio.isoformat()} .. {fin.isoformat()} | "
            f"AP={creadas['APROBADO']} PE={creadas['PENDIENTE']} RE={creadas['RECHAZADO']} | "
            f"docs {prog.docs_cargados}/{prog.docs_total} ({prog.docs_pct}%)"
        )
