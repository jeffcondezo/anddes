# ADR-0003 — Vigencia obligatoria para confirmar aprobación

- **Estado:** Aceptada  
- **Fecha:** 2025-10  

## Contexto

Aprobar un documento sin frecuencia de actualización ni fechas de Rev. 0 / caducidad dejaba el seguimiento incompleto para recordatorios de vigencia.

## Decisión

La aprobación (total) **no se confirma** hasta registrar vigencia. El flujo guarda una aprobación pendiente en sesión y redirige a la pantalla de vigencia; **Cancelar** no aplica la aprobación.

## Alternativas consideradas

1. Aprobar primero y pedir vigencia después (opcional) — se olvidaba con frecuencia.  
2. Campos de vigencia en el mismo formulario de revisión — formularios ya densos por pregunta.

## Consecuencias

- UX de dos pasos al aprobar.  
- Tests de aprobación deben completar el POST de vigencia.  
- Caducidad y notificaciones se apoyan en esos campos.
