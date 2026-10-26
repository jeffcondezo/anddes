# GISTM — Monitoring Platform (Anddes)

**Código de proyecto:** `10PI.10.2025-0007_GISTM`  
**Producto:** plataforma web para seguimiento del estándar GISTM (Global Industry Standard on Tailings Management) en empresas mineras clientes de Anddes.

## Qué hace

- Catálogo GISTM (Tema → Principio → Requisito → Criterio → Pregunta) y revisiones por empresa.
- Carga y revisión de evidencias documentales (cliente / consultor).
- Indicadores de avance, glosario, plan de entrega y seguimiento operativo.
- Actividades y acuerdos (calendario, recordatorios, caducidad de documentos).
- Roles: Administrador, Gestor (consultor) y Cliente.

## Stack

| Capa | Tecnología |
|------|------------|
| Backend | Django **5.2.18** LTS (app `maestro`) · **psycopg[c]** en prod |
| BD | PostgreSQL **16** |
| Auth | Microsoft Entra ID (SSO) + perfiles internos |
| Archivos | Azure Blob Storage (`django-storages`) |
| Correo | Azure Communication Services (Email) |
| Frontend | Plantillas Django + CSS; Chart.js + Montserrat self-host |
| Prod HTTP | Ubuntu 24.04 · Apache + mod_wsgi · Python 3.13 sistema (sin venv) · HTTP por IP (TLS pendiente) |

## Documentación técnica (Anexo 01 §11.1)

La documentación **viva** del repositorio está en [`docs/`](docs/README.md):

| Documento | Contenido |
|-----------|-----------|
| [Arquitectura](docs/arquitectura.md) | Componentes, datos, integraciones |
| [Despliegue y operación](docs/despliegue.md) | Bootstrap, Apache, variables, cron, checklist |
| [Librerías Python](docs/librerias.md) | Inventario + licencias + soporte Django LTS |
| [Librerías frontend](docs/librerias-frontend.md) | Chart.js, Montserrat |
| [Bitácora de decisiones](docs/decisiones/README.md) | ADRs |
| [Diagramas](docs/diagramas/README.md) | Mermaid editables |

## Arranque local (desarrollo)

En local sí se puede usar **venv** (en Windows hace falta `psycopg[binary]` porque `psycopg[c]` no compila sin libpq).

```bash
# 1. Dependencias
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
# Si falla psycopg[c] en Windows:
#   pip install "psycopg[binary]==3.3.4"

# 2. Variables de entorno
cp .env.example .env

# 3. Migraciones y servidor
cd gistm
python manage.py migrate
python manage.py runserver
```

## Producción (Ubuntu 24.04)

Sin venv; Apache HTTP por IP (sin dominio/SSL por ahora):

```bash
sudo ./deploy/bootstrap-ubuntu-24.04.sh --ip 203.0.113.10
```

Detalle: [`docs/despliegue.md`](docs/despliegue.md).

Requisitos prod: Python **3.13**, PostgreSQL **16**, Azure (Blob + ACS + Entra) según `.env.example`.

## Tests

```bash
cd gistm
python manage.py test maestro
```

## Comandos de operación relevantes

```bash
# Job diario (cron): estados + avisos + recordatorios de acuerdos/caducidad
python manage.py procesar_actividades_diarias

# Solo estados/avisos, sin correos de acuerdos/caducidad
python manage.py procesar_actividades_diarias --sin-acuerdos
```

Plantilla de crontab: [`deploy/cron.example`](deploy/cron.example).

## Remoto

- **GitLab (oficial Anddes):** `https://gitlab.com/anddesapi/10pi.10.2025-0007_gistm`

## Licencia / uso

Código y documentación para uso interno del proyecto Anddes / cliente GISTM. No publicar secretos (`.env`, connection strings, claves) en el repositorio.
