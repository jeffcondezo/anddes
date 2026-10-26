# Documentación técnica GISTM

Documentación **viva** del repositorio (Anexo 01 §11.1). Se actualiza junto con el código en cada entrega relevante.

## Índice

1. [Arquitectura](arquitectura.md) — módulos, modelo de datos, integraciones, roles.
2. [Despliegue y operación](despliegue.md) — entorno productivo, variables, cron, troubleshooting.
3. [Bitácora de decisiones técnicas](decisiones/README.md) — registro ADR (Architecture Decision Records).
4. [Diagramas](diagramas/README.md) — diagramas en Mermaid (formato editable en el repo).
5. [Librerías (Python + licencias)](librerias.md) — inventario directo/indirecto y copyleft débil.
6. [Librerías frontend](librerias-frontend.md) — Chart.js, Montserrat (versión, origen, hash).

## Cómo mantenerla

- Cambios de arquitectura o integraciones → actualizar `arquitectura.md` + ADR si aplica.
- Cambios de despliegue/cron/env → actualizar `despliegue.md`.
- Decisiones con alternativas descartadas → nuevo archivo `decisiones/ADR-NNNN-….md` y entrada en el índice.
- Flujos nuevos → diagramas Mermaid en `diagramas/` (no solo PNG/PDF).

## Convenciones

- Idioma: español (términos técnicos en inglés cuando sea el nombre canónico: Blob, Entra ID, ACS).
- No incluir secretos ni connection strings reales.
- Preferir Mermaid (`.md`) sobre capturas no editables.
