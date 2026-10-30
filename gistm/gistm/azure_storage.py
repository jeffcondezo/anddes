"""Backend Azure Blob con SAS por archivo (solo lectura, corta duración).

No reutiliza SharedAccessSignature de cuenta en las URLs de descarga: quien
obtenga un enlace no debe poder listar, escribir o borrar otros blobs.
"""
from __future__ import annotations

from storages.backends.azure_storage import AzureStorage


# Duración por defecto de los enlaces de evidencia (15 minutos).
DEFAULT_BLOB_SAS_SECONDS = 900


class GistmAzureStorage(AzureStorage):
    """
    Genera siempre `generate_blob_sas` a nivel de blob con permiso de lectura.

    Requiere AccountKey (connection string) o token_credential (Managed Identity).
    Una connection string solo con SharedAccessSignature de cuenta no sirve para
    firmar SAS de blob y se rechaza al pedir URL.
    """

    def url(self, name, expire=None, parameters=None, mode="r"):
        # Las URLs públicas de evidencia son siempre de solo lectura.
        mode = "r"
        if expire is None:
            expire = self.expiration_secs or DEFAULT_BLOB_SAS_SECONDS
        if not expire or int(expire) <= 0:
            expire = DEFAULT_BLOB_SAS_SECONDS

        if not self.account_key and self.token_credential is None:
            raise ValueError(
                "No se puede firmar SAS de blob de solo lectura: falta AccountKey "
                "o token_credential (Managed Identity). No use una connection string "
                "que solo contenga SharedAccessSignature de cuenta para servir evidencias."
            )
        return super().url(name, expire=expire, parameters=parameters, mode=mode)
