from datetime import datetime

from django.core.management.base import BaseCommand

from maestro.acuerdo_recordatorios import enviar_recordatorios_automaticos


class Command(BaseCommand):
    help = (
        "Envía recordatorios automáticos de acuerdos con fecha de compromiso = hoy. "
        "Úsalo desde cron en el servidor (ej. una vez al día)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fecha",
            default="",
            help="Fecha YYYY-MM-DD (por defecto: hoy local). Útil para pruebas.",
        )

    def handle(self, *args, **options):
        fecha_txt = (options.get("fecha") or "").strip()
        dia = None
        if fecha_txt:
            dia = datetime.strptime(fecha_txt, "%Y-%m-%d").date()
        resultado = enviar_recordatorios_automaticos(dia)
        self.stdout.write(
            self.style.SUCCESS(
                f"Recordatorios enviados: {resultado['enviados']}. "
                f"Omitidos: {resultado['omitidos']}."
            )
        )
        for err in resultado["errores"]:
            self.stdout.write(self.style.ERROR(err))
