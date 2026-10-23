from __future__ import annotations

from dataclasses import dataclass, field

from .models import EmpresaDocumento, Revision


@dataclass
class PlanDocItem:
    id: int
    codigo: str
    nombre: str
    fecha_limite_entrega: object | None
    principios: list[str] = field(default_factory=list)


@dataclass
class PlanPrincipioGrupo:
    codigo: str
    nombre: str
    documentos: list[PlanDocItem] = field(default_factory=list)


@dataclass
class PlanTemaGrupo:
    codigo: str
    nombre: str
    principios: list[PlanPrincipioGrupo] = field(default_factory=list)


def arbol_plan_entrega(revision: Revision) -> list[PlanTemaGrupo]:
    """
    Documentos activos de la revisión agrupados por Tema → Principio
    (según el primer criterio vinculado, por código).
    Documentos sin criterios van en un grupo final «Sin asignar».
    """
    docs = list(
        EmpresaDocumento.objects.filter(revision=revision, activo=True)
        .prefetch_related(
            "vinculos_criterio__criterio__tema",
            "vinculos_criterio__criterio__principio",
        )
        .order_by("codigo")
    )

    # tema_codigo -> {principio_codigo -> {docs}}
    temas: dict[str, dict] = {}
    sin_asignar: list[PlanDocItem] = []

    for doc in docs:
        vinculos = list(doc.vinculos_criterio.all())
        if not vinculos:
            sin_asignar.append(
                PlanDocItem(
                    id=doc.pk,
                    codigo=doc.codigo,
                    nombre=doc.nombre,
                    fecha_limite_entrega=doc.fecha_limite_entrega,
                )
            )
            continue

        # Principios únicos del documento (para mostrar chips)
        prin_labels: list[str] = []
        seen_prin: set[str] = set()
        for v in sorted(vinculos, key=lambda x: x.criterio.codigo):
            p = v.criterio.principio
            if p and p.codigo not in seen_prin:
                seen_prin.add(p.codigo)
                prin_labels.append(p.codigo)

        # Ubicación primaria: primer criterio por código
        primario = sorted(vinculos, key=lambda x: x.criterio.codigo)[0]
        tema = primario.criterio.tema
        principio = primario.criterio.principio
        tema_key = tema.codigo if tema else "—"
        tema_nombre = tema.nombre if tema else "Sin tema"
        prin_key = principio.codigo if principio else "—"
        prin_nombre = (
            (principio.descripcion or principio.codigo) if principio else "Sin principio"
        )

        tema_bucket = temas.setdefault(
            tema_key,
            {"nombre": tema_nombre, "principios": {}},
        )
        prin_bucket = tema_bucket["principios"].setdefault(
            prin_key,
            {"nombre": prin_nombre, "docs": []},
        )
        prin_bucket["docs"].append(
            PlanDocItem(
                id=doc.pk,
                codigo=doc.codigo,
                nombre=doc.nombre,
                fecha_limite_entrega=doc.fecha_limite_entrega,
                principios=prin_labels,
            )
        )

    out: list[PlanTemaGrupo] = []
    for tema_codigo in sorted(temas.keys()):
        t = temas[tema_codigo]
        principios_out: list[PlanPrincipioGrupo] = []
        for prin_codigo in sorted(t["principios"].keys()):
            p = t["principios"][prin_codigo]
            docs_sorted = sorted(p["docs"], key=lambda d: d.codigo)
            principios_out.append(
                PlanPrincipioGrupo(
                    codigo=prin_codigo,
                    nombre=p["nombre"],
                    documentos=docs_sorted,
                )
            )
        out.append(
            PlanTemaGrupo(
                codigo=tema_codigo,
                nombre=t["nombre"],
                principios=principios_out,
            )
        )

    if sin_asignar:
        out.append(
            PlanTemaGrupo(
                codigo="—",
                nombre="Sin criterios asignados",
                principios=[
                    PlanPrincipioGrupo(
                        codigo="—",
                        nombre="Documentos sin vínculo a criterio",
                        documentos=sorted(sin_asignar, key=lambda d: d.codigo),
                    )
                ],
            )
        )
    return out
