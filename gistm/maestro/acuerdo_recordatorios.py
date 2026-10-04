from __future__ import annotations

from datetime import date

from django.utils import timezone

from .actividad_mail import enviar_correo_acuerdo, publicar_mensajes_acuerdo
from .models import Acuerdo, EstadoAcuerdo


def acuerdos_para_recordatorio_automatico(dia: date | None = None) -> list[Acuerdo]:
    """
    Acuerdos abiertos con fecha de compromiso en `dia` (por defecto hoy, hora local)
    que aún no recibieron recordatorio ese mismo día.
    """
    dia = dia or timezone.localdate()
    qs = (
        Acuerdo.objects.filter(
            estado=EstadoAcuerdo.ABIERTO,
            empresa__isnull=False,
            fecha_compromiso__isnull=False,
        )
        .select_related("empresa", "actividad", "creado_por")
        .prefetch_related("documentos")
    )
    candidatos: list[Acuerdo] = []
    for acuerdo in qs:
        compromiso = acuerdo.fecha_compromiso
        compromiso_local = (
            timezone.localtime(compromiso)
            if timezone.is_aware(compromiso)
            else compromiso
        )
        if compromiso_local.date() != dia:
            continue
        if acuerdo.ultimo_recordatorio_en:
            ult = acuerdo.ultimo_recordatorio_en
            ult_local = timezone.localtime(ult) if timezone.is_aware(ult) else ult
            if ult_local.date() >= dia:
                continue
        candidatos.append(acuerdo)
    return candidatos


def enviar_recordatorios_automaticos(dia: date | None = None) -> dict:
    """
    Envía recordatorios del día. Pensado para cron:
      python manage.py enviar_recordatorios_acuerdos
    """
    enviados = 0
    omitidos = 0
    errores: list[str] = []
    for acuerdo in acuerdos_para_recordatorio_automatico(dia):
        try:
            ok, msg = enviar_correo_acuerdo(acuerdo, es_recordatorio=True)
            if ok:
                publicar_mensajes_acuerdo(
                    acuerdo, acuerdo.creado_por, solo_pendientes=True
                )
                enviados += 1
            else:
                omitidos += 1
        except Exception as exc:  # noqa: BLE001
            errores.append(f"Acuerdo {acuerdo.pk}: {exc}")
    return {"enviados": enviados, "omitidos": omitidos, "errores": errores}
