from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from maestro.documento_criterio_excel import (
    format_import_message,
    importar_documento_criterio_excel,
)


class Command(BaseCommand):
    help = (
        "Importa la relación estándar Documento↔Criterio desde la hoja "
        "Documento_x_Criterio del Excel (ID_Documento, ID_Criterio)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default="",
            help="Ruta al excel.xlsx (por defecto gistm/excel.xlsx).",
        )

    def handle(self, *args, **options):
        path_opt = (options.get("path") or "").strip()
        if path_opt:
            excel_path = Path(path_opt)
        else:
            # manage.py vive en gistm/; excel en gistm/excel.xlsx
            excel_path = Path(__file__).resolve().parents[3] / "excel.xlsx"

        if not excel_path.is_file():
            raise CommandError(f"No se encontró el archivo: {excel_path}")

        try:
            stats = importar_documento_criterio_excel(excel_path)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(format_import_message(stats)))
        if stats["missing_docs"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Documentos no encontrados ({len(stats['missing_docs'])}): "
                    f"{', '.join(stats['missing_docs'][:15])}"
                    + ("..." if len(stats["missing_docs"]) > 15 else "")
                )
            )
        if stats["missing_crits"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Criterios no encontrados ({len(stats['missing_crits'])}): "
                    f"{', '.join(stats['missing_crits'][:15])}"
                    + ("..." if len(stats['missing_crits']) > 15 else "")
                )
            )
