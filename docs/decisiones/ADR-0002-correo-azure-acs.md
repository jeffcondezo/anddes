# ADR-0002 — Correo solo vía Azure Communication Services

- **Estado:** Aceptada  
- **Fecha:** 2025-10  

## Contexto

Se requieren correos transaccionales (actividades, acuerdos, caducidad, plantillas HTML con logo) sin mantener un SMTP propio.

## Decisión

Enviar **únicamente** por **Azure Communication Services Email**, con backend Django dedicado (`gistm.azure_email_backend`) que soporte `contentId`/CID para el logo.

## Alternativas consideradas

1. SMTP genérico / SendGrid — más proveedores que mantener.  
2. Solo texto plano sin plantillas — peor UX y branding Anddes.

## Consecuencias

- Configuración `AZURE_COMMUNICATION_*` y remitente verificado obligatorios.  
- Historial en `CorreoEnvio` (bandeja Recordatorios).  
- Logo embebido por CID; previsualización debe evitar romper el parseo de plantillas.
