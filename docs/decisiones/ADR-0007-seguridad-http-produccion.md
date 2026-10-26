# ADR-0007 — Seguridad HTTP en producción (cookies, HSTS, CSP)

- **Estado:** Aceptada  
- **Fecha:** 2026-10-24  

## Contexto

La revisión / Anexo 01 señaló ausencia de ajustes de seguridad para producción: cookies seguras, HSTS y CSP.

## Decisión

1. Activar el paquete estándar de Django (`SECURE_*`, cookies Secure/HttpOnly/SameSite) cuando `DEBUG=False` y no es test.  
2. Middleware `ContentSecurityPolicyMiddleware` con **nonce por request** en `script-src` y `style-src` (sin `'unsafe-inline'`).  
3. Migrar handlers HTML a `data-confirm` / `data-autosubmit` y estilos UI a clases (`csp_inline.css`).  
4. Configurar `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS` por entorno.

## Alternativas consideradas

1. Solo nginx (headers en el proxy) — válido, pero no queda trazable en el código/repo.  
2. Externalizar todo el JS a estáticos — más limpio a largo plazo; nonces cubren el riesgo XSS ya.  
3. Dejar `'unsafe-inline'` en styles — rechazado tras migrar atributos a clases.

## Consecuencias

- Producción exige HTTPS bien configurado (o desactivar redirect/HSTS a propósito).  
- UI sin `style="..."` (salvo plantillas `email/` para clientes de correo).  
- `/admin/` queda fuera del CSP de la app (JS propio de Django admin).  
- Variables nuevas en `.env.example` y `docs/despliegue.md`.  
- Chart.js (indicadores) va vendorizado en `static/maestro/js/` (sin CDN jsDelivr).  
- Tipografía UI: Montserrat self-host (`static/maestro/fonts/`); CSP sin `fonts.googleapis.com` / `fonts.gstatic.com`.  
- Versiones y hashes: `docs/librerias-frontend.md`; inventario Python: `docs/librerias.md`.
