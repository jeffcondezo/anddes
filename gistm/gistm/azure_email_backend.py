"""
Backend ACS con soporte de imágenes inline (contentId / CID).

El paquete django-azure-communication-email envía adjuntos sin contentId,
así que ACS los trata como archivos adjuntos normales. Este backend lee el
header Content-ID del MIME y lo pasa a la API de Azure.
"""

from __future__ import annotations

from email.mime.base import MIMEBase
from typing import Any, Dict

from django_azure_communication_email.backend import ACEmailBackend
from django_azure_communication_email import attachment as acs_attachment


class EmailBackend(ACEmailBackend):
    """Igual que ACS, pero marca como inline los MIME con Content-ID."""

    @staticmethod
    def _build_attachment(file: acs_attachment.Attachment) -> Dict[str, Any]:
        converter = acs_attachment.get_converter(file)
        payload: Dict[str, Any] = {
            "name": converter.get_filename(),
            "contentType": converter.get_filetype(),
            "contentInBase64": converter.get_content(),
        }
        if isinstance(file, MIMEBase):
            content_id = (file.get("Content-ID") or "").strip()
            if content_id:
                # ACS espera el id sin < >; HTML usa cid:ese-id
                payload["contentId"] = content_id.strip("<>").strip()
        return payload
