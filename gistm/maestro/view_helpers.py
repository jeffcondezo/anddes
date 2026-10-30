"""Helpers compartidos entre módulos de vistas (refactor 4.2)."""

from __future__ import annotations

from django.http import HttpRequest
from django.urls import reverse

from .models import Empresa, Revision
from .progress import revision_activa_por_id


def rev_id_param(request: HttpRequest) -> int | None:
    raw = (request.GET.get("rev") or request.POST.get("rev") or "").strip()
    return int(raw) if raw.isdigit() else None


def revision_seguimiento(
    empresa: Empresa, request: HttpRequest | None = None, rev_id: int | None = None
) -> Revision | None:
    if rev_id is None and request is not None:
        rev_id = rev_id_param(request)
    return revision_activa_por_id(empresa.pk, rev_id)


def seguimiento_url(name: str, empresa_pk: int, revision: Revision | None = None) -> str:
    url = reverse(name, args=[empresa_pk])
    if revision is not None:
        return f"{url}?rev={revision.pk}"
    return url
