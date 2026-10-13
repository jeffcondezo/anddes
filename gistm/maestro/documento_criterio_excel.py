from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

from django.db import transaction
from openpyxl import Workbook, load_workbook

from maestro.models import Criterio, Documento, DocumentoCriterio

SHEET_NAME = "Documento_x_Criterio"

HEADERS = (
    "ID_Documento",
    "Documento",
    "ID_Criterio",
    "Principio",
    "Criterio",
    "% Contribución",
    "Ponderación Criterio",
    "% Avance Documento",
    "Avance Ponderado Efectivo",
    "Rango de páginas",
    "Fragmentos",
)

# Fila de ejemplo (solo ID_Documento e ID_Criterio se usan al importar).
EJEMPLO = (
    "D001",
    "Compromiso de DDHH",
    "C001",
    "Principio 01",
    "1.1 - A",
    1,
    "",
    "",
    "",
    "",
    "-",
)


def build_plantilla_workbook() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    ws.append(list(HEADERS))
    ws.append(list(EJEMPLO))
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 14
    return wb


def plantilla_bytes() -> bytes:
    wb = build_plantilla_workbook()
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@transaction.atomic
def importar_documento_criterio_excel(source: str | Path | BinaryIO) -> dict[str, Any]:
    """
    Importa pares Documento↔Criterio desde la hoja Documento_x_Criterio.
    Usa columnas A (ID_Documento) y C (ID_Criterio).
    """
    wb = load_workbook(source, data_only=True)
    if SHEET_NAME not in wb.sheetnames:
        raise ValueError(f"La hoja '{SHEET_NAME}' no existe en el Excel.")
    ws = wb[SHEET_NAME]

    docs = {d.codigo: d for d in Documento.objects.all()}
    crits = {c.codigo: c for c in Criterio.objects.all()}

    created = 0
    existing = 0
    skipped = 0
    missing_docs: set[str] = set()
    missing_crits: set[str] = set()

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row:
            continue
        id_doc = str(row[0]).strip() if row[0] is not None else ""
        id_crit = str(row[2]).strip() if len(row) > 2 and row[2] is not None else ""
        if not id_doc or not id_crit:
            continue
        # No importar la fila de ejemplo si el catálogo no la tiene (se cuenta como omitida).
        documento = docs.get(id_doc)
        criterio = crits.get(id_crit)
        if documento is None:
            missing_docs.add(id_doc)
            skipped += 1
            continue
        if criterio is None:
            missing_crits.add(id_crit)
            skipped += 1
            continue

        _, was_created = DocumentoCriterio.objects.get_or_create(
            documento=documento,
            criterio=criterio,
        )
        if was_created:
            created += 1
        else:
            existing += 1

    return {
        "created": created,
        "existing": existing,
        "skipped": skipped,
        "missing_docs": sorted(missing_docs),
        "missing_crits": sorted(missing_crits),
    }


def format_import_message(stats: dict[str, Any]) -> str:
    parts = [
        f"creados={stats['created']}",
        f"ya existían={stats['existing']}",
        f"omitidos={stats['skipped']}",
    ]
    msg = "Importación lista: " + ", ".join(parts)
    if stats["missing_docs"]:
        docs = ", ".join(stats["missing_docs"][:15])
        if len(stats["missing_docs"]) > 15:
            docs += "..."
        msg += f". Documentos no encontrados: {docs}"
    if stats["missing_crits"]:
        crits = ", ".join(stats["missing_crits"][:15])
        if len(stats["missing_crits"]) > 15:
            crits += "..."
        msg += f". Criterios no encontrados: {crits}"
    return msg
