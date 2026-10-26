# ADR-0004 — Reglas de notificación por días exactos (día 0 de sistema)

- **Estado:** Aceptada  
- **Fecha:** 2025-10  

## Contexto

Se necesitaban recordatorios de acuerdos (y caducidad de documentos) configurables por empresa, sin ambigüedad de “ventanas” de días.

## Decisión

- Reglas empresa: listas de **días exactos** ANTES y POST (separados por coma en UI).  
- El **día 0** (fecha de compromiso / evento) lo reserva el **sistema** (`NOTIFICACION_ACUERDO_DIA_SISTEMA`); no se configura como regla editable duplicada.  
- Flag `empresa.notificaciones_activas` corta el envío.

## Alternativas consideradas

1. Rangos “entre N y M días” — más complejo de auditar y de deduplicar.  
2. Solo recordatorio manual — insuficiente para operación diaria.

## Consecuencias

- Cron diario suficiente (mismas reglas por fecha calendario).  
- Tabla `AcuerdoNotificacionEnvio` evita reenvíos el mismo día/regla.  
- Acuerdos “solo consultor” no notifican a clientes.
