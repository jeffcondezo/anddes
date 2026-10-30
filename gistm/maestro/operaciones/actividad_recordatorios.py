from __future__ import annotations

from datetime import date

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from ..models import (
    Actividad,
    AvisoConsultor,
    EstadoActividad,
    TipoAvisoConsultor,
    fecha_vencimiento_actividad,
    sincronizar_actividades_pasadas,
)

# Días antes del vencimiento en los que se genera aviso (1 = mañana, 0 = hoy).
AVISO_DIAS_ANTES = tuple(
    getattr(settings, "ACTIVIDAD_AVISO_DIAS_ANTES", (1, 0))
)


def _url_actividad(actividad: Actividad) -> str:
    return reverse("maestro:actividad_detalle", args=[actividad.pk])


def _destinatarios_actividad(actividad: Actividad) -> list[int]:
    """Asignado y, si difiere, también el creador."""
    ids: list[int] = []
    if actividad.asignado_a_id:
        ids.append(actividad.asignado_a_id)
    if actividad.creado_por_id and actividad.creado_por_id not in ids:
        ids.append(actividad.creado_por_id)
    return ids


def _crear_avisos_actividad(
    *,
    actividad: Actividad,
    tipo: str,
    titulo: str,
    detalle: str,
) -> int:
    creados = 0
    for user_id in _destinatarios_actividad(actividad):
        AvisoConsultor.objects.create(
            destinatario_id=user_id,
            empresa_id=actividad.empresa_id,
            tipo=tipo,
            titulo=titulo,
            detalle=detalle,
            url=_url_actividad(actividad),
        )
        creados += 1
    return creados


def generar_avisos_actividades_proximas(*, dia: date | None = None) -> dict:
    """
    Crea avisos in-app para actividades PROGRAMADA que vencen en N días
    (por defecto: 1 día antes y el día del vencimiento).
    """
    dia = dia or timezone.localdate()
    umbrales = set(AVISO_DIAS_ANTES)
    creados = 0
    omitidos = 0
    qs = (
        Actividad.objects.filter(estado=EstadoActividad.PROGRAMADA)
        .select_related("empresa", "creado_por", "asignado_a", "area", "responsable")
        .order_by("inicio")
    )
    for act in qs:
        venc = fecha_vencimiento_actividad(act)
        if venc is None:
            omitidos += 1
            continue
        dias_antes = (venc - dia).days
        if dias_antes not in umbrales:
            continue
        # Evitar reenviar el mismo umbral (o uno más lejano ya notificado).
        if (
            act.ultimo_dias_antes_aviso is not None
            and act.ultimo_dias_antes_aviso <= dias_antes
        ):
            omitidos += 1
            continue

        if dias_antes == 0:
            titulo = f"Actividad vence hoy · {act.titulo}"
            cuando = "hoy"
        elif dias_antes == 1:
            titulo = f"Actividad vence mañana · {act.titulo}"
            cuando = "mañana"
        else:
            titulo = f"Actividad por vencer ({dias_antes} días) · {act.titulo}"
            cuando = f"en {dias_antes} días"

        partes = [
            f"La actividad «{act.titulo}» vence {cuando} ({venc.strftime('%d/%m/%Y')}).",
        ]
        if act.empresa_id:
            partes.append(f"Empresa: {act.empresa.nombre}.")
        if act.area_id:
            partes.append(f"Área: {act.area.nombre}.")
        if act.responsable_id:
            partes.append(f"Responsable: {act.responsable.nombre}.")
        if act.asignado_a_id:
            nombre = (
                act.asignado_a.get_full_name() or act.asignado_a.email or act.asignado_a.username
            )
            partes.append(f"Asignado a: {nombre}.")
        detalle = " ".join(partes)

        n = _crear_avisos_actividad(
            actividad=act,
            tipo=TipoAvisoConsultor.ACTIVIDAD_PROXIMA,
            titulo=titulo[:255],
            detalle=detalle,
        )
        if n:
            act.ultimo_dias_antes_aviso = dias_antes
            act.save(update_fields=["ultimo_dias_antes_aviso", "actualizado_en"])
            creados += n
        else:
            omitidos += 1

    return {"creados": creados, "omitidos": omitidos}


def generar_avisos_actividades_pasadas(ids: list[int]) -> int:
    """Avisos al pasar actividades a estado PASADA."""
    if not ids:
        return 0
    creados = 0
    qs = Actividad.objects.filter(pk__in=ids).select_related(
        "empresa", "creado_por", "asignado_a", "area", "responsable"
    )
    for act in qs:
        venc = fecha_vencimiento_actividad(act)
        venc_txt = venc.strftime("%d/%m/%Y") if venc else "—"
        partes = [
            f"La actividad «{act.titulo}» quedó pasada (vencimiento {venc_txt}).",
        ]
        if act.empresa_id:
            partes.append(f"Empresa: {act.empresa.nombre}.")
        creados += _crear_avisos_actividad(
            actividad=act,
            tipo=TipoAvisoConsultor.ACTIVIDAD_PASADA,
            titulo=f"Actividad pasada · {act.titulo}"[:255],
            detalle=" ".join(partes),
        )
    return creados


def procesar_actividades_diarias(*, dia: date | None = None) -> dict:
    """
    Job diario:
      1) Marcar actividades PROGRAMADA → PASADA si ya venció el día.
      2) Marcar acuerdos ABIERTO → VENCIDO si ya pasó la fecha de compromiso.
      3) Avisos de actividades pasadas / próximas a vencer.
    """
    from ..models import sincronizar_acuerdos_vencidos

    ids_pasadas = sincronizar_actividades_pasadas()
    ids_acuerdos = sincronizar_acuerdos_vencidos()
    avisos_pasadas = generar_avisos_actividades_pasadas(ids_pasadas)
    proximas = generar_avisos_actividades_proximas(dia=dia)
    return {
        "pasadas": len(ids_pasadas),
        "acuerdos_vencidos": len(ids_acuerdos),
        "avisos_pasadas": avisos_pasadas,
        "avisos_proximas": proximas["creados"],
        "omitidos_proximas": proximas["omitidos"],
    }
