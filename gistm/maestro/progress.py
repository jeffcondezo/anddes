from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, Prefetch, Q

from .models import (
    DocumentoCarga,
    EmpresaDocumento,
    EmpresaDocumentoCriterio,
    EmpresaDocumentoPregunta,
    EstadoRevisionCarga,
    EstadoRevisionPregunta,
    OrigenEmpresaDocumento,
    Revision,
    RevisionCatalogoCriterio,
    RevisionCriterioDesactivado,
    asegurar_snapshot_catalogo_revision,
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
        return self.pct >= 100.0


@dataclass
class _SnapCodigo:
    codigo: str
    descripcion: str = ""
    nombre: str = ""


@dataclass
class CriterioSnapView:
    """Criterio visto desde el snapshot de la revisión (histórico congelado)."""

    id: int
    codigo: str
    descripcion: str
    tema: _SnapCodigo
    principio: _SnapCodigo
    requisito: _SnapCodigo
    ponderacion: Decimal = Decimal("1")

    @property
    def pk(self) -> int:
        return self.id


def _criterios_desde_snapshot(
    revision: Revision, desactivados_ids: set[int]
) -> list[CriterioSnapView]:
    asegurar_snapshot_catalogo_revision(revision)
    snaps = (
        RevisionCatalogoCriterio.objects.filter(revision=revision)
        .exclude(criterio_id__in=desactivados_ids)
        .order_by("tema_codigo", "principio_codigo", "requisito_codigo", "codigo")
    )
    return [
        CriterioSnapView(
            id=s.criterio_id,
            codigo=s.codigo,
            descripcion=s.descripcion or "",
            tema=_SnapCodigo(codigo=s.tema_codigo, nombre=s.tema_nombre or ""),
            principio=_SnapCodigo(
                codigo=s.principio_codigo, descripcion=s.principio_descripcion or ""
            ),
            requisito=_SnapCodigo(
                codigo=s.requisito_codigo, descripcion=s.requisito_descripcion or ""
            ),
            ponderacion=s.ponderacion or Decimal("1"),
        )
        for s in snaps
    ]


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
    revision_texto: str = ""
    revision_porcentaje: int | None = None
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


def _avg_pct(valores: list[float]) -> float:
    """Promedio de porcentajes parciales (0–100)."""
    if not valores:
        return 0.0
    return round(sum(valores) / len(valores), 1)


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

    _vinculo_qs = (
        EmpresaDocumentoCriterio.objects.select_related(
            "criterio", "criterio__tema", "area", "empresa_documento_secundario"
        )
        .prefetch_related(
            Prefetch(
                "preguntas",
                queryset=EmpresaDocumentoPregunta.objects.select_related(
                    "pregunta"
                ).order_by("pregunta__orden", "pregunta__codigo"),
            )
        )
        .order_by("criterio__codigo")
    )
    documentos_qs = (
        EmpresaDocumento.objects.filter(revision=revision, activo=True)
        .annotate(
            criterios_principales=Count("vinculos_criterio", distinct=True),
            criterios_secundarios=Count("vinculos_criterio_secundarios", distinct=True),
        )
        .prefetch_related(
            Prefetch(
                "cargas",
                queryset=DocumentoCarga.objects.filter(activo=True).order_by("-creado_en"),
                to_attr="cargas_vigentes",
            ),
            Prefetch(
                "vinculos_criterio",
                queryset=_vinculo_qs,
                to_attr="vinculos_list",
            ),
            Prefetch(
                "vinculos_criterio_secundarios",
                queryset=_vinculo_qs,
                to_attr="vinculos_secundarios_list",
            ),
        )
        .order_by("codigo")
    )
    documentos_all: list[DocumentoProgreso] = []
    docs_completos_ids: set[int] = set()
    for doc in documentos_qs:
        carga = doc.cargas_vigentes[0] if getattr(doc, "cargas_vigentes", None) else None
        estado = carga.estado_revision if carga else ""
        vinculos_list = list(getattr(doc, "vinculos_list", [])) + list(
            getattr(doc, "vinculos_secundarios_list", [])
        )
        crit_count = int(doc.criterios_principales or 0) + int(
            doc.criterios_secundarios or 0
        )

        def _criterio_completo_por_preguntas(vinculo: EmpresaDocumentoCriterio) -> bool:
            pregs = list(getattr(vinculo, "preguntas").all())
            if not pregs:
                return False
            return all(
                p.estado_revision == EstadoRevisionPregunta.CUMPLE for p in pregs
            )

        crit_aprobados = sum(
            1 for v in vinculos_list if _criterio_completo_por_preguntas(v)
        )
        # Documento completo = todos sus criterios al 100% de preguntas.
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
            revision_texto=(doc.revision_texto or "").strip(),
            revision_porcentaje=doc.revision_porcentaje,
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

    # Criterio → vínculo (estado derivado de preguntas).
    # Cuenta si el documento principal o el secundario está activo.
    vinculos_por_criterio = {
        edc.criterio_id: edc
        for edc in EmpresaDocumentoCriterio.objects.filter(
            revision=revision,
        )
        .filter(
            Q(empresa_documento__activo=True)
            | Q(empresa_documento_secundario__activo=True)
        )
        .select_related("empresa_documento", "empresa_documento_secundario")
    }

    preguntas_por_criterio: dict[int, list[EmpresaDocumentoPregunta]] = {}
    for resp in EmpresaDocumentoPregunta.objects.filter(
        revision=revision,
        vinculo_criterio__empresa_documento__activo=True,
    ).select_related("pregunta", "vinculo_criterio"):
        preguntas_por_criterio.setdefault(
            resp.vinculo_criterio.criterio_id, []
        ).append(resp)

    criterios_aplicables = _criterios_desde_snapshot(revision, desactivados_ids)

    criterio_completo: dict[int, bool] = {}
    criterio_preguntas_pct: dict[int, tuple[int, int, float]] = {}
    criterios_huerfanos: list[CriterioSnapView] = []
    for criterio in criterios_aplicables:
        vinculo = vinculos_por_criterio.get(criterio.pk)
        if not vinculo:
            criterios_huerfanos.append(criterio)
            criterio_completo[criterio.pk] = False
            criterio_preguntas_pct[criterio.pk] = (0, 0, 0.0)
            continue
        pregs = preguntas_por_criterio.get(criterio.pk, [])
        total_p = len(pregs)
        if total_p == 0:
            # Sin preguntas → no completo (regla 2A)
            criterio_completo[criterio.pk] = False
            criterio_preguntas_pct[criterio.pk] = (0, 0, 0.0)
            continue
        ok_p = sum(
            1 for p in pregs if p.estado_revision == EstadoRevisionPregunta.CUMPLE
        )
        pct_p = round(100.0 * ok_p / total_p, 1)
        criterio_preguntas_pct[criterio.pk] = (ok_p, total_p, pct_p)
        criterio_completo[criterio.pk] = ok_p == total_p
    huerfanos = len(criterios_huerfanos)

    criterios_total = len(criterios_aplicables)
    criterios_completos = sum(1 for ok in criterio_completo.values() if ok)
    # Avance parcial: promedio del % de preguntas de cada criterio.
    criterios_pct = _avg_pct(
        [criterio_preguntas_pct[c.pk][2] for c in criterios_aplicables]
    )

    # Agrupar por códigos congelados del snapshot: requisito → principio → tema
    por_requisito: dict[str, list[CriterioSnapView]] = {}
    for c in criterios_aplicables:
        por_requisito.setdefault(c.requisito.codigo, []).append(c)

    por_principio: dict[str, set[str]] = {}
    principio_meta: dict[str, _SnapCodigo] = {}
    for c in criterios_aplicables:
        por_principio.setdefault(c.principio.codigo, set()).add(c.requisito.codigo)
        principio_meta[c.principio.codigo] = c.principio

    por_tema: dict[str, set[str]] = {}
    tema_meta: dict[str, _SnapCodigo] = {}
    for c in criterios_aplicables:
        por_tema.setdefault(c.tema.codigo, set()).add(c.principio.codigo)
        tema_meta[c.tema.codigo] = c.tema

    # Árbol: el % de cada nivel es el promedio de los % de sus hijos
    # (el avance parcial de preguntas sube a criterio → requisito → principio → tema).
    temas_nivel: list[NivelProgreso] = []
    for tema_codigo in sorted(por_tema.keys()):
        tema = tema_meta[tema_codigo]
        principios_nivel: list[NivelProgreso] = []
        for prin_codigo in sorted(por_tema[tema_codigo]):
            principio = principio_meta[prin_codigo]
            requisitos_nivel: list[NivelProgreso] = []
            for req_codigo in sorted(por_principio[prin_codigo]):
                crits = por_requisito[req_codigo]
                requisito = crits[0].requisito
                hijos_crit = [
                    NivelProgreso(
                        codigo=c.codigo,
                        nombre=(c.descripcion or "")[:120],
                        total=criterio_preguntas_pct.get(c.pk, (0, 0, 0.0))[1] or 1,
                        completos=criterio_preguntas_pct.get(c.pk, (0, 0, 0.0))[0],
                        pct=criterio_preguntas_pct.get(c.pk, (0, 0, 0.0))[2],
                    )
                    for c in crits
                ]
                crits_ok = sum(1 for n in hijos_crit if n.completo)
                requisitos_nivel.append(
                    NivelProgreso(
                        codigo=requisito.codigo,
                        nombre=(requisito.descripcion or "")[:120],
                        total=len(hijos_crit),
                        completos=crits_ok,
                        pct=_avg_pct([n.pct for n in hijos_crit]),
                        children=hijos_crit,
                    )
                )
            reqs_ok = sum(1 for n in requisitos_nivel if n.completo)
            principios_nivel.append(
                NivelProgreso(
                    codigo=principio.codigo,
                    nombre=(principio.descripcion or "")[:120],
                    total=len(requisitos_nivel),
                    completos=reqs_ok,
                    pct=_avg_pct([n.pct for n in requisitos_nivel]),
                    children=requisitos_nivel,
                )
            )
        crits_tema = [
            c
            for prin in principios_nivel
            for req in prin.children
            for c in req.children
        ]
        temas_nivel.append(
            NivelProgreso(
                codigo=tema.codigo,
                nombre=tema.nombre or tema.codigo,
                total=len(crits_tema),
                completos=sum(1 for c in crits_tema if c.completo),
                pct=_avg_pct([n.pct for n in principios_nivel]),
                children=principios_nivel,
            )
        )

    requisitos_todos = [
        req for tema in temas_nivel for prin in tema.children for req in prin.children
    ]
    principios_todos = [prin for tema in temas_nivel for prin in tema.children]

    requisitos_total = len(requisitos_todos)
    requisitos_completos = sum(1 for n in requisitos_todos if n.completo)
    requisitos_pct = _avg_pct([n.pct for n in requisitos_todos])

    principios_total = len(principios_todos)
    principios_completos = sum(1 for n in principios_todos if n.completo)
    principios_pct = _avg_pct([n.pct for n in principios_todos])

    temas_total = len(temas_nivel)
    temas_completos = sum(1 for n in temas_nivel if n.completo)
    temas_pct = _avg_pct([n.pct for n in temas_nivel])

    return ProgresoRevision(
        revision=revision,
        docs_total=docs_total,
        docs_cargados=docs_cargados,
        docs_pct=_pct(docs_cargados, docs_total),
        criterios_total=criterios_total,
        criterios_completos=criterios_completos,
        criterios_pct=criterios_pct,
        requisitos_total=requisitos_total,
        requisitos_completos=requisitos_completos,
        requisitos_pct=requisitos_pct,
        principios_total=principios_total,
        principios_completos=principios_completos,
        principios_pct=principios_pct,
        temas_total=temas_total,
        temas_completos=temas_completos,
        temas_pct=temas_pct,
        criterios_huerfanos_count=huerfanos,
        criterios_huerfanos=criterios_huerfanos,
        documentos=documentos,
        documentos_propios_todos=documentos_propios_todos,
        documentos_sin_criterios_config=documentos_sin_criterios_config,
        documentos_all_activos=documentos_all,
        temas=temas_nivel,
    )
