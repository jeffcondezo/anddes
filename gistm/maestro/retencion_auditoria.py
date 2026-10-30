"""
Retención de registros de auditoría (Anexo / punto 2.1).

Política: conservar 1 año. El borrado solo lo hace el job de retención
(QuerySet.delete), no la UI ni el delete() de instancia.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TypedDict

from django.utils import timezone

from .models import RegistroAuditoria

# Límite acordado con el cliente (12-oct nº11 / contraste 2.1).
DIAS_RETENCION_AUDITORIA = 365


class ResultadoPurgaAuditoria(TypedDict):
    limite: object
    eliminados: int
    dry_run: bool


def purgar_auditoria_antigua(
    *,
    dias: int = DIAS_RETENCION_AUDITORIA,
    dry_run: bool = False,
) -> ResultadoPurgaAuditoria:
    """
    Elimina RegistroAuditoria con creado_en anterior a (ahora - dias).

    Usa borrado masivo por queryset (no llama a RegistroAuditoria.delete(),
    que está bloqueado a propósito en la aplicación).
    """
    if dias < 1:
        raise ValueError("dias de retención debe ser >= 1")
    limite = timezone.now() - timedelta(days=dias)
    qs = RegistroAuditoria.objects.filter(creado_en__lt=limite)
    count = qs.count()
    if not dry_run and count:
        # Bulk SQL: no invoca el delete() de instancia (que lanza ValidationError).
        qs.delete()
    return {
        "limite": limite,
        "eliminados": count,
        "dry_run": dry_run,
    }
