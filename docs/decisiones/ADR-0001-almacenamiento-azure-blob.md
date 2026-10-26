# ADR-0001 — Evidencias solo en Azure Blob Storage

- **Estado:** Aceptada  
- **Fecha:** 2025-10  

## Contexto

Las evidencias GISTM deben persistir de forma durable, versionable y accesible desde el VPS sin depender del disco local del servidor de aplicación (rotaciones, escalado, backups).

## Decisión

Usar **Azure Blob Storage** como único backend de archivos en runtime (`django-storages` + `azure-storage-blob`). En tests, filesystem local para no tocar Blob.

## Alternativas consideradas

1. Disco local / `MEDIA_ROOT` — simple, pero frágil en despliegues y backups.  
2. S3 u otro object storage — viable, pero el ecosistema Anddes ya opera en Azure.

## Consecuencias

- Obligatoria `AZURE_STORAGE_CONNECTION_STRING` fuera de tests.  
- Dependencias Azure deben instalarse limpias (evitar conflictos con paquetes Debian).  
- Rutas de versión de carga incorporan identificadores únicos en Blob.
