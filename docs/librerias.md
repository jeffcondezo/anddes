# Inventario de librerías (Python + frontend)

Lista para Anexo / revisión de seguridad (Cláusula 3.16 — análisis de
componentes). Versiones fijadas en
[`requirements.lock.txt`](../requirements.lock.txt) (Python 3.13, 2026-10-25).

## Soporte Django 5.2 LTS

| Concepto | Valor |
|----------|--------|
| Versión fijada | **5.2.18** (último parche de seguridad de la rama 5.2 a la fecha del lock) |
| Fin soporte mainstream | 3 dic 2025 |
| Fin soporte seguridad / data-loss (extendido) | **abril 2028** |
| Referencia | [Download Django — supported versions](https://www.djangoproject.com/download/) |

La fecha de soporte de seguridad (**abr. 2028**) supera el horizonte típico de verificación del proyecto. Actualizar el pin al publicar nuevos parches 5.2.x.

## Python — uso directo (`requirements.in`)

| Paquete | Versión (lock) | Licencia | Notas |
|---------|----------------|----------|-------|
| Django | **5.2.18** | BSD-3-Clause | LTS; ver tabla de soporte arriba |
| psycopg + **psycopg-c** | 3.3.4 | **LGPL-3.0** | Variante **[c]** acordada (lista rev2 27-ago). No usar `psycopg-binary` en producción. Copyleft débil — registrar |
| python-dotenv | 1.2.2 | BSD-3-Clause | Variables `.env` |
| msal | 1.39.0 | MIT | Microsoft Entra ID |
| openpyxl | 3.1.5 | MIT | Excel relación documento–criterio |
| reportlab | 4.4.3 | BSD-3-Clause | PDFs |
| django-storages | 1.14.6 | BSD-3-Clause | Backend Azure Blob |
| azure-storage-blob | 12.26.0 | MIT | Evidencias en Blob |
| azure-communication-email | 1.1.0 | MIT | Correo ACS |
| django-azure-communication-email | 1.6.0 | MIT | Backend Django ACS |
| typing_extensions | 4.16.0 | PSF-2.0 | Pin por Python 3.13 / Azure |
| cffi | 2.1.1 | MIT | Pin por Azure / cryptography |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | Pin por Azure / MSAL |

## Python — uso indirecto (transitivas del lock)

| Paquete | Versión (lock) | Licencia | Traída por (aprox.) |
|---------|----------------|----------|---------------------|
| asgiref | 3.13.0 | BSD-3-Clause | Django |
| sqlparse | 0.6.0 | BSD-3-Clause | Django |
| tzdata | 2026.5 | Apache-2.0 | Django (Windows) |
| azure-core | 1.41.0 | MIT | Azure SDK |
| azure-identity | 1.26.0 | MIT | Azure SDK |
| isodate | 0.7.2 | BSD-3-Clause | azure-storage-blob |
| msal-extensions | 1.3.1 | MIT | msal / azure-identity |
| requests | 2.34.2 | Apache-2.0 | msal / azure |
| urllib3 | 2.8.0 | MIT | requests |
| idna | 3.20 | BSD-3-Clause | requests |
| charset-normalizer | 3.5.2 | MIT | requests |
| **certifi** | 2026.10.23 | **MPL-2.0** | requests; copyleft débil — registrar |
| PyJWT | 2.15.1 | MIT | msal |
| pycparser | 3.12 | BSD-3-Clause | cffi |
| et_xmlfile | 2.0.0 | MIT | openpyxl |
| pillow | 13.0.0 | HPND-like / PIL | reportlab |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | varias |

No hay GPL ni AGPL. LGPL (psycopg) y MPL (certifi) requieren registro en la lista aprobada (Anexo 01 §10).

## Frontend

Ver [librerias-frontend.md](librerias-frontend.md).

| Artefacto | Versión | Licencia | Origen |
|-----------|---------|----------|--------|
| Chart.js | 4.4.6 | MIT | Estático propio |
| Montserrat | fontsource 5.2.5 / OFL | SIL OFL 1.1 | Estático propio |

**Neo Sans**: pendiente de archivos con licencia corporativa Anddes.

## Regenerar el lock

En **Linux** (producción / CI; con `libpq-dev` y `python3.13-dev` para `psycopg[c]`), **sin venv**:

```bash
python3.13 -m pip install --break-system-packages -U pip
python3.13 -m pip install --break-system-packages --ignore-installed -r requirements.in
python3.13 -m pip freeze | grep -v '^wheel==' > requirements.lock.txt
```

En Windows local sin libpq no se compila `psycopg-c`; regenerar el lock en el VPS o CI Linux.
