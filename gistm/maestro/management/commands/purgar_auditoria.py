from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from maestro.retencion_auditoria import (
    DIAS_RETENCION_AUDITORIA,
    purgar_auditoria_antigua,
)


class Command(BaseCommand):
    help = (
        "Elimina registros de auditoría (RegistroAuditoria) anteriores al "
        f"periodo de retención (por defecto {DIAS_RETENCION_AUDITORIA} días / 1 año). "
        "Pensado para cron diario o semanal. "
        "Ejemplo: python manage.py purgar_auditoria"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dias",
            type=int,
            default=DIAS_RETENCION_AUDITORIA,
            help=(
                f"Conservar los últimos N días (default {DIAS_RETENCION_AUDITORIA})."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Solo cuenta cuántos se borrarían; no elimina.",
        )

    def handle(self, *args, **options):
        dias = int(options.get("dias") or DIAS_RETENCION_AUDITORIA)
        dry_run = bool(options.get("dry_run"))
        try:
            resultado = purgar_auditoria_antigua(dias=dias, dry_run=dry_run)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        limite = resultado["limite"]
        n = resultado["eliminados"]
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] Se eliminarían {n} registro(s) anteriores a {limite}."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Auditoría: eliminados {n} registro(s) anteriores a {limite} "
                f"(retención {dias} días)."
            )
        )
