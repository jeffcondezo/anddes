from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from calendar import monthrange

from django.db.models import Q
from django.utils import timezone

from ..models import (
    Criterio,
    Documento,
    DocumentoCarga,
    DocumentoCriterio,
    Empresa,
    EmpresaDocumento,
    EstadoRevisionCarga,
    OrigenEmpresaDocumento,
    PerfilUsuario,
    Principio,
    Requisito,
    Revision,
    Tema,
    TipoUsuario,
)
from ..progress import (
    ProgresoRevision,
    calcular_progreso_revision,
    nivel_cumplimiento_anddes,
    revisiones_activas_empresa,
)

TABS = ("general", "progreso", "documentos", "estandar", "glosario")
DOC_SUBTABS = ("todos", "referenciales", "multidisciplinario", "multiprincipio")


@dataclass
class PrincipioAvance:
    codigo: str
    nombre: str
    total: int
    completos: int
    pct: float
    tema_codigo: str = ""


@dataclass
class DocumentoIndicador:
    id: int
    codigo: str
    nombre: str
    origen: str
    origen_label: str
    estado_label: str
    estado_revision: str
    tipo_documento: str
    areas: list[str] = field(default_factory=list)
    principios: list[str] = field(default_factory=list)
    criterios: list[str] = field(default_factory=list)
    consultores: list[str] = field(default_factory=list)
    elaborador: str = ""  # consultor_responsable del documento (4.3)
    revisado_por: str = ""
    clasificacion: str = "referenciales"  # referenciales | multidisciplinario
    es_multiprincipio: bool = False  # criterios de 2+ principios distintos
    fecha_limite: object | None = None  # date | None
    fecha_carga: object | None = None
    fecha_revision: object | None = None


@dataclass
class CoberturaEstandar:
    docs_catalogo_total: int
    docs_catalogo_activos: int
    docs_catalogo_pct: float
    criterios_estandar_total: int
    criterios_vinculados: int
    criterios_cobertura_pct: float
    docs_sin_evidencia: int
    criterios_huerfanos: int
    filas_tema: list[dict] = field(default_factory=list)


@dataclass
class GlosarioItem:
    id: int
    concepto: str
    descripcion: str


@dataclass
class DocumentoPrioritario:
    id: int
    nombre: str
    codigo: str
    area: str
    estado_label: str
    temas: list[str] = field(default_factory=list)
    aprobado: bool = False


@dataclass
class DocumentoPorVencer:
    id: int
    nombre: str
    codigo: str
    area: str
    estado_label: str
    fecha_limite: object  # date
    dias: int  # negativos = vencido
    temas: list[str] = field(default_factory=list)
    aprobado: bool = False

    @property
    def dias_abs(self) -> int:
        return abs(self.dias)


def _temas_de_documento_progreso(doc) -> list[str]:
    temas: list[str] = []
    for v in getattr(doc, "vinculos", None) or []:
        tema = getattr(getattr(v, "criterio", None), "tema", None)
        codigo = getattr(tema, "codigo", None) if tema else None
        if codigo and codigo not in temas:
            temas.append(codigo)
    return temas


def _pct(parte: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * parte / total, 1)


def _actor_label(user) -> str:
    if not user:
        return ""
    nombre = (user.get_full_name() or "").strip()
    return nombre or user.email or user.username or ""


def consultores_empresa(empresa: Empresa) -> list[str]:
    perfiles = (
        PerfilUsuario.objects.filter(
            activo=True,
            tipo=TipoUsuario.GESTOR,
            empresas=empresa,
            user__is_active=True,
        )
        .select_related("user")
        .order_by("user__first_name", "user__email")
    )
    labels = [_actor_label(p.user) for p in perfiles if p.user]
    admins = (
        PerfilUsuario.objects.filter(
            activo=True,
            tipo=TipoUsuario.ADMINISTRADOR,
            user__is_active=True,
        )
        .select_related("user")
        .order_by("user__first_name", "user__email")[:5]
    )
    for p in admins:
        label = _actor_label(p.user)
        if label and label not in labels:
            labels.append(label)
    return [x for x in labels if x]


def avance_por_principios(progreso: ProgresoRevision) -> list[PrincipioAvance]:
    items: list[PrincipioAvance] = []
    for tema in progreso.temas:
        for principio in tema.children:
            items.append(
                PrincipioAvance(
                    codigo=principio.codigo,
                    nombre=principio.nombre,
                    total=principio.total,
                    completos=principio.completos,
                    pct=principio.pct,
                    tema_codigo=tema.codigo,
                )
            )
    items.sort(key=lambda x: x.codigo)
    return items


def documentos_indicadores(
    revision: Revision,
    progreso: ProgresoRevision,
    *,
    empresa: Empresa,
) -> list[DocumentoIndicador]:
    from django.db.models import Prefetch

    from ..models import DocumentoCarga, EmpresaDocumentoCriterio, EstadoRevisionCarga

    progreso_by_id = {d.id: d for d in progreso.documentos_all_activos}
    docs_qs = (
        EmpresaDocumento.objects.filter(revision=revision, activo=True)
        .select_related("consultor_responsable")
        .prefetch_related(
            Prefetch(
                "vinculos_criterio",
                queryset=EmpresaDocumentoCriterio.objects.select_related(
                    "area", "criterio", "criterio__principio"
                ).order_by("criterio__codigo"),
            ),
            Prefetch(
                "cargas",
                queryset=DocumentoCarga.objects.filter(activo=True)
                .select_related("revisado_por")
                .order_by("-creado_en"),
                to_attr="cargas_vigentes",
            ),
        )
        .order_by("codigo")
    )
    resultado: list[DocumentoIndicador] = []
    for meta in docs_qs:
        prog = progreso_by_id.get(meta.pk)
        areas: list[str] = []
        principios: list[str] = []
        criterios: list[str] = []
        for vinculo in meta.vinculos_criterio.all():
            if vinculo.area_id and vinculo.area.nombre not in areas:
                areas.append(vinculo.area.nombre)
            crit = vinculo.criterio
            if crit:
                criterios.append(crit.codigo)
                prin_code = crit.principio.codigo if crit.principio_id else ""
                if prin_code and prin_code not in principios:
                    principios.append(prin_code)
        carga = meta.cargas_vigentes[0] if getattr(meta, "cargas_vigentes", None) else None
        revisado = _actor_label(carga.revisado_por) if carga else ""
        fecha_carga = None
        fecha_revision = None
        if carga and carga.creado_en:
            dt = carga.creado_en
            if timezone.is_aware(dt):
                dt = timezone.localtime(dt)
            fecha_carga = dt.date()
        if carga and carga.revisado_en:
            dt = carga.revisado_en
            if timezone.is_aware(dt):
                dt = timezone.localtime(dt)
            fecha_revision = dt.date()
        elaborador = _actor_label(meta.consultor_responsable)
        if not elaborador and prog is not None:
            elaborador = (getattr(prog, "consultor_nombre", None) or "").strip()
        clasificacion = (
            "referenciales"
            if meta.origen == OrigenEmpresaDocumento.CATALOGO
            else "multidisciplinario"
        )
        es_multiprincipio = len(principios) > 1
        if prog:
            estado_label = prog.estado_label
            estado_revision = prog.estado_revision or ""
            origen_label = prog.origen_label
        else:
            estado_revision = carga.estado_revision if carga else ""
            if not carga:
                estado_label = "Pendiente de carga"
            elif estado_revision == EstadoRevisionCarga.APROBADO:
                estado_label = "Aprobado"
            elif estado_revision == EstadoRevisionCarga.PENDIENTE:
                estado_label = "Por revisar"
            elif estado_revision == EstadoRevisionCarga.RECHAZADO:
                estado_label = "Rechazado"
            else:
                estado_label = estado_revision or "—"
            origen_label = (
                "Precargado"
                if meta.origen == OrigenEmpresaDocumento.CATALOGO
                else "Del cliente"
            )
        resultado.append(
            DocumentoIndicador(
                id=meta.pk,
                codigo=meta.codigo,
                nombre=meta.nombre,
                origen=meta.origen,
                origen_label=origen_label,
                estado_label=estado_label,
                estado_revision=estado_revision,
                tipo_documento=meta.tipo_documento or "",
                areas=areas,
                principios=principios,
                criterios=criterios,
                consultores=[elaborador] if elaborador else [],
                elaborador=elaborador,
                revisado_por=revisado,
                clasificacion=clasificacion,
                es_multiprincipio=es_multiprincipio,
                fecha_limite=meta.fecha_limite_entrega,
                fecha_carga=fecha_carga,
                fecha_revision=fecha_revision,
            )
        )
    return resultado


def filtrar_documentos(
    docs: list[DocumentoIndicador], subtab: str
) -> list[DocumentoIndicador]:
    if subtab == "referenciales":
        return [d for d in docs if d.clasificacion == "referenciales"]
    if subtab == "multidisciplinario":
        return [d for d in docs if d.clasificacion == "multidisciplinario"]
    if subtab == "multiprincipio":
        return [d for d in docs if d.es_multiprincipio]
    return docs


def filtrar_documentos_query(
    docs: list[DocumentoIndicador],
    *,
    q: str = "",
    consultor: str = "",
    area: str = "",
    estado: str = "",
    fecha_desde=None,
    fecha_hasta=None,
) -> list[DocumentoIndicador]:
    """Filtros adicionales sobre la lista ya recortada por subtab."""
    q = (q or "").strip().lower()
    consultor = (consultor or "").strip()
    area = (area or "").strip()
    estado = (estado or "").strip()
    out: list[DocumentoIndicador] = []
    for d in docs:
        if q and q not in d.codigo.lower() and q not in d.nombre.lower():
            continue
        if consultor and (d.elaborador or "") != consultor:
            continue
        if area and area not in (d.areas or []):
            continue
        if estado and d.estado_label != estado:
            continue
        # Rango de fechas: prioridad fecha límite → revisión → carga
        ref = d.fecha_limite or d.fecha_revision or d.fecha_carga
        if fecha_desde or fecha_hasta:
            if ref is None:
                continue
            if fecha_desde and ref < fecha_desde:
                continue
            if fecha_hasta and ref > fecha_hasta:
                continue
        out.append(d)
    return out


def opciones_filtros_documentos(docs: list[DocumentoIndicador]) -> dict:
    areas: set[str] = set()
    estados: set[str] = set()
    consultores: set[str] = set()
    for d in docs:
        areas.update(d.areas or [])
        if d.estado_label:
            estados.add(d.estado_label)
        if d.elaborador:
            consultores.add(d.elaborador)
    return {
        "areas": sorted(areas),
        "estados": sorted(estados),
        "consultores": sorted(consultores),
    }


def cobertura_estandar(revision: Revision, progreso: ProgresoRevision) -> CoberturaEstandar:
    catalogo_docs = list(Documento.objects.order_by("codigo"))
    activos = {
        ed.documento_id
        for ed in EmpresaDocumento.objects.filter(
            revision=revision,
            activo=True,
            origen=OrigenEmpresaDocumento.CATALOGO,
            documento__isnull=False,
        )
    }
    docs_total = len(catalogo_docs)
    docs_activos = sum(1 for d in catalogo_docs if d.pk in activos)

    from ..models import EmpresaDocumentoCriterio

    vinculos_estandar = list(
        DocumentoCriterio.objects.select_related(
            "criterio", "criterio__principio", "criterio__tema", "documento"
        )
    )
    criterios_estandar_ids = {v.criterio_id for v in vinculos_estandar}
    vinculados_empresa = set(
        EmpresaDocumentoCriterio.objects.filter(
            revision=revision,
            empresa_documento__activo=True,
        ).values_list("criterio_id", flat=True)
    )
    criterios_estandar_total = len(criterios_estandar_ids)
    criterios_vinculados = len(criterios_estandar_ids & vinculados_empresa)

    docs_catalogo_sin_evidencia = sum(
        1
        for d in progreso.documentos
        if d.es_catalogo and not d.cargado
    )

    por_tema: dict[str, dict] = {}
    for v in vinculos_estandar:
        tema = v.criterio.tema
        key = tema.codigo
        fila = por_tema.setdefault(
            key,
            {
                "tema_codigo": tema.codigo,
                "tema_nombre": tema.nombre,
                "criterios_estandar": 0,
                "criterios_vinculados": 0,
                "principios": set(),
            },
        )
        fila["criterios_estandar"] += 1
        fila["principios"].add(v.criterio.principio.codigo)
        if v.criterio_id in vinculados_empresa:
            fila["criterios_vinculados"] += 1

    filas = []
    for key in sorted(por_tema.keys()):
        fila = por_tema[key]
        total = fila["criterios_estandar"]
        ok = fila["criterios_vinculados"]
        filas.append(
            {
                "tema_codigo": fila["tema_codigo"],
                "tema_nombre": fila["tema_nombre"],
                "principios": len(fila["principios"]),
                "criterios_estandar": total,
                "criterios_vinculados": ok,
                "pct": _pct(ok, total),
            }
        )

    return CoberturaEstandar(
        docs_catalogo_total=docs_total,
        docs_catalogo_activos=docs_activos,
        docs_catalogo_pct=_pct(docs_activos, docs_total),
        criterios_estandar_total=criterios_estandar_total,
        criterios_vinculados=criterios_vinculados,
        criterios_cobertura_pct=_pct(criterios_vinculados, criterios_estandar_total),
        docs_sin_evidencia=docs_catalogo_sin_evidencia,
        criterios_huerfanos=progreso.criterios_huerfanos_count,
        filas_tema=filas,
    )


def glosario_items(q: str = "") -> list[GlosarioItem]:
    """Términos activos registrados en Administración → Catálogo → Glosario."""
    from ..models import GlosarioTermino

    qs = GlosarioTermino.objects.filter(activo=True).order_by("concepto")
    q = (q or "").strip()
    if q:
        qs = qs.filter(Q(concepto__icontains=q) | Q(descripcion__icontains=q))
    return [
        GlosarioItem(id=t.pk, concepto=t.concepto, descripcion=t.descripcion or "")
        for t in qs
    ]


def chart_general(progreso: ProgresoRevision) -> dict:
    """Donut global + desglose por tema para filtros cruzados en General."""
    overall = {
        "aprobados": progreso.docs_cargados,
        "pendientes": max(progreso.docs_total - progreso.docs_cargados, 0),
    }
    por_tema: dict[str, dict[str, int]] = {
        t.codigo: {"aprobados": 0, "pendientes": 0} for t in progreso.temas
    }
    for doc in progreso.documentos:
        temas_doc = _temas_de_documento_progreso(doc)
        if not temas_doc:
            continue
        for tc in temas_doc:
            bucket = por_tema.setdefault(tc, {"aprobados": 0, "pendientes": 0})
            if doc.cargado:
                bucket["aprobados"] += 1
            else:
                bucket["pendientes"] += 1
    return {
        "docs_donut": {
            "labels": ["Aprobados", "Pendientes"],
            "values": [overall["aprobados"], overall["pendientes"]],
        },
        "overall": overall,
        "por_tema": por_tema,
    }


def chart_temas(progreso: ProgresoRevision) -> dict:
    """% de cumplimiento por tema (promedio del avance parcial), igual que Seguimiento."""
    temas = sorted(progreso.temas, key=lambda t: t.codigo)
    return {
        "labels": [t.codigo for t in temas],
        "nombres": [t.nombre for t in temas],
        "values": [t.pct for t in temas],
        "completos": [t.completos for t in temas],
        "totales": [t.total for t in temas],
    }


def chart_principios(principios: list[PrincipioAvance]) -> dict:
    return {
        "labels": [f"{p.codigo}" for p in principios],
        "values": [p.pct for p in principios],
        "nombres": [p.nombre for p in principios],
    }


def _label_principio(codigo: str) -> str:
    num = codigo[1:] if codigo[:1].upper() == "P" and len(codigo) > 1 else codigo
    return f"Principio {num}"


def _label_requisito(codigo: str) -> str:
    num = codigo[1:] if codigo[:1].upper() == "R" and len(codigo) > 1 else codigo
    return f"Requisito {num}"


def _aplica_a_label(codigo: str, descripcion: str) -> str:
    import re

    m = re.match(r"^(\d+\.\d+\s*-\s*[A-Za-z])\b", (descripcion or "").strip())
    if m:
        return m.group(1)
    return codigo


def _estado_madurez_doc(doc) -> str:
    """
    Etiqueta de revisión del consultor (campo libre en el documento).
    Independiente de PENDIENTE / APROBADO / RECHAZADO del sistema.
    """
    if getattr(doc, "criterios_count", 0) <= 0:
        return "No Asignado"
    texto = (getattr(doc, "revision_texto", None) or "").strip()
    return texto or "Sin revisión"


def tablero_estandar() -> dict:
    """
    Navegador del estándar GISTM (catálogo):
    temas → principios (con descripción) → requisitos y criterios.
    """
    temas_qs = Tema.objects.prefetch_related(
        "principios__requisitos__criterios",
    ).order_by("codigo")
    temas_out: list[dict] = []
    for tema in temas_qs:
        principios_out: list[dict] = []
        for prin in sorted(tema.principios.all(), key=lambda p: p.codigo):
            requisitos_out: list[dict] = []
            criterios_prin: list[dict] = []
            for req in sorted(prin.requisitos.all(), key=lambda r: r.codigo):
                crits_req: list[dict] = []
                for crit in sorted(req.criterios.all(), key=lambda c: c.codigo):
                    desc = crit.descripcion or crit.codigo
                    item = {
                        "codigo": crit.codigo,
                        "aplica_a": _aplica_a_label(crit.codigo, desc),
                        "descripcion": desc,
                        "requisito": req.codigo,
                    }
                    crits_req.append(item)
                    criterios_prin.append(item)
                requisitos_out.append(
                    {
                        "codigo": req.codigo,
                        "label": _label_requisito(req.codigo),
                        "descripcion": req.descripcion or req.codigo,
                        "criterios": crits_req,
                    }
                )
            principios_out.append(
                {
                    "codigo": prin.codigo,
                    "label": _label_principio(prin.codigo),
                    "descripcion": prin.descripcion or "",
                    "requisitos": requisitos_out,
                    "criterios": criterios_prin,
                }
            )
        temas_out.append(
            {
                "codigo": tema.codigo,
                "nombre": tema.nombre,
                "principios": principios_out,
            }
        )
    return {
        "temas": temas_out,
        "tema_activo": temas_out[0]["codigo"] if temas_out else "",
    }


def tablero_progreso(
    progreso: ProgresoRevision,
    *,
    empresa: Empresa | None = None,
) -> dict:
    """
    Datos para Progreso: temas → principios → requisitos,
    más tablas de documentos asociados y criterios del tema.
    """
    # Criterio codigo → doc progreso (vía vínculos)
    crit_to_doc: dict[str, object] = {}
    for doc in progreso.documentos_all_activos:
        for vinculo in doc.vinculos or []:
            crit = getattr(vinculo, "criterio", None)
            if crit and crit.codigo not in crit_to_doc:
                crit_to_doc[crit.codigo] = doc

    # Descripciones completas (el árbol de progreso las trunca)
    codigos_crit = [
        crit.codigo
        for tema in progreso.temas
        for prin in tema.children
        for req in prin.children
        for crit in req.children
    ]
    desc_by_codigo = {
        c.codigo: (c.descripcion or c.codigo)
        for c in Criterio.objects.filter(codigo__in=codigos_crit).only(
            "codigo", "descripcion"
        )
    }

    temas_out: list[dict] = []
    for tema in progreso.temas:
        principios_out: list[dict] = []
        requisitos_tema: list[dict] = []
        criterios_tema: list[dict] = []
        docs_ids_tema: set[int] = set()
        docs_meta: dict[int, dict] = {}

        for prin in tema.children:
            crits_total_p = 0
            crits_ok_p = 0
            for req in prin.children:
                crits_total_p += req.total
                crits_ok_p += req.completos
                req_estado, req_clave = nivel_cumplimiento_anddes(pct=req.pct)
                requisitos_tema.append(
                    {
                        "codigo": req.codigo,
                        "label": _label_requisito(req.codigo),
                        "nombre": req.nombre,
                        "pct": req.pct,
                        "completos": req.completos,
                        "total": req.total,
                        "principio": prin.codigo,
                        "estado": req_estado,
                        "estado_clave": req_clave,
                    }
                )
                for crit in req.children:
                    descripcion = desc_by_codigo.get(crit.codigo) or crit.nombre or crit.codigo
                    doc = crit_to_doc.get(crit.codigo)
                    if doc is not None:
                        docs_ids_tema.add(doc.id)
                        elaborador = (
                            (getattr(doc, "consultor_nombre", None) or "").strip()
                            or "—"
                        )
                        bucket = docs_meta.setdefault(
                            doc.id,
                            {
                                "id": doc.id,
                                "codigo": doc.codigo,
                                "nombre": doc.nombre,
                                "revision": _estado_madurez_doc(doc),
                                "revision_porcentaje": getattr(
                                    doc, "revision_porcentaje", None
                                ),
                                "estado_sistema": getattr(
                                    doc, "estado_label", None
                                )
                                or "—",
                                "elaborador": elaborador,
                                "areas": [],
                                "principios": [],
                                "requisitos": [],
                                "crit_total": 0,
                                "crit_ok": 0,
                            },
                        )
                        bucket["crit_total"] += 1
                        if crit.pct >= 100:
                            bucket["crit_ok"] += 1
                        if prin.codigo not in bucket["principios"]:
                            bucket["principios"].append(prin.codigo)
                        if req.codigo not in bucket["requisitos"]:
                            bucket["requisitos"].append(req.codigo)
                        for vinculo in doc.vinculos or []:
                            area = getattr(vinculo, "area", None)
                            if area and area.nombre not in bucket["areas"]:
                                bucket["areas"].append(area.nombre)
                    crit_estado, crit_clave = nivel_cumplimiento_anddes(pct=crit.pct)
                    criterios_tema.append(
                        {
                            "codigo": crit.codigo,
                            "aplica_a": _aplica_a_label(crit.codigo, descripcion),
                            "descripcion": descripcion,
                            "pct": crit.pct,
                            "estado": crit_estado,
                            "estado_clave": crit_clave,
                            "requisito": req.codigo,
                            "principio": prin.codigo,
                        }
                    )
            principios_out.append(
                {
                    "codigo": prin.codigo,
                    "label": _label_principio(prin.codigo),
                    "nombre": prin.nombre,
                    "pct": _pct(crits_ok_p, crits_total_p),
                    "completos": crits_ok_p,
                    "total": crits_total_p,
                }
            )

        documentos = []
        for doc_id in sorted(docs_ids_tema, key=lambda i: docs_meta[i]["codigo"]):
            m = docs_meta[doc_id]
            documentos.append(
                {
                    "id": m["id"],
                    "codigo": m["codigo"],
                    "nombre": m["nombre"],
                    # Compat: "estado" = revisión del consultor (como antes en la UI).
                    "estado": m["revision"],
                    "revision": m["revision"],
                    "revision_porcentaje": m.get("revision_porcentaje"),
                    "estado_sistema": m.get("estado_sistema") or "—",
                    "elaborador": m["elaborador"],
                    "area": m["areas"][0] if m["areas"] else "—",
                    "avance": _pct(m["crit_ok"], m["crit_total"]),
                    "principios": m.get("principios") or [],
                    "requisitos": m.get("requisitos") or [],
                }
            )

        temas_out.append(
            {
                "codigo": tema.codigo,
                "nombre": tema.nombre,
                "pct": tema.pct,
                "completos": tema.completos,
                "total": tema.total,
                "principios": principios_out,
                "requisitos": requisitos_tema,
                "documentos": documentos,
                "criterios": criterios_tema,
            }
        )

    # 5.3: criterios desactivados en la revisión aparecen como «No aplica».
    from ..models import RevisionCatalogoCriterio, RevisionCriterioDesactivado

    revision_pk = progreso.revision.pk
    desactivados_ids = set(
        RevisionCriterioDesactivado.objects.filter(
            revision_id=revision_pk
        ).values_list("criterio_id", flat=True)
    )
    if desactivados_ids:
        no_aplica_label, no_aplica_clave = nivel_cumplimiento_anddes(no_aplica=True)
        temas_by_codigo = {t["codigo"]: t for t in temas_out}
        reqs_visibles = {
            (t["codigo"], r["codigo"]) for t in temas_out for r in t["requisitos"]
        }
        snaps_na = RevisionCatalogoCriterio.objects.filter(
            revision_id=revision_pk,
            criterio_id__in=desactivados_ids,
        )
        for s in snaps_na:
            tema_bucket = temas_by_codigo.get(s.tema_codigo)
            if tema_bucket is None:
                tema_bucket = {
                    "codigo": s.tema_codigo,
                    "nombre": s.tema_nombre or s.tema_codigo,
                    "pct": 0.0,
                    "completos": 0,
                    "total": 0,
                    "principios": [],
                    "requisitos": [],
                    "documentos": [],
                    "criterios": [],
                }
                temas_out.append(tema_bucket)
                temas_by_codigo[s.tema_codigo] = tema_bucket
            desc = s.descripcion or s.codigo
            tema_bucket["criterios"].append(
                {
                    "codigo": s.codigo,
                    "aplica_a": _aplica_a_label(s.codigo, desc),
                    "descripcion": desc,
                    "pct": 0.0,
                    "estado": no_aplica_label,
                    "estado_clave": no_aplica_clave,
                    "requisito": s.requisito_codigo,
                    "principio": s.principio_codigo,
                }
            )
            key = (s.tema_codigo, s.requisito_codigo)
            if key not in reqs_visibles:
                tema_bucket["requisitos"].append(
                    {
                        "codigo": s.requisito_codigo,
                        "label": _label_requisito(s.requisito_codigo),
                        "nombre": (s.requisito_descripcion or "")[:120],
                        "pct": 0.0,
                        "completos": 0,
                        "total": 0,
                        "principio": s.principio_codigo,
                        "estado": no_aplica_label,
                        "estado_clave": no_aplica_clave,
                    }
                )
                reqs_visibles.add(key)

    return {
        "temas": temas_out,
        "tema_activo": temas_out[0]["codigo"] if temas_out else "",
    }


def _month_ends(start: date, end: date) -> list[date]:
    """Último día de cada mes entre start y end (inclusive)."""
    if end < start:
        start, end = end, start
    points: list[date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        last = monthrange(y, m)[1]
        points.append(date(y, m, last))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return points


def _curva_series_para_docs(
    revision: Revision,
    docs: list,
    *,
    docs_pct_fallback: float,
    inicio: date,
    fin: date,
    hoy_clip: date,
    months: list[date],
) -> dict:
    """Plan / Real / Forecast para un subconjunto de documentos."""
    from ..models import EmpresaDocumento

    total_docs = max(len(docs), 1)
    doc_ids = {d.id for d in docs}
    plan_fechas = list(
        EmpresaDocumento.objects.filter(
            revision=revision,
            activo=True,
            pk__in=doc_ids,
            fecha_limite_entrega__isnull=False,
        ).values_list("fecha_limite_entrega", flat=True)
    )
    usa_plan_docs = len(plan_fechas) > 0
    plan_fechas.sort()

    aprob_fechas: list[date] = []
    for doc in docs:
        if not getattr(doc, "cargado", False) or not getattr(doc, "carga", None):
            continue
        dt = doc.carga.revisado_en or doc.carga.creado_en
        if not dt:
            continue
        if timezone.is_aware(dt):
            dt = timezone.localtime(dt)
        aprob_fechas.append(dt.date())
    aprob_fechas.sort()

    plan: list[float | None] = []
    real: list[float | None] = []
    forecast: list[float | None] = []
    span_days = max((fin - inicio).days, 1)

    for d in months:
        if usa_plan_docs:
            planificados = sum(1 for fd in plan_fechas if fd <= d)
            plan_pct = round(100.0 * planificados / max(len(plan_fechas), 1), 2)
        else:
            plan_pct = round(100.0 * (d - inicio).days / span_days, 2)
        plan.append(min(100.0, max(0.0, plan_pct)))

        if d < inicio:
            real.append(0.0)
            forecast.append(None)
            continue

        if d <= hoy_clip:
            aprobados = sum(1 for fd in aprob_fechas if fd <= d)
            meses_hasta_hoy = [x for x in months if x <= hoy_clip]
            ultimo_mes = meses_hasta_hoy[-1] if meses_hasta_hoy else d
            if not aprob_fechas and d == ultimo_mes:
                real.append(float(docs_pct_fallback))
            else:
                real.append(round(100.0 * aprobados / total_docs, 2))
            forecast.append(None)
        else:
            real.append(None)
            forecast.append(None)

    last_real_idx = None
    last_real_val = 0.0
    for i, v in enumerate(real):
        if v is not None:
            last_real_idx = i
            last_real_val = v
    if last_real_idx is None:
        last_real_idx = 0
        last_real_val = float(docs_pct_fallback)
        if real:
            real[0] = last_real_val

    forecast[last_real_idx] = last_real_val
    remaining = len(months) - 1 - last_real_idx
    if remaining <= 0:
        forecast[-1] = 100.0 if last_real_val >= 100 else last_real_val
    else:
        for i in range(last_real_idx + 1, len(months)):
            step = i - last_real_idx
            if last_real_val >= 100:
                forecast[i] = 100.0
            else:
                forecast[i] = round(
                    last_real_val + (100.0 - last_real_val) * step / remaining, 2
                )

    return {"plan": plan, "real": real, "forecast": forecast}


def curva_avance(revision: Revision, progreso: ProgresoRevision) -> dict:
    """
    Serie Plan / Real / Forecast (global + por tema para filtros cruzados).
    - Plan: fechas límite por documento, o lineal 0→100% si no hay plan.
    - Real: % docs aprobados acumulado según fecha de aprobación/carga.
    - Forecast: desde el último punto real hasta 100% en fecha_fin.
    """
    vacio = {
        "labels": [],
        "plan": [],
        "real": [],
        "forecast": [],
        "hoy_label": "",
        "sin_fechas": True,
        "por_tema": {},
    }
    if not revision.fecha_inicio or not revision.fecha_fin:
        return vacio
    if revision.fecha_fin < revision.fecha_inicio:
        return vacio

    inicio = revision.fecha_inicio
    fin = revision.fecha_fin
    hoy = timezone.localdate()
    if hoy < inicio:
        hoy_clip = inicio
    elif hoy > fin:
        hoy_clip = fin
    else:
        hoy_clip = hoy

    months = _month_ends(inicio, fin)
    if not months:
        return vacio

    labels = [d.strftime("%b %Y") for d in months]
    global_series = _curva_series_para_docs(
        revision,
        list(progreso.documentos),
        docs_pct_fallback=float(progreso.docs_pct),
        inicio=inicio,
        fin=fin,
        hoy_clip=hoy_clip,
        months=months,
    )

    por_tema: dict[str, dict] = {}
    for tema in progreso.temas:
        docs_tema = [
            d
            for d in progreso.documentos
            if tema.codigo in _temas_de_documento_progreso(d)
        ]
        if not docs_tema:
            continue
        aprobados = sum(1 for d in docs_tema if d.cargado)
        por_tema[tema.codigo] = _curva_series_para_docs(
            revision,
            docs_tema,
            docs_pct_fallback=_pct(aprobados, len(docs_tema)),
            inicio=inicio,
            fin=fin,
            hoy_clip=hoy_clip,
            months=months,
        )

    return {
        "labels": labels,
        "plan": global_series["plan"],
        "real": global_series["real"],
        "forecast": global_series["forecast"],
        "hoy_label": hoy_clip.strftime("%d/%m/%Y"),
        "sin_fechas": False,
        "fecha_inicio": inicio.isoformat(),
        "fecha_fin": fin.isoformat(),
        "por_tema": por_tema,
    }


def chart_documentos_estados(progreso: ProgresoRevision) -> dict:
    """
    Pastel por etiqueta de revisión del consultor (revision_texto).
    Independiente del estado de carga/aprobación del sistema.
    """
    palette = [
        "#1f4e79",
        "#5b9bd5",
        "#e891b0",
        "#7f7f7f",
        "#ffc000",
        "#2f9e6b",
        "#0b4f7d",
        "#c55a11",
    ]
    counts: dict[str, int] = {}
    for doc in progreso.documentos:
        label = _estado_madurez_doc(doc)
        counts[label] = counts.get(label, 0) + 1
    for doc in progreso.documentos_sin_criterios_config:
        counts["No Asignado"] = counts.get("No Asignado", 0) + 1

    # Orden preferido si existen esas etiquetas; el resto alfabético.
    preferidos = ["Iniciado", "Rev0", "RevA", "RevB", "Sin revisión", "No Asignado"]
    ordenados = [k for k in preferidos if k in counts]
    ordenados += sorted(k for k in counts if k not in preferidos)
    slices = [
        (label, counts[label], palette[i % len(palette)])
        for i, label in enumerate(ordenados)
    ]
    if sum(v for _, v, _ in slices) == 0:
        slices = [("Sin datos", 1, "#d0d5da")]
    total_real = sum(s[1] for s in slices if s[0] != "Sin datos")
    return {
        "labels": [s[0] for s in slices],
        "values": [s[1] for s in slices],
        "colors": [s[2] for s in slices],
        "total": total_real,
        "leyenda": [
            {
                "label": s[0],
                "value": s[1],
                "pct": _pct(s[1], max(total_real, 1)),
                "color": s[2],
            }
            for s in slices
            if s[0] != "Sin datos"
        ],
    }


def _area_primer_vinculo(meta: EmpresaDocumento) -> str:
    for v in meta.vinculos_criterio.all():
        if v.area_id:
            return v.area.nombre
    return "—"


def documentos_prioritarios(
    revision: Revision, progreso: ProgresoRevision, *, limite: int = 8
) -> list[DocumentoPrioritario]:
    """Documentos marcados como prioritarios en la revisión (campo prioritario)."""
    from django.db.models import Prefetch

    from ..models import EmpresaDocumentoCriterio

    docs = list(
        EmpresaDocumento.objects.filter(
            revision=revision, activo=True, prioritario=True
        )
        .prefetch_related(
            Prefetch(
                "vinculos_criterio",
                queryset=EmpresaDocumentoCriterio.objects.select_related(
                    "area", "criterio", "criterio__tema"
                ),
            )
        )
        .order_by("codigo")[:limite]
    )
    prog_by_id = {d.id: d for d in progreso.documentos_all_activos}
    result: list[DocumentoPrioritario] = []
    for meta in docs:
        prog = prog_by_id.get(meta.pk)
        temas = _temas_de_documento_progreso(prog) if prog else []
        if not temas:
            for v in meta.vinculos_criterio.all():
                tema = getattr(getattr(v, "criterio", None), "tema", None)
                if tema and tema.codigo not in temas:
                    temas.append(tema.codigo)
        result.append(
            DocumentoPrioritario(
                id=meta.pk,
                nombre=meta.nombre,
                codigo=meta.codigo,
                area=_area_primer_vinculo(meta),
                estado_label=prog.estado_label if prog else "—",
                temas=temas,
                aprobado=bool(prog and prog.cargado),
            )
        )
    return result


# Ventana por defecto: hoy y hasta N días adelante (incluye ya vencidos).
DIAS_POR_VENCER = 15


def documentos_por_vencer(
    revision: Revision,
    progreso: ProgresoRevision,
    *,
    dias: int = DIAS_POR_VENCER,
    limite: int = 12,
) -> list[DocumentoPorVencer]:
    """Activos de la revisión con fecha límite cercana o ya vencida (no aprobados)."""
    from datetime import timedelta

    from django.db.models import Prefetch

    from ..models import EmpresaDocumentoCriterio

    hoy = date.today()
    hasta = hoy + timedelta(days=max(0, dias))
    docs = list(
        EmpresaDocumento.objects.filter(
            revision=revision,
            activo=True,
            fecha_limite_entrega__isnull=False,
            fecha_limite_entrega__lte=hasta,
        )
        .prefetch_related(
            Prefetch(
                "vinculos_criterio",
                queryset=EmpresaDocumentoCriterio.objects.select_related(
                    "area", "criterio", "criterio__tema"
                ),
            )
        )
        .order_by("fecha_limite_entrega", "codigo")
    )
    prog_by_id = {d.id: d for d in progreso.documentos_all_activos}
    result: list[DocumentoPorVencer] = []
    for meta in docs:
        prog = prog_by_id.get(meta.pk)
        if prog and prog.estado_label == "Aprobado":
            continue
        limite_f = meta.fecha_limite_entrega
        temas = _temas_de_documento_progreso(prog) if prog else []
        if not temas:
            for v in meta.vinculos_criterio.all():
                tema = getattr(getattr(v, "criterio", None), "tema", None)
                if tema and tema.codigo not in temas:
                    temas.append(tema.codigo)
        result.append(
            DocumentoPorVencer(
                id=meta.pk,
                nombre=meta.nombre,
                codigo=meta.codigo,
                area=_area_primer_vinculo(meta),
                estado_label=prog.estado_label if prog else "—",
                fecha_limite=limite_f,
                dias=(limite_f - hoy).days,
                temas=temas,
                aprobado=False,
            )
        )
        if len(result) >= limite:
            break
    return result


def to_json(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def filas_empresas_indicadores(empresas) -> list[dict]:
    filas = []
    for empresa in empresas:
        revisiones = list(revisiones_activas_empresa(empresa.pk))
        if not revisiones:
            filas.append(
                {
                    "empresa": empresa,
                    "revision": None,
                    "progreso": None,
                }
            )
            continue
        for revision in revisiones:
            filas.append(
                {
                    "empresa": empresa,
                    "revision": revision,
                    "progreso": calcular_progreso_revision(revision),
                }
            )
    return filas
