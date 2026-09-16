from __future__ import annotations

from django.contrib.auth import get_user_model

from .models import DocumentoCarga, DocumentoMensaje, TipoDocumentoMensaje
from .permissions import usuario_es_administrador, usuario_es_gestor

User = get_user_model()


def autor_es_consultor(user) -> bool:
    return bool(usuario_es_administrador(user) or usuario_es_gestor(user))


def registrar_mensaje(
    *,
    empresa_documento,
    texto: str,
    autor=None,
    es_consultor: bool | None = None,
    tipo: str = TipoDocumentoMensaje.OBSERVACION,
    carga: DocumentoCarga | None = None,
) -> DocumentoMensaje:
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("El mensaje no puede estar vacío.")
    if es_consultor is None:
        es_consultor = autor_es_consultor(autor) if autor is not None else False
    return DocumentoMensaje.objects.create(
        empresa_documento=empresa_documento,
        autor=autor,
        es_consultor=es_consultor,
        tipo=tipo,
        texto=texto,
        carga=carga,
    )


def sembrar_mensaje_rechazo_si_falta(carga: DocumentoCarga) -> DocumentoMensaje | None:
    """Si hay motivo de rechazo y aún no hay mensaje RECHAZO ligado, lo crea."""
    motivo = (carga.motivo_rechazo or "").strip()
    if not motivo:
        return None
    existe = DocumentoMensaje.objects.filter(
        empresa_documento_id=carga.empresa_documento_id,
        tipo=TipoDocumentoMensaje.RECHAZO,
        carga=carga,
    ).exists()
    if existe:
        return None
    return registrar_mensaje(
        empresa_documento=carga.empresa_documento,
        texto=motivo,
        autor=carga.revisado_por,
        es_consultor=True,
        tipo=TipoDocumentoMensaje.RECHAZO,
        carga=carga,
    )
