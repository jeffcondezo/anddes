# Arquitectura técnica

## Vista general

Aplicación monolítica Django (`gistm`) con un dominio de negocio principal en la app `maestro`. La UI es server-rendered (plantillas). La autenticación de personal Anddes usa Microsoft Entra ID; los perfiles de negocio (Administrador / Gestor / Cliente) viven en `PerfilUsuario`.

```
[Navegador]
    │
    ▼
[Apache + mod_wsgi] ──► [Django 5.2 / maestro] ──► [PostgreSQL 16]
    │
    ├── Auth Entra ID (MSAL)
    ├── Archivos → Azure Blob Storage
    └── Correo → Azure Communication Services
```

Producción: Ubuntu 24.04, Python 3.13 **sin venv**, HTTP por IP hasta dominio/TLS. Ver [despliegue.md](despliegue.md).

Diagrama editable: [diagramas/contexto.md](diagramas/contexto.md).

## Módulos de negocio (`maestro`)

| Área | Responsabilidad |
|------|-----------------|
| Catálogo GISTM | Tema, Principio, Requisito, Criterio, Pregunta (activo/inactivo) |
| Empresas / revisiones | Empresa, Área, Responsable, Revisión, documentos por revisión |
| Evidencias | `EmpresaDocumento`, `DocumentoCarga` (versiones en Blob), mensajes/conversación |
| Seguimiento | Revisión consultor, aprobación parcial por pregunta, vigencia obligatoria al aprobar |
| Indicadores | Dashboard por empresa (general, progreso, documentos, estándar, glosario) |
| Operación | Actividades (calendario), Acuerdos, avisos in-app, recordatorios |
| Notificaciones | Reglas ANTES/POST por empresa, plantillas HTML, historial `CorreoEnvio` |
| Portal cliente | Indicadores (lectura), documentos + comentarios, actividades/acuerdos solo lectura |

## Roles y permisos

| Rol | Alcance |
|-----|---------|
| Administrador | Todas las empresas; administración de usuarios/catálogo |
| Gestor | Empresas asignadas; seguimiento, operación, indicadores |
| Cliente | Solo su empresa; carga de evidencias, comentarios; visualiza indicadores/actividades/acuerdos compartidos (`CON_CLIENTE`) |

Reglas en `maestro/permissions.py`. Actividades `PERSONAL` (solo consultor) no son visibles al cliente.

## Persistencia

- Motor: PostgreSQL.
- Migraciones Django en `maestro/migrations/`.
- Archivos de evidencia: Blob Storage (no disco local en runtime productivo). En tests se usa filesystem local.

## Seguridad HTTP (producción)

Con `DEBUG=False`: cookies Secure, HSTS, SSL redirect, nosniff, `X-Frame-Options=DENY` y cabecera **Content-Security-Policy** (`ContentSecurityPolicyMiddleware`). Detalle: [despliegue.md](despliegue.md) y [ADR-0007](decisiones/ADR-0007-seguridad-http-produccion.md).

## Integraciones externas

| Servicio | Uso | Configuración |
|----------|-----|----------------|
| Microsoft Entra ID | SSO personal Anddes | `AZURE_AD_*` |
| Azure Blob Storage | Evidencias / PDFs | `AZURE_STORAGE_CONNECTION_STRING`, contenedor |
| Azure Communication Services | Correo transaccional | `AZURE_COMMUNICATION_*`, `DEFAULT_FROM_EMAIL` |

Backend de correo: `gistm.azure_email_backend` (CID/logo en plantillas).

## Jobs asíncronos / cron

Comando: `manage.py procesar_actividades_diarias` (1×/día).

1. Actividades `PROGRAMADA` vencidas → `PASADA`.
2. Acuerdos `ABIERTO` vencidos → `VENCIDO`.
3. Avisos in-app de actividades.
4. Correos de recordatorio de acuerdos (día 0 sistema + reglas empresa).
5. Correos de caducidad de documentos (mismas reglas ANTES/POST).

Ver [despliegue.md](despliegue.md) y [diagramas/flujo-cron.md](diagramas/flujo-cron.md).

## Estructura de carpetas relevante

```
gistm-anddes/
├── README.md
├── requirements.in / .txt / .lock.txt
├── .env.example
├── deploy/
│   ├── bootstrap-ubuntu-24.04.sh
│   ├── apache/gistm-site.conf.template
│   └── cron.example
├── docs/                 ← documentación viva
└── gistm/
    ├── manage.py
    ├── gistm/            ← settings, urls, backends Azure
    └── maestro/          ← dominio GISTM
```
