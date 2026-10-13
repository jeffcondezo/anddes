from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Count, Prefetch

from .models import (
    Criterio,
    DocumentoCarga,
    EmpresaDocumento,
    EmpresaDocumentoCriterio,
    EstadoRevisionCarga,
    EstadoRevisionCriterio,
    OrigenEmpresaDocumento,
    Principio,
    Revision,
    RevisionCriterioDesactivado,
    Tema,
)


@dataclass
class NivelProgreso:
    codigo: str
    nombre: str
    total: int
    completos: int
    pct: float
    children: list = field(default_factory=list)

    @property
    def completo(self) -> bool:
        return self.total > 0 and self.completos == self.total


@dataclass
class DocumentoProgreso:
    id: int
    codigo: str
    nombre: str
    origen: str
    cargado: bool  # todos los criterios del doc APROBADOS (cuenta en avance documentos)
    criterios_count: int = 0
    criterios_aprobados: int = 0
    carga: DocumentoCarga | None = None
    estado_revision: str = ""
    pendiente_revision: bool = False
    parcial: bool = False
    rechazado: bool = False
    vinculos: list = field(default_factory=list)

    @property
    def es_catalogo(self) -> bool:
        return self.origen == OrigenEmpresaDocumento.CATALOGO

    @property
    def es_propio(self) -> bool:
        return self.origen == OrigenEmpresaDocumento.PROPIO

    @property
    def sin_criterios(self) -> bool:
        return self.criterios_count == 0

    @property
    def origen_label(self) -> str:
        if self.es_catalogo:
            return "Precargado"
        return "Del cliente"

    @property
    def estado_label(self) -> str:
        if self.cargado:
            return "Aprobado"
        if self.parcial:
            return "Parcial"
        if self.pendiente_revision:
            return "Por revisar"
        if self.rechazado:
            return "Rechazado"
        return "Pendiente de carga"



@dataclass
class ProgresoRevision:
    revision: Revision
    docs_total: int
    docs_cargados: int
    docs_pct: float
    criterios_total: int
    criterios_completos: int
    criterios_pct: float
    requisitos_total: int
    requisitos_completos: int
    requisitos_pct: float
    principios_total: int
    principios_completos: int
    principios_pct: float
    temas_total: int
    temas_completos: int
    temas_pct: float
    criterios_huerfanos_count: int
    criterios_huerfanos: list = field(default_factory=list)
    documentos: list[DocumentoProgreso] = field(default_factory=list)
    documentos_propios_todos: list[DocumentoProgreso] = field(default_factory=list)
    documentos_sin_criterios_config: list[DocumentoProgreso] = field(default_factory=list)
    documentos_all_activos: list[DocumentoProgreso] = field(default_factory=list)
    temas: list[NivelProgreso] = field(default_factory=list)

    @property
    def documentos_cargados(self) -> list[DocumentoProgreso]:
        return [d for d in self.documentos if d.cargado]

    @property
    def documentos_pendientes(self) -> list[DocumentoProgreso]:
        return [d for d in self.documentos if not d.cargado]

    @property
    def docs_pendientes(self) -> int:
        return self.docs_total - self.docs_cargados

    @property
    def documentos_catalogo(self) -> list[DocumentoProgreso]:
        return [d for d in self.documentos if d.es_catalogo]

    @property
    def documentos_propios(self) -> list[DocumentoProgreso]:
        """Propios aplicables (ya con criterios)."""
        return [d for d in self.documentos if d.es_propio]

    @property
    def docs_catalogo(self) -> int:
        return len(self.documentos_catalogo)

    @property
    def docs_propios(self) -> int:
        return len(self.documentos_propios_todos)

    @property
    def docs_sin_criterios_config(self) -> int:
        return len(self.documentos_sin_criterios_config)

    @property
    def documentos_por_revisar(self) -> list[DocumentoProgreso]:
        return [d for d in self.documentos_all_activos if d.pendiente_revision]

    @property
    def docs_por_revisar(self) -> int:
        return len(self.documentos_por_revisar)

    @property
    def documentos_aprobados(self) -> list[DocumentoProgreso]:
        """Aplicables con evidencia aprobada (vista detalle limpia)."""
        return [d for d in self.documentos if d.cargado]

    @property
    def documentos_en_revision(self) -> list[DocumentoProgreso]:
        return [d for d in self.documentos if d.pendiente_revision]

    @property
    def documentos_rechazados(self) -> list[DocumentoProgreso]:
        return [d for d in self.documentos if d.rechazado]

    @property
    def documentos_por_subir(self) -> list[DocumentoProgreso]:
        """Sin evidencia vigente (ni aprobada, ni en revisión, ni rechazada)."""
        return [
            d
            for d in self.documentos
            if not d.cargado and not d.pendiente_revision and not d.rechazado
        ]

    @property
    def docs_en_revision(self) -> int:
        return len(self.documentos_en_revision)

    @property
    def docs_rechazados(self) -> int:
        return len(self.documentos_rechazados)

    @property
    def docs_por_subir(self) -> int:
        return len(self.documentos_por_subir)

    @property
    def documentos_pendientes_entrega(self) -> list[DocumentoProgreso]:
        """Sin entregar (sin evidencia) + rechazados. Para backlog de revisión."""
        return self.documentos_por_subir + self.documentos_rechazados

    @property
    def docs_pendientes_entrega(self) -> int:
        return len(self.documentos_pendientes_entrega)


def _pct(parte: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * parte / total, 1)


def revisiones_activas_empresa(empresa_id: int):
    """Todas las revisiones activas de la empresa (pueden coexistir en paralelo)."""
    return Revision.objects.filter(empresa_id=empresa_id, activo=True).order_by(
        "-anio", "-creado_en"
    )


def revision_activa_empresa(empresa_id: int) -> Revision | None:
    """Primera revisión activa (año más reciente / más reciente creada)."""
    return revisiones_activas_empresa(empresa_id).first()


def revision_activa_por_id(
    empresa_id: int, rev_id: int | None = None
) -> Revision | None:
    """
    Resuelve una revisión activa concreta. Si rev_id no aplica o es None,
    usa la primera activa.
    """
    qs = revisiones_activas_empresa(empresa_id)
    if rev_id is not None:
        elegida = qs.filter(pk=rev_id).first()
        if elegida is not None:
            return elegida
    return qs.first()


def calcular_progreso_revision(revision: Revision) -> ProgresoRevision:
    desactivados_ids = set(
        RevisionCriterioDesactivado.objects.filter(revision=revision).values_list(
            "criterio_id", flat=True
        )
    )

    documentos_qs = (
        EmpresaDocumento.objects.filter(revision=revision, activo=True)
        .annotate(
            criterios_count=Count("vinculos_criterio", distinct=True),
        )
        .prefetch_related(
            Prefetch(
                "cargas",
                queryset=DocumentoCarga.objects.filter(activo=True).order_by("-creado_en"),
                to_attr="cargas_vigentes",
            ),
            Prefetch(
                "vinculos_criterio",
                queryset=EmpresaDocumentoCriterio.objects.select_related(
                    "criterio", "criterio__tema", "area"
                ).order_by("criterio__codigo"),
                to_attr="vinculos_list",
            ),
        )
        .order_by("codigo")
    )
    documentos_all: list[DocumentoProgreso] = []
    docs_completos_ids: set[int] = set()
    for doc in documentos_qs:
        carga = doc.cargas_vigentes[0] if getattr(doc, "cargas_vigentes", None) else None
        estado = carga.estado_revision if carga else ""
        vinculos_list = list(getattr(doc, "vinculos_list", []))
        crit_count = int(doc.criterios_count or 0)
        crit_aprobados = sum(
            1
            for v in vinculos_list
            if v.estado_revision == EstadoRevisionCriterio.APROBADO
        )
        # Documento completo = todos sus criterios aprobados (o carga APROBADO sin vínculos).
        if crit_count > 0:
            aprobado = crit_aprobados == crit_count
        else:
            aprobado = bool(carga and carga.estado_revision == EstadoRevisionCarga.APROBADO)
        parcial = bool(
            carga
            and (
                carga.estado_revision == EstadoRevisionCarga.PARCIAL
                or (crit_count > 0 and 0 < crit_aprobados < crit_count)
            )
        )
        pendiente = bool(
            carga
            and carga.estado_revision
            in (EstadoRevisionCarga.PENDIENTE, EstadoRevisionCarga.PARCIAL)
        )
        rechazado = bool(carga and carga.estado_revision == EstadoRevisionCarga.RECHAZADO)
        item = DocumentoProgreso(
            id=doc.pk,
            codigo=doc.codigo,
            nombre=doc.nombre,
            origen=doc.origen,
            cargado=aprobado,
            criterios_count=crit_count,
            criterios_aprobados=crit_aprobados,
            carga=carga,
            estado_revision=estado,
            pendiente_revision=pendiente,
            parcial=parcial,
            rechazado=rechazado,
            vinculos=vinculos_list,
        )
        documentos_all.append(item)
        if aprobado and crit_count > 0:
            docs_completos_ids.add(doc.pk)

    documentos = [d for d in documentos_all if d.criterios_count > 0]
    documentos_sin_criterios_config = [d for d in documentos_all if d.sin_criterios]
    documentos_propios_todos = [d for d in documentos_all if d.es_propio]

    docs_total = len(documentos)
    docs_cargados = len(docs_completos_ids)

    # Criterio → vínculo (estado de aprobación parcial)
    vinculos_por_criterio = {
        edc.criterio_id: edc
        for edc in EmpresaDocumentoCriterio.objects.filter(
            revision=revision,
            empresa_documento__activo=True,
        ).select_related("empresa_documento")
    }

    criterios_aplicables = list(
        Criterio.objects.exclude(pk__in=desactivados_ids)
        .select_related("requisito", "principio", "tema")
        .order_by("tema__codigo", "principio__codigo", "requisito__codigo", "codigo")
    )

    criterio_completo: dict[int, bool] = {}
    criterios_huerfanos: list[Criterio] = []
    for criterio in criterios_aplicables:
        vinculo = vinculos_por_criterio.get(criterio.pk)
        if not vinculo:
            criterios_huerfanos.append(criterio)
            criterio_completo[criterio.pk] = False
            continue
        criterio_completo[criterio.pk] = (
            vinculo.estado_revision == EstadoRevisionCriterio.APROBADO
        )
    huerfanos = len(criterios_huerfanos)

    criterios_total = len(criterios_aplicables)
    criterios_completos = sum(1 for ok in criterio_completo.values() if ok)

    # Agrupar: requisito → principio → tema
    por_requisito: dict[int, list[Criterio]] = {}
    for c in criterios_aplicables:
        por_requisito.setdefault(c.requisito_id, []).append(c)

    requisito_ok: dict[int, bool] = {}
    for req_id, crits in por_requisito.items():
        requisito_ok[req_id] = bool(crits) and all(
            criterio_completo.get(c.pk, False) for c in crits
        )

    requisitos_total = len(por_requisito)
    requisitos_completos = sum(1 for ok in requisito_ok.values() if ok)

    por_principio: dict[int, set[int]] = {}
    principio_meta: dict[int, Principio] = {}
    for c in criterios_aplicables:
        por_principio.setdefault(c.principio_id, set()).add(c.requisito_id)
        principio_meta[c.principio_id] = c.principio

    principio_ok: dict[int, bool] = {}
    for prin_id, req_ids in por_principio.items():
        principio_ok[prin_id] = bool(req_ids) and all(
            requisito_ok.get(rid, False) for rid in req_ids
        )

    principios_total = len(por_principio)
    principios_completos = sum(1 for ok in principio_ok.values() if ok)

    por_tema: dict[int, set[int]] = {}
    tema_meta: dict[int, Tema] = {}
    for c in criterios_aplicables:
        por_tema.setdefault(c.tema_id, set()).add(c.principio_id)
        tema_meta[c.tema_id] = c.tema

    tema_ok: dict[int, bool] = {}
    for tema_id, prin_ids in por_tema.items():
        tema_ok[tema_id] = bool(prin_ids) and all(
            principio_ok.get(pid, False) for pid in prin_ids
        )

    temas_total = len(por_tema)
    temas_completos = sum(1 for ok in tema_ok.values() if ok)

    # Árbol para UI
    temas_nivel: list[NivelProgreso] = []
    for tema_id in sorted(por_tema.keys(), key=lambda i: tema_meta[i].codigo):
        tema = tema_meta[tema_id]
        principios_nivel: list[NivelProgreso] = []
        for prin_id in sorted(por_tema[tema_id], key=lambda i: principio_meta[i].codigo):
            principio = principio_meta[prin_id]
            requisitos_nivel: list[NivelProgreso] = []
            for req_id in sorted(por_principio[prin_id]):
                crits = por_requisito[req_id]
                requisito = crits[0].requisito
                crits_ok = sum(1 for c in crits if criterio_completo.get(c.pk, False))
                requisitos_nivel.append(
                    NivelProgreso(
                        codigo=requisito.codigo,
                        nombre=(requisito.descripcion or "")[:120],
                        total=len(crits),
                        completos=crits_ok,
                        pct=_pct(crits_ok, len(crits)),
                        children=[
                            NivelProgreso(
                                codigo=c.codigo,
                                nombre=(c.descripcion or "")[:120],
                                total=1,
                                completos=1 if criterio_completo.get(c.pk) else 0,
                                pct=100.0 if criterio_completo.get(c.pk) else 0.0,
                            )
                            for c in crits
                        ],
                    )
                )
            reqs_ok = sum(1 for n in requisitos_nivel if n.completo)
            principios_nivel.append(
                NivelProgreso(
                    codigo=principio.codigo,
                    nombre=(principio.descripcion or "")[:120],
                    total=len(requisitos_nivel),
                    completos=reqs_ok,
                    pct=_pct(reqs_ok, len(requisitos_nivel)),
                    children=requisitos_nivel,
                )
            )
        # Cumplimiento del tema = criterios con evidencia aprobada / criterios del tema.
        # (Misma unidad que el dashboard "Cumplimiento por tema".)
        crits_total = 0
        crits_ok = 0
        for prin in principios_nivel:
            for req in prin.children:
                crits_total += req.total
                crits_ok += req.completos
        temas_nivel.append(
            NivelProgreso(
                codigo=tema.codigo,
                nombre=tema.nombre,
                total=crits_total,
                completos=crits_ok,
                pct=_pct(crits_ok, crits_total),
                children=principios_nivel,
            )
        )

    return ProgresoRevision(
        revision=revision,
        docs_total=docs_total,
        docs_cargados=docs_cargados,
        docs_pct=_pct(docs_cargados, docs_total),
        criterios_total=criterios_total,
        criterios_completos=criterios_completos,
        criterios_pct=_pct(criterios_completos, criterios_total),
        requisitos_total=requisitos_total,
        requisitos_completos=requisitos_completos,
        requisitos_pct=_pct(requisitos_completos, requisitos_total),
        principios_total=principios_total,
        principios_completos=principios_completos,
        principios_pct=_pct(principios_completos, principios_total),
        temas_total=temas_total,
        temas_completos=temas_completos,
        temas_pct=_pct(temas_completos, temas_total),
        criterios_huerfanos_count=huerfanos,
        criterios_huerfanos=criterios_huerfanos,
        documentos=documentos,
        documentos_propios_todos=documentos_propios_todos,
        documentos_sin_criterios_config=documentos_sin_criterios_config,
        documentos_all_activos=documentos_all,
        temas=temas_nivel,
    )
