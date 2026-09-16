from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import (
    DocumentoCarga,
    EmpresaDocumento,
    EmpresaDocumentoCriterio,
    Revision,
    RevisionCriterioDesactivado,
)


@dataclass
class EventoMonitoreo:
    fecha: datetime
    tipo: str
    titulo: str
    detalle: str
    actor: str
    revision_label: str
    archivo_url: str | None = None


def _actor_label(user) -> str:
    if not user:
        return "Sistema"
    nombre = (user.get_full_name() or "").strip()
    return nombre or user.email or user.username or "Usuario"


def eventos_monitoreo_empresa(empresa_id: int, *, limite: int = 100) -> list[EventoMonitoreo]:
    """Línea de tiempo de cambios de avance/evidencias de una empresa."""
    revision_ids = list(
        Revision.objects.filter(empresa_id=empresa_id).values_list("pk", flat=True)
    )
    if not revision_ids:
        return []

    eventos: list[EventoMonitoreo] = []

    cargas = (
        DocumentoCarga.objects.filter(empresa_documento__revision_id__in=revision_ids)
        .select_related(
            "subido_por",
            "empresa_documento",
            "empresa_documento__revision",
        )
        .order_by("-creado_en")[:limite]
    )
    for carga in cargas:
        doc = carga.empresa_documento
        rev = doc.revision
        nombre_archivo = carga.nombre_original or (
            carga.archivo.name.rsplit("/", 1)[-1] if carga.archivo else "archivo"
        )
        reemplazo = "" if carga.activo else " (reemplazada)"
        eventos.append(
            EventoMonitoreo(
                fecha=carga.creado_en,
                tipo="carga",
                titulo=f"Evidencia subida · {doc.codigo}",
                detalle=(
                    f"Se cargó «{nombre_archivo}» para el documento {doc.nombre}."
                    f"{reemplazo}"
                    + (f" Obs.: {carga.observaciones}" if carga.observaciones else "")
                ),
                actor=_actor_label(carga.subido_por),
                revision_label=rev.etiqueta if rev else "—",
                archivo_url=carga.archivo.url if carga.archivo else None,
            )
        )

    vinculos = (
        EmpresaDocumentoCriterio.objects.filter(
            empresa_documento__revision_id__in=revision_ids
        )
        .select_related(
            "criterio",
            "area",
            "empresa_documento",
            "empresa_documento__revision",
        )
        .order_by("-creado_en")[:limite]
    )
    for vinculo in vinculos:
        doc = vinculo.empresa_documento
        rev = doc.revision
        eventos.append(
            EventoMonitoreo(
                fecha=vinculo.creado_en,
                tipo="vinculo",
                titulo=f"Criterio asociado · {vinculo.criterio.codigo}",
                detalle=(
                    f"El criterio {vinculo.criterio.codigo} quedó vinculado al documento "
                    f"{doc.codigo} ({doc.nombre}), área {vinculo.area.nombre}."
                ),
                actor="Consultor / configuración",
                revision_label=rev.etiqueta if rev else "—",
            )
        )

    docs_propios = (
        EmpresaDocumento.objects.filter(
            revision_id__in=revision_ids,
            origen="PROPIO",
        )
        .select_related("revision")
        .order_by("-creado_en")[:limite]
    )
    for doc in docs_propios:
        eventos.append(
            EventoMonitoreo(
                fecha=doc.creado_en,
                tipo="documento",
                titulo=f"Documento del cliente · {doc.codigo}",
                detalle=f"Se registró el documento propio «{doc.nombre}» en la revisión.",
                actor="Cliente / sistema",
                revision_label=doc.revision.etiqueta,
            )
        )

    desactivaciones = (
        RevisionCriterioDesactivado.objects.filter(revision_id__in=revision_ids)
        .select_related("criterio", "revision")
        .order_by("-creado_en")[:limite]
    )
    for des in desactivaciones:
        eventos.append(
            EventoMonitoreo(
                fecha=des.creado_en,
                tipo="desactivacion",
                titulo=f"Criterio no aplicable · {des.criterio.codigo}",
                detalle=(
                    f"Se marcó {des.criterio.codigo} como no aplicable. "
                    f"Motivo: {des.motivo[:180]}"
                ),
                actor="Consultor / configuración",
                revision_label=des.revision.etiqueta,
            )
        )

    eventos.sort(key=lambda e: e.fecha, reverse=True)
    return eventos[:limite]
