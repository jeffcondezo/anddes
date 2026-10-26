# ADR-0005 — Portal cliente: indicadores + actividades/acuerdos en solo lectura

- **Estado:** Aceptada  
- **Fecha:** 2025-10  

## Contexto

El cliente necesitaba el mismo nivel de visibilidad de avance (incl. glosario) y de la operación programada, sin poder alterar actividades ni acuerdos.

## Decisión

- Reutilizar `indicadores_empresa` (todas las pestañas) para la empresa del cliente (“Avance”).  
- Abrir calendario de actividades y bandeja de acuerdos filtrados a alcance `CON_CLIENTE`, UI **solo lectura**.  
- Comentarios / respuesta al avance: conversación de documento (`documento_conversacion`), no edición de acuerdos.

## Alternativas consideradas

1. Duplicar un dashboard cliente aparte — más coste de mantenimiento.  
2. Permitir al cliente crear/editar acuerdos — fuera del rol acordado.

## Consecuencias

- Decorador `gestor_admin_o_cliente_required` en vistas de lectura compartidas.  
- Plantillas condicionadas con `solo_lectura` / `es_cliente`.  
- Seguimiento interno (aprobaciones) sigue restringido a gestor/admin.
