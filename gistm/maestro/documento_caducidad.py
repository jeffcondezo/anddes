from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone

from .actividad_mail import _enviar, destinatarios_cliente
from .acuerdo_recordatorios import reglas_activas_por_empresa
from .models import (
    DocumentoCaducidadEnvio,
    EmpresaDocumento,
    EstadoRevisionCarga,
    NOTIFICACION_ACUERDO_DIA_SISTEMA,
    TipoCorreoEnvio,
    TipoNotificacionAcuerdo,
)


def _ya_enviado(doc: EmpresaDocumento, *, tipo: str, dias: int, dia: date) -> bool:
    return DocumentoCaducidadEnvio.objects.filter(
        empresa_documento=doc,
        tipo=tipo,
        dias=dias,
        dia_referencia=dia,
    ).exists()


def _registrar(doc: EmpresaDocumento, *, tipo: str, dias: int, dia: date) -> None:
    DocumentoCaducidadEnvio.objects.create(
        empresa_documento=doc,
        tipo=tipo,
        dias=dias,
        dia_referencia=dia,
    )


def documentos_con_caducidad() -> list[EmpresaDocumento]:
    return list(
        EmpresaDocumento.objects.filter(
            activo=True,
            fecha_caducidad__isnull=False,
            revision__empresa__isnull=False,
            revision__empresa__activo=True,
            revision__empresa__notificaciones_activas=True,
            cargas__activo=True,
            cargas__estado_revision=EstadoRevisionCarga.APROBADO,
        )
        .select_related("revision", "revision__empresa")
        .distinct()
    )


def disparos_caducidad(
    doc: EmpresaDocumento,
    dia: date,
    reglas,
) -> list[tuple[str, int]]:
    """
    Devuelve lista de (tipo, dias) a disparar hoy.
    Incluye siempre ANTES/0 el día de caducidad; reglas de empresa son días exactos extra.
    """
    cad = doc.fecha_caducidad
    if cad is None:
        return []
    out: list[tuple[str, int]] = []
    # Sistema: día de caducidad
    if cad == dia and not _ya_enviado(
        doc, tipo=TipoNotificacionAcuerdo.ANTES, dias=NOTIFICACION_ACUERDO_DIA_SISTEMA, dia=dia
    ):
        out.append((TipoNotificacionAcuerdo.ANTES, NOTIFICACION_ACUERDO_DIA_SISTEMA))
    for regla in reglas:
        if regla.dias == NOTIFICACION_ACUERDO_DIA_SISTEMA:
            continue
        if regla.tipo == TipoNotificacionAcuerdo.ANTES:
            if cad - timedelta(days=regla.dias) == dia and not _ya_enviado(
                doc, tipo=regla.tipo, dias=regla.dias, dia=dia
            ):
                out.append((regla.tipo, regla.dias))
        elif regla.tipo == TipoNotificacionAcuerdo.POST:
            if cad + timedelta(days=regla.dias) == dia and not _ya_enviado(
                doc, tipo=regla.tipo, dias=regla.dias, dia=dia
            ):
                out.append((regla.tipo, regla.dias))
    return out


def _cuerpo_caducidad(doc: EmpresaDocumento, *, tipo: str, dias: int) -> tuple[str, str]:
    empresa = doc.revision.empresa
    cad = doc.fecha_caducidad.strftime("%d/%m/%Y") if doc.fecha_caducidad else "—"
    if tipo == TipoNotificacionAcuerdo.ANTES and dias == 0:
        asunto = f"[GISTM] Documento caduca hoy · {doc.codigo} · {empresa.nombre}"
        intro = f"El documento {doc.codigo} ({doc.nombre}) caduca hoy ({cad})."
    elif tipo == TipoNotificacionAcuerdo.ANTES:
        asunto = (
            f"[GISTM] Documento por caducar ({dias} d) · {doc.codigo} · {empresa.nombre}"
        )
        intro = (
            f"El documento {doc.codigo} ({doc.nombre}) caduca en {dias} día(s) "
            f"({cad})."
        )
    else:
        asunto = (
            f"[GISTM] Documento caducado ({dias} d) · {doc.codigo} · {empresa.nombre}"
        )
        intro = (
            f"El documento {doc.codigo} ({doc.nombre}) caducó el {cad} "
            f"(hace {dias} día(s))."
        )
    frecuencia = doc.get_frecuencia_actualizacion_display() or "—"
    ref = (
        doc.fecha_ultima_revision.strftime("%d/%m/%Y")
        if doc.fecha_ultima_revision
        else "—"
    )
    cuerpo = (
        f"Hola,\n\n{intro}\n\n"
        f"Frecuencia de actualización: {frecuencia}\n"
        f"Rev. 0: {ref}\n"
        f"Fecha de caducidad: {cad}\n\n"
        "Por favor ingresen a GISTM y carguen una versión actualizada del documento.\n\n"
        "Saludos,\nAnddes · GISTM\n"
    )
    return asunto, cuerpo


def enviar_recordatorios_caducidad(dia: date | None = None) -> dict:
    """Recordatorios de caducidad de documentos aprobados (mismas reglas ANTES/POST)."""
    dia = dia or timezone.localdate()
    enviados = 0
    omitidos = 0
    errores: list[str] = []

    docs = documentos_con_caducidad()
    empresa_ids = {d.revision.empresa_id for d in docs if d.revision.empresa_id}
    reglas_map = reglas_activas_por_empresa(empresa_ids)

    for doc in docs:
        empresa = doc.revision.empresa
        if not empresa or not empresa.notificaciones_activas:
            omitidos += 1
            continue
        reglas = reglas_map.get(empresa.pk, [])
        for tipo, dias in disparos_caducidad(doc, dia, reglas):
            try:
                destinatarios = destinatarios_cliente(empresa.pk)
                if not destinatarios:
                    omitidos += 1
                    continue
                asunto, cuerpo = _cuerpo_caducidad(doc, tipo=tipo, dias=dias)
                _enviar(
                    asunto,
                    cuerpo,
                    destinatarios,
                    tipo=TipoCorreoEnvio.CADUCIDAD,
                    empresa=empresa,
                    automatico=True,
                )
                _registrar(doc, tipo=tipo, dias=dias, dia=dia)
                enviados += 1
            except Exception as exc:  # noqa: BLE001
                errores.append(f"Doc {doc.pk} {tipo}/{dias}: {exc}")

    return {"enviados": enviados, "omitidos": omitidos, "errores": errores}
