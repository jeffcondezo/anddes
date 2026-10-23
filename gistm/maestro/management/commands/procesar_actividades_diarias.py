from datetime import datetime

from django.core.management.base import BaseCommand

from maestro.actividad_recordatorios import procesar_actividades_diarias
from maestro.acuerdo_recordatorios import enviar_recordatorios_automaticos
from maestro.documento_caducidad import enviar_recordatorios_caducidad


class Command(BaseCommand):
    help = (
        "Job diario (cron 1×/día): marca actividades PROGRAMADA→PASADA y "
        "acuerdos ABIERTO→VENCIDO; crea avisos de actividades; envía "
        "recordatorios de acuerdos y caducidad de documentos según reglas por empresa. "
        "Ejemplo crontab: 0 7 * * * cd /app && python manage.py procesar_actividades_diarias"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fecha",
            default="",
            help="Fecha YYYY-MM-DD (por defecto: hoy local). Útil para pruebas.",
        )
        parser.add_argument(
            "--sin-acuerdos",
            action="store_true",
            help="No enviar recordatorios de acuerdos.",
        )

    def handle(self, *args, **options):
        fecha_txt = (options.get("fecha") or "").strip()
        dia = None
        if fecha_txt:
            dia = datetime.strptime(fecha_txt, "%Y-%m-%d").date()

        resultado = procesar_actividades_diarias(dia=dia)
        self.stdout.write(
            self.style.SUCCESS(
                f"Actividades pasadas: {resultado['pasadas']}. "
                f"Acuerdos vencidos: {resultado['acuerdos_vencidos']}. "
                f"Avisos pasadas: {resultado['avisos_pasadas']}. "
                f"Avisos próximas: {resultado['avisos_proximas']} "
                f"(omitidos: {resultado['omitidos_proximas']})."
            )
        )

        if not options.get("sin_acuerdos"):
            acuerdos = enviar_recordatorios_automaticos(dia)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Recordatorios acuerdos enviados: {acuerdos['enviados']}. "
                    f"Omitidos: {acuerdos['omitidos']}."
                )
            )
            for err in acuerdos["errores"]:
                self.stdout.write(self.style.ERROR(err))

            caducidad = enviar_recordatorios_caducidad(dia)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Recordatorios caducidad docs: {caducidad['enviados']}. "
                    f"Omitidos: {caducidad['omitidos']}."
                )
            )
            for err in caducidad["errores"]:
                self.stdout.write(self.style.ERROR(err))
