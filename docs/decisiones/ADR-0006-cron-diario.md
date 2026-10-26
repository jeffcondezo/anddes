# ADR-0006 — Un job diario para estados, avisos y recordatorios

- **Estado:** Aceptada  
- **Fecha:** 2025-10  

## Contexto

Hay que sincronizar estados temporales (actividad pasada, acuerdo vencido), avisos in-app y correos basados en **días calendario**.

## Decisión

Un único comando `procesar_actividades_diarias` ejecutado **1×/día** (crontab). Opción `--sin-acuerdos` para pruebas sin correo de acuerdos/caducidad.

## Alternativas consideradas

1. Varios crons (uno por tipo) — más puntos de fallo y horarios que alinear.  
2. Frecuencia horaria — no aporta a reglas por día; sí a marcar PASADA/VENCIDO más pronto, trade-off menor.

## Consecuencias

- Hora del crontab = hora del SO (UTC vs Lima).  
- Documentado en `deploy/cron.example` y `docs/despliegue.md`.  
- Si se necesita estados más “en caliente”, se puede añadir una segunda corrida diaria sin cambiar el modelo de notificaciones.
