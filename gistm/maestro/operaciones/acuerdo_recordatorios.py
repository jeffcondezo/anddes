from __future__ import annotations

from datetime import date, timedelta

from django.db.models import Prefetch
from django.utils import timezone

from .actividad_mail import enviar_correo_acuerdo, publicar_mensajes_acuerdo
from ..models import (
    Acuerdo,
    AcuerdoNotificacionEnvio,
    EmpresaNotificacionRegla,
    EstadoAcuerdo,
    NOTIFICACION_ACUERDO_DIA_SISTEMA,
    TipoNotificacionAcuerdo,
)


def _fecha_compromiso_local(acuerdo: Acuerdo) -> date | None:
    if not acuerdo.fecha_compromiso:
        return None
    compromiso = acuerdo.fecha_compromiso
    if timezone.is_aware(compromiso):
        return timezone.localtime(compromiso).date()
    return compromiso.date() if hasattr(compromiso, "date") else compromiso


def _ya_enviado_hoy(
    acuerdo: Acuerdo,
    *,
    tipo: str,
    dias: int,
    dia: date,
) -> bool:
    return AcuerdoNotificacionEnvio.objects.filter(
        acuerdo=acuerdo,
        tipo=tipo,
        dias=dias,
        dia_referencia=dia,
    ).exists()


def _registrar_envio(
    acuerdo: Acuerdo,
    regla: EmpresaNotificacionRegla | None,
    *,
    tipo: str,
    dias: int,
    dia: date,
) -> None:
    AcuerdoNotificacionEnvio.objects.create(
        acuerdo=acuerdo,
        regla=regla,
        tipo=tipo,
        dias=dias,
        dia_referencia=dia,
    )


def _regla_sistema_dia_cero(empresa_id: int) -> EmpresaNotificacionRegla:
    """Regla sintética: aviso base el día del vencimiento."""
    return EmpresaNotificacionRegla(
        empresa_id=empresa_id,
        tipo=TipoNotificacionAcuerdo.ANTES,
        dias=NOTIFICACION_ACUERDO_DIA_SISTEMA,
        activo=True,
    )


def _debe_disparar_sistema_dia_cero(acuerdo: Acuerdo, dia: date) -> bool:
    compromiso = _fecha_compromiso_local(acuerdo)
    if compromiso is None or compromiso != dia:
        return False
    if _ya_enviado_hoy(
        acuerdo,
        tipo=TipoNotificacionAcuerdo.ANTES,
        dias=NOTIFICACION_ACUERDO_DIA_SISTEMA,
        dia=dia,
    ):
        return False
    if acuerdo.ultimo_recordatorio_en:
        ult = acuerdo.ultimo_recordatorio_en
        ult_local = timezone.localtime(ult) if timezone.is_aware(ult) else ult
        if ult_local.date() >= dia:
            return False
    return True


def reglas_activas_por_empresa(empresa_ids: set[int]) -> dict[int, list[EmpresaNotificacionRegla]]:
    if not empresa_ids:
        return {}
    out: dict[int, list[EmpresaNotificacionRegla]] = {eid: [] for eid in empresa_ids}
    for regla in EmpresaNotificacionRegla.objects.filter(
        empresa_id__in=empresa_ids,
        activo=True,
    ).order_by("tipo", "dias"):
        out.setdefault(regla.empresa_id, []).append(regla)
    return out


def disparos_para_acuerdo(
    acuerdo: Acuerdo,
    dia: date,
    reglas: list[EmpresaNotificacionRegla],
) -> list[EmpresaNotificacionRegla]:
    """
    Reglas que aplican en `dia`.
    Siempre incluye el aviso base del sistema el día del vencimiento (ANTES/0).
    Las reglas de empresa son días exactos adicionales (antes o después).
    """
    compromiso = _fecha_compromiso_local(acuerdo)
    if compromiso is None:
        return []

    disparos: list[EmpresaNotificacionRegla] = []
    if _debe_disparar_sistema_dia_cero(acuerdo, dia):
        disparos.append(_regla_sistema_dia_cero(acuerdo.empresa_id))

    for regla in reglas:
        if regla.dias == NOTIFICACION_ACUERDO_DIA_SISTEMA:
            # El día 0 lo maneja solo el sistema.
            continue
        if regla.tipo == TipoNotificacionAcuerdo.ANTES:
            if compromiso - timedelta(days=regla.dias) == dia:
                if not _ya_enviado_hoy(
                    acuerdo, tipo=regla.tipo, dias=regla.dias, dia=dia
                ):
                    disparos.append(regla)
        elif regla.tipo == TipoNotificacionAcuerdo.POST:
            if compromiso + timedelta(days=regla.dias) == dia:
                if not _ya_enviado_hoy(
                    acuerdo, tipo=regla.tipo, dias=regla.dias, dia=dia
                ):
                    disparos.append(regla)
    return disparos


def acuerdos_candidatos(dia: date | None = None) -> list[Acuerdo]:
    dia = dia or timezone.localdate()
    return list(
        Acuerdo.objects.filter(
            # Abiertos (aún no vencidos) y vencidos siguen recibiendo reglas POST.
            estado__in=(EstadoAcuerdo.ABIERTO, EstadoAcuerdo.VENCIDO),
            empresa__isnull=False,
            fecha_compromiso__isnull=False,
        )
        .select_related("empresa", "actividad", "creado_por")
        .prefetch_related(
            "documentos",
            Prefetch(
                "empresa__notificacion_reglas",
                queryset=EmpresaNotificacionRegla.objects.filter(activo=True),
            ),
        )
    )


def enviar_recordatorios_automaticos(dia: date | None = None) -> dict:
    """
    Aplica aviso base (día 0) + reglas por empresa (días exactos antes/después).
    Pensado para cron diario.
    """
    dia = dia or timezone.localdate()
    enviados = 0
    omitidos = 0
    errores: list[str] = []

    acuerdos = acuerdos_candidatos(dia)
    empresa_ids = {a.empresa_id for a in acuerdos if a.empresa_id}
    reglas_map = reglas_activas_por_empresa(empresa_ids)

    for acuerdo in acuerdos:
        if acuerdo.empresa_id and not getattr(
            acuerdo.empresa, "notificaciones_activas", True
        ):
            omitidos += 1
            continue
        reglas = reglas_map.get(acuerdo.empresa_id, [])
        disparos = disparos_para_acuerdo(acuerdo, dia, reglas)
        for regla in disparos:
            try:
                ok, msg = enviar_correo_acuerdo(
                    acuerdo,
                    es_recordatorio=True,
                    tipo_regla=regla.tipo,
                    dias_regla=regla.dias,
                    automatico=True,
                )
                if ok:
                    _registrar_envio(
                        acuerdo,
                        regla if regla.pk else None,
                        tipo=regla.tipo,
                        dias=regla.dias,
                        dia=dia,
                    )
                    publicar_mensajes_acuerdo(
                        acuerdo, acuerdo.creado_por, solo_pendientes=True
                    )
                    enviados += 1
                else:
                    omitidos += 1
            except Exception as exc:  # noqa: BLE001
                errores.append(
                    f"Acuerdo {acuerdo.pk} regla {regla.tipo}/{regla.dias}: {exc}"
                )

    return {"enviados": enviados, "omitidos": omitidos, "errores": errores}


# Compat: nombre usado en tests/código antiguo
def enviar_recordatorios_acuerdos(dia: date | None = None) -> dict:
    return enviar_recordatorios_automaticos(dia)


def acuerdos_con_disparo_hoy(dia: date | None = None) -> list[Acuerdo]:
    dia = dia or timezone.localdate()
    acuerdos = acuerdos_candidatos(dia)
    empresa_ids = {a.empresa_id for a in acuerdos if a.empresa_id}
    reglas_map = reglas_activas_por_empresa(empresa_ids)
    return [
        a
        for a in acuerdos
        if disparos_para_acuerdo(a, dia, reglas_map.get(a.empresa_id, []))
    ]
