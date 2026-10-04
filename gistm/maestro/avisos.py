from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import Q
from django.urls import reverse

from .models import (
    AvisoConsultor,
    DocumentoCarga,
    Empresa,
    EmpresaDocumento,
    PerfilUsuario,
    TipoAvisoConsultor,
    TipoUsuario,
)

User = get_user_model()


def destinatarios_consultor(empresa_id: int, *, excluir_user=None) -> list:
    """Gestores asignados a la empresa + administradores activos."""
    qs = (
        PerfilUsuario.objects.filter(activo=True, user__is_active=True)
        .filter(
            Q(tipo=TipoUsuario.ADMINISTRADOR)
            | Q(tipo=TipoUsuario.GESTOR, empresas__pk=empresa_id)
        )
        .select_related("user")
        .distinct()
    )
    users = []
    seen: set[int] = set()
    for perfil in qs:
        user = perfil.user
        if not user or user.pk in seen:
            continue
        if excluir_user is not None and user.pk == getattr(excluir_user, "pk", None):
            continue
        seen.add(user.pk)
        users.append(user)
    return users


def _emails(users) -> list[str]:
    return sorted(
        {
            (u.email or "").strip()
            for u in users
            if (u.email or "").strip()
        }
    )


def _enviar_email_aviso(*, asunto: str, cuerpo: str, destinatarios: list[str]) -> None:
    if not destinatarios:
        return
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "noreply@anddes.com"
    send_mail(
        subject=asunto,
        message=cuerpo,
        from_email=from_email,
        recipient_list=destinatarios,
        fail_silently=True,
    )


def crear_avisos_consultor(
    *,
    empresa: Empresa,
    tipo: str,
    titulo: str,
    detalle: str = "",
    url: str = "",
    actor=None,
    empresa_documento: EmpresaDocumento | None = None,
    carga: DocumentoCarga | None = None,
    enviar_email: bool = True,
) -> list[AvisoConsultor]:
    users = destinatarios_consultor(empresa.pk, excluir_user=actor)
    if not users:
        return []

    avisos = [
        AvisoConsultor(
            destinatario=user,
            empresa=empresa,
            tipo=tipo,
            titulo=titulo,
            detalle=(detalle or "").strip(),
            url=(url or "")[:500],
            empresa_documento=empresa_documento,
            carga=carga,
        )
        for user in users
    ]
    creados = AvisoConsultor.objects.bulk_create(avisos)

    if enviar_email:
        emails = _emails(users)
        if emails:
            cuerpo = (
                f"{titulo}\n\n"
                f"Empresa: {empresa.nombre}\n"
                f"{detalle.strip()}\n\n"
                "Revisa el seguimiento en GISTM."
            )
            if url:
                sitio = getattr(settings, "SITE_URL", "").rstrip("/")
                if sitio and url.startswith("/"):
                    cuerpo += f"\n\nEnlace: {sitio}{url}"
                elif url.startswith("http"):
                    cuerpo += f"\n\nEnlace: {url}"
            _enviar_email_aviso(
                asunto=f"[GISTM] {titulo} · {empresa.nombre}",
                cuerpo=cuerpo,
                destinatarios=emails,
            )
    return creados


def avisar_nueva_carga(*, carga: DocumentoCarga, actor=None) -> list[AvisoConsultor]:
    doc = carga.empresa_documento
    empresa = doc.revision.empresa
    url = reverse(
        "maestro:seguimiento_empresa_revision",
        args=[empresa.pk],
    )
    nombre = carga.nombre_original or (
        carga.archivo.name.rsplit("/", 1)[-1] if carga.archivo else "archivo"
    )
    return crear_avisos_consultor(
        empresa=empresa,
        tipo=TipoAvisoConsultor.CARGA,
        titulo=f"Nueva evidencia · {doc.codigo}",
        detalle=f"{actor_label(actor)} cargó «{nombre}» para {doc.nombre}.",
        url=url,
        actor=actor,
        empresa_documento=doc,
        carga=carga,
    )


def avisar_documento_propio(
    *, documento: EmpresaDocumento, carga: DocumentoCarga | None, actor=None
) -> list[AvisoConsultor]:
    empresa = documento.revision.empresa
    url = reverse(
        "maestro:seguimiento_documento_asociar_criterios",
        args=[empresa.pk, documento.pk],
    )
    return crear_avisos_consultor(
        empresa=empresa,
        tipo=TipoAvisoConsultor.DOCUMENTO_PROPIO,
        titulo=f"Documento del cliente · {documento.codigo}",
        detalle=(
            f"{actor_label(actor)} registró «{documento.nombre}» con evidencia. "
            "Pendiente asociar criterios."
        ),
        url=url,
        actor=actor,
        empresa_documento=documento,
        carga=carga,
    )


def avisar_mensaje_cliente(
    *, documento: EmpresaDocumento, texto: str, actor=None
) -> list[AvisoConsultor]:
    empresa = documento.revision.empresa
    url = reverse("maestro:documento_conversacion", args=[documento.pk])
    preview = (texto or "").strip()
    if len(preview) > 220:
        preview = preview[:217] + "…"
    return crear_avisos_consultor(
        empresa=empresa,
        tipo=TipoAvisoConsultor.MENSAJE,
        titulo=f"Mensaje del cliente · {documento.codigo}",
        detalle=f"{actor_label(actor)}: {preview}",
        url=url,
        actor=actor,
        empresa_documento=documento,
    )


def actor_label(user) -> str:
    if not user:
        return "Cliente"
    nombre = (user.get_full_name() or "").strip()
    return nombre or user.email or user.username or "Cliente"


def contar_avisos_no_leidos(user) -> int:
    if not getattr(user, "is_authenticated", False):
        return 0
    return AvisoConsultor.objects.filter(destinatario=user, leido=False).count()


def avisos_recientes(user, *, limite: int = 6) -> list[AvisoConsultor]:
    if not getattr(user, "is_authenticated", False):
        return []
    return list(
        AvisoConsultor.objects.filter(destinatario=user)
        .select_related("empresa")
        .order_by("leido", "-creado_en")[:limite]
    )
