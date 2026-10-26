from __future__ import annotations

import logging
from typing import Any

from django.http import HttpRequest

from .models import Empresa, RegistroAuditoria, TipoAuditoria

logger = logging.getLogger(__name__)


def _client_ip(request: HttpRequest | None) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _user_agent(request: HttpRequest | None) -> str:
    if request is None:
        return ""
    return (request.META.get("HTTP_USER_AGENT") or "")[:1000]


def registrar_auditoria(
    *,
    tipo: str,
    detalle: str,
    request: HttpRequest | None = None,
    usuario=None,
    empresa: Empresa | None = None,
    objeto_tipo: str = "",
    objeto_id: int | None = None,
) -> RegistroAuditoria | None:
    """
    Inserta un evento de auditoría. Nunca propaga errores al flujo de negocio.
    """
    try:
        if usuario is None and request is not None:
            user = getattr(request, "user", None)
            if user is not None and getattr(user, "is_authenticated", False):
                usuario = user

        email = ""
        nombre = ""
        if usuario is not None:
            email = (getattr(usuario, "email", None) or getattr(usuario, "username", "") or "")[
                :254
            ]
            nombre = (usuario.get_full_name() or "").strip()[:255]

        empresa_nombre = ""
        if empresa is not None:
            empresa_nombre = (empresa.nombre or "")[:255]

        return RegistroAuditoria.objects.create(
            tipo=tipo,
            usuario=usuario,
            usuario_email=email,
            usuario_nombre=nombre,
            empresa=empresa,
            empresa_nombre=empresa_nombre,
            objeto_tipo=(objeto_tipo or "")[:64],
            objeto_id=objeto_id,
            detalle=(detalle or "")[:4000],
            ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo registrar auditoría tipo=%s", tipo)
        return None


def auditar_login(request: HttpRequest, *, usuario, empresa: Empresa | None = None) -> None:
    registrar_auditoria(
        tipo=TipoAuditoria.LOGIN,
        detalle="Inicio de sesión exitoso",
        request=request,
        usuario=usuario,
        empresa=empresa,
        objeto_tipo="user",
        objeto_id=getattr(usuario, "pk", None),
    )


def auditar_carga(request: HttpRequest, *, carga, detalle: str | None = None) -> None:
    doc = carga.empresa_documento
    empresa = doc.revision.empresa
    registrar_auditoria(
        tipo=TipoAuditoria.CARGA,
        detalle=detalle
        or (
            f"Evidencia v{carga.version} en {doc.codigo}"
            + (" (URL)" if carga.es_url else " (archivo)")
        ),
        request=request,
        empresa=empresa,
        objeto_tipo="documentocarga",
        objeto_id=carga.pk,
    )


def auditar_aprobacion(request: HttpRequest, *, carga, parcial: bool = False) -> None:
    doc = carga.empresa_documento
    registrar_auditoria(
        tipo=TipoAuditoria.APROBACION,
        detalle=(
            f"{'Aprobación parcial' if parcial else 'Aprobación'} de {doc.codigo}"
            f" · evidencia v{carga.version}"
        ),
        request=request,
        empresa=doc.revision.empresa,
        objeto_tipo="documentocarga",
        objeto_id=carga.pk,
    )


def auditar_rechazo(request: HttpRequest, *, carga) -> None:
    doc = carga.empresa_documento
    registrar_auditoria(
        tipo=TipoAuditoria.RECHAZO,
        detalle=f"Rechazo de {doc.codigo} · evidencia v{carga.version}",
        request=request,
        empresa=doc.revision.empresa,
        objeto_tipo="documentocarga",
        objeto_id=carga.pk,
    )


def auditar_edicion(
    request: HttpRequest,
    *,
    detalle: str,
    empresa: Empresa | None = None,
    objeto_tipo: str = "",
    objeto_id: int | None = None,
) -> None:
    registrar_auditoria(
        tipo=TipoAuditoria.EDICION,
        detalle=detalle,
        request=request,
        empresa=empresa,
        objeto_tipo=objeto_tipo,
        objeto_id=objeto_id,
    )


def auditar_descarga(request: HttpRequest, *, carga) -> None:
    doc = carga.empresa_documento
    registrar_auditoria(
        tipo=TipoAuditoria.DESCARGA,
        detalle=(
            f"Apertura/descarga de evidencia v{carga.version} · {doc.codigo}"
            + (" (URL)" if carga.es_url else "")
        ),
        request=request,
        empresa=doc.revision.empresa,
        objeto_tipo="documentocarga",
        objeto_id=carga.pk,
    )


def auditar_exportacion(
    request: HttpRequest,
    *,
    empresa: Empresa,
    detalle: str,
    objeto_tipo: str = "",
    objeto_id: int | None = None,
) -> None:
    registrar_auditoria(
        tipo=TipoAuditoria.EXPORTACION,
        detalle=detalle,
        request=request,
        empresa=empresa,
        objeto_tipo=objeto_tipo,
        objeto_id=objeto_id,
    )


def auditar_archivo(
    request: HttpRequest,
    *,
    detalle: str,
    empresa: Empresa | None = None,
    objeto_tipo: str = "",
    objeto_id: int | None = None,
) -> None:
    registrar_auditoria(
        tipo=TipoAuditoria.ARCHIVO,
        detalle=detalle,
        request=request,
        empresa=empresa,
        objeto_tipo=objeto_tipo,
        objeto_id=objeto_id,
    )
