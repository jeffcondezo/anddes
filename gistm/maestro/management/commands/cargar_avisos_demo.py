from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from maestro.models import (
    AvisoConsultor,
    Empresa,
    EmpresaDocumento,
    PerfilUsuario,
    TipoAvisoConsultor,
    TipoUsuario,
)
from maestro.progress import revision_activa_empresa

User = get_user_model()


class Command(BaseCommand):
    help = "Crea avisos de demostración para gestores y administradores."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Elimina avisos cuyo título empieza con [Demo] antes de crear.",
        )
        parser.add_argument(
            "--empresa",
            type=int,
            action="append",
            dest="empresas",
            help="ID de empresa (repetible). Por defecto: primeras empresas activas.",
        )

    def handle(self, *args, **options):
        destinatarios = list(
            User.objects.filter(
                is_active=True,
                perfil__activo=True,
                perfil__tipo__in=[TipoUsuario.ADMINISTRADOR, TipoUsuario.GESTOR],
            )
            .select_related("perfil")
            .distinct()
        )
        if not destinatarios:
            self.stdout.write(self.style.WARNING("No hay gestores/administradores activos."))
            return

        empresa_ids = options.get("empresas")
        empresas_qs = Empresa.objects.filter(activo=True).order_by("nombre")
        if empresa_ids:
            empresas_qs = empresas_qs.filter(pk__in=empresa_ids)
        empresas = list(empresas_qs[:4])
        if not empresas:
            self.stdout.write(self.style.WARNING("No hay empresas activas para demo."))
            return

        with transaction.atomic():
            if options["reset"]:
                borrados, _ = AvisoConsultor.objects.filter(titulo__startswith="[Demo]").delete()
                self.stdout.write(f"Avisos demo previos eliminados: {borrados}")

            creados = 0
            now = timezone.now()
            for user in destinatarios:
                perfil = user.perfil
                visibles = empresas
                if perfil.tipo == TipoUsuario.GESTOR:
                    visibles = [
                        e
                        for e in empresas
                        if perfil.empresas.filter(pk=e.pk).exists()
                    ]
                    if not visibles:
                        # Si el gestor no tiene empresas, usar todas las demo para que vea algo.
                        visibles = empresas[:1]

                for idx, empresa in enumerate(visibles[:3]):
                    revision = revision_activa_empresa(empresa.pk)
                    doc = None
                    if revision:
                        doc = (
                            EmpresaDocumento.objects.filter(revision=revision, activo=True)
                            .order_by("codigo")
                            .first()
                        )

                    muestras = self._muestras(empresa, doc, now, idx)
                    for offset, tipo, titulo, detalle, url, leido in muestras:
                        aviso = AvisoConsultor.objects.create(
                            destinatario=user,
                            empresa=empresa,
                            tipo=tipo,
                            titulo=titulo,
                            detalle=detalle,
                            url=url,
                            empresa_documento=doc,
                            leido=leido,
                            leido_en=now - timedelta(hours=1) if leido else None,
                        )
                        # Ajustar fecha para que no queden todas idénticas.
                        AvisoConsultor.objects.filter(pk=aviso.pk).update(
                            creado_en=now - timedelta(hours=offset)
                        )
                        creados += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Se crearon {creados} avisos demo para {len(destinatarios)} consultor(es)."
            )
        )

    def _muestras(self, empresa, doc, now, idx: int):
        codigo = doc.codigo if doc else "DOC-DEMO"
        nombre = doc.nombre if doc else "Documento de demostración"
        url_revision = reverse("maestro:seguimiento_empresa_revision", args=[empresa.pk])
        url_conv = (
            reverse("maestro:documento_conversacion", args=[doc.pk]) if doc else url_revision
        )
        url_asociar = (
            reverse(
                "maestro:seguimiento_documento_asociar_criterios",
                args=[empresa.pk, doc.pk],
            )
            if doc
            else url_revision
        )
        base = idx * 3
        return [
            (
                base + 1,
                TipoAvisoConsultor.CARGA,
                f"[Demo] Nueva evidencia · {codigo}",
                f"Usuario demo cargó «evidencia_{codigo.lower()}.pdf» para {nombre}.",
                url_revision,
                False,
            ),
            (
                base + 5,
                TipoAvisoConsultor.MENSAJE,
                f"[Demo] Mensaje del cliente · {codigo}",
                "Cliente demo: Ya subimos la versión corregida. ¿Pueden revisarla esta semana?",
                url_conv,
                False,
            ),
            (
                base + 12,
                TipoAvisoConsultor.DOCUMENTO_PROPIO,
                f"[Demo] Documento del cliente · {codigo}",
                f"Cliente demo registró «{nombre}» con evidencia. Pendiente asociar criterios.",
                url_asociar,
                True,
            ),
        ]
