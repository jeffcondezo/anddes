# Despliegue y operación

## Entorno productivo (referencia)

- Ruta típica VPS: `/var/www/html/anddes/`
- App Django: `/var/www/html/anddes/gistm/`
- Python: **3.13.x del sistema** (p. ej. 3.13.15) — **sin venv** en producción; `pip` con `--break-system-packages`.
- PostgreSQL: **16**
- Servidor HTTP: **Apache 2 + mod_wsgi** (pip `mod_wsgi` contra ese Python 3.13)
- SO: **Ubuntu 24.04 LTS**
- Fase actual: **HTTP por IP** (sin dominio ni SSL hasta que Anddes los provea)
- Zona horaria Django: `America/Lima` (`TIME_ZONE` en settings)
- El crontab usa la **hora del sistema operativo** (si el server está en UTC, ajustar la hora del cron)

### Script de bootstrap (Ubuntu 24.04)

[`deploy/bootstrap-ubuntu-24.04.sh`](../deploy/bootstrap-ubuntu-24.04.sh) deja la app instalada y sirviendo por **HTTP en la IP**. También puede instalar el runtime:

1. Pregunta (o `--ip`) la IP → `DJANGO_ALLOWED_HOSTS` / CSRF / Entra redirect provisional  
2. **Python 3.13** vía PPA [deadsnakes](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa) si no está  
3. **PostgreSQL 16** (`postgresql-16` / `postgresql` en Ubuntu 24.04) si no está  
4. apt build/Apache + `pip` al **python3.13 del sistema** (sin venv)  
5. Crea rol/BD si hay `DB_*` en `.env`  
6. migrate + collectstatic  
7. Apache + mod_wsgi en **:80**  

```bash
cd /var/www/html/anddes
chmod +x deploy/bootstrap-ubuntu-24.04.sh
# Completar en .env al menos DB_* y Azure (Blob/ACS/Entra) si aplica
sudo ./deploy/bootstrap-ubuntu-24.04.sh --ip 203.0.113.10
```

Si Python/Postgres ya están instalados a mano: `--skip-python` / `--skip-postgres`.  
Acceso: `http://<IP>/`. Opciones: `--help`. Plantilla: [`deploy/apache/gistm-site.conf.template`](../deploy/apache/gistm-site.conf.template).

**Nota:** deadsnakes no es el repo oficial de Ubuntu; se usa porque 24.04 trae 3.12 por defecto y el proyecto fija 3.13. No sustituye `/usr/bin/python3` del sistema.

Cuando haya dominio y certificado **corporativo**: actualizar hosts, CSRF, `AZURE_AD_REDIRECT_URI` y el VirtualHost (este flujo no usa certbot).

## Variables de entorno

Plantilla: [`.env.example`](../.env.example).

| Variable | Fase actual (IP / HTTP) | Cuando haya dominio + TLS |
|----------|-------------------------|---------------------------|
| `DJANGO_SECRET_KEY` | Obligatorio (el bootstrap puede generarlo) | Igual |
| `DJANGO_DEBUG` | `False` | `False` |
| `DJANGO_ALLOWED_HOSTS` | IP del servidor (+ localhost) | Dominio(s) público(s) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `http://<IP>` | `https://tudominio.com` |
| `DJANGO_SECURE_SSL_REDIRECT` | `False` (bootstrap) | `True` con HTTPS |
| `DB_*` | Obligatorio | Igual |
| `AZURE_AD_*` | Obligatorio para SSO | Ajustar `REDIRECT_URI` a HTTPS |
| `AZURE_STORAGE_*` / ACS / `DEFAULT_FROM_EMAIL` | Obligatorio | Connection string **con AccountKey** (no SAS de cuenta sola). URLs = SAS por blob, solo lectura, ~15 min (`AZURE_STORAGE_URL_EXPIRE_SECS`) |

No versionar `.env` ni secretos.

## Seguridad HTTP

### Fase actual (HTTP por IP)

El bootstrap deja `DJANGO_SECURE_SSL_REDIRECT=False`. Cookies `Secure` / HSTS de Django con `DEBUG=False` pueden impedir sesión por HTTP puro: si el login falla en la IP, desactivar temporalmente el bloque `SECURE_*` vía env o usar solo la fase con TLS cuando exista certificado.

CSP (nonces, sin `'unsafe-inline'` en script/style) sí aplica:

- Chart.js y **Montserrat** desde estáticos propios (sin Google Fonts ni jsDelivr)
- Ver [librerias-frontend.md](librerias-frontend.md), [ADR-0007](decisiones/ADR-0007-seguridad-http-produccion.md)

### Cuando exista HTTPS (dominio + cert)

Con `DEBUG=False` Django activa cookies Secure, HSTS, SSL redirect, nosniff, `X-Frame-Options=DENY`. Apache termina TLS (o un proxy delante); si hay proxy: `DJANGO_SECURE_PROXY_SSL=True` y cabecera `X-Forwarded-Proto: https`.

## Dependencias

| Archivo | Rol |
|---------|-----|
| `requirements.in` | Dependencias **directas** (editable) |
| `requirements.txt` | Incluye `-r requirements.in` (desarrollo / local) |
| `requirements.lock.txt` | **Lock** directas + transitivas (producción) |

### psycopg[c] (producción)

Acordado en lista de librerías rev2 (27-ago): **no** `psycopg[binary]` en el servidor. Compila contra libpq (`libpq-dev`, `python3.13-dev`).

En **Windows local** para tests se puede usar `psycopg[binary]` en un venv de desarrollo; el lock de producción sigue con `psycopg-c`.

### Instalación producción

Preferir el script bootstrap (incluye deadsnakes + PostgreSQL 16). Equivalente manual:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update
sudo apt-get install -y build-essential python3.13 python3.13-dev libpq-dev \
  postgresql-16 postgresql-contrib apache2 apache2-dev
python3.13 -m pip install --break-system-packages --ignore-installed -r requirements.lock.txt
```

Regenerar lock (Linux con libpq):

```bash
python3.13 -m pip install --break-system-packages -U pip
python3.13 -m pip install --break-system-packages --ignore-installed -r requirements.in
python3.13 -m pip freeze | grep -v '^wheel==' > requirements.lock.txt
```

**Soporte Django 5.2 LTS:** seguridad extendida hasta **abril 2028** (ver [librerias.md](librerias.md)). Inventario licencias: [librerias.md](librerias.md).

## Migraciones

```bash
cd /var/www/html/anddes/gistm
python3.13 manage.py migrate
python3.13 manage.py collectstatic --noinput
python3.13 manage.py check
```

## Cron diario

Plantilla: [`deploy/cron.example`](../deploy/cron.example) (usa `python3.13` del sistema, no venv).

Ejemplo (servidor en **UTC**, disparo ≈ **07:00 Lima** = 12:00 UTC):

```cron
0 12 * * * cd /var/www/html/anddes/gistm && /usr/bin/python3.13 manage.py procesar_actividades_diarias >> /var/log/gistm-cron.log 2>&1
```

```bash
sudo touch /var/log/gistm-cron.log
sudo chown www-data:www-data /var/log/gistm-cron.log   # o el usuario del cron
```

Prueba sin correos de acuerdos/caducidad:

```bash
python3.13 manage.py procesar_actividades_diarias --sin-acuerdos
```

## Checklist post-despliegue

- [ ] Bootstrap ejecutado con IP correcta (`http://<IP>/` responde)
- [ ] `.env` con DB + Blob + ACS + Entra
- [ ] `migrate` / `collectstatic` / `check` OK
- [ ] Login Entra (redirect provisional `http://<IP>/auth/callback/` registrado en Azure si aplica)
- [ ] Subida de evidencia a Blob (connection string con AccountKey; no SAS de cuenta)
- [ ] Abrir evidencia → URL firmada por blob, solo lectura, caduca en ~15 min
- [ ] Correo de prueba
- [ ] Crontab con `python3.13` del sistema
- [ ] Pendiente Anddes: dominio + TLS + actualizar hosts/CSRF/Entra/Apache

## Troubleshooting frecuente

| Síntoma | Causa habitual | Acción |
|---------|----------------|--------|
| Error `AzureStorage` / `ParamSpec` / `_cffi_backend` | Mezcla apt + pip | `pip install --break-system-packages --ignore-installed -r requirements.lock.txt` |
| `psycopg_c` / libpq | Falta `libpq-dev` / headers | Instalar `libpq-dev python3.13-dev` y reinstalar lock |
| Apache 500 / WSGI | mod_wsgi de apt (otra versión Python) | Usar `wsgi_gistm.load` del bootstrap (`mod_wsgi-express module-config`) |
| Cron sin log | Permisos en `/var/log` | `touch` + `chown` |
| Correos a hora incorrecta | Server UTC vs Lima | Ajustar cron o TZ del OS |
| Aprobación no “pega” | Vigencia obligatoria | Completar pantalla de vigencia |
| Cookies / login roto en HTTP | `SECURE_*` con HTTPS forzado | Mantener `DJANGO_SECURE_SSL_REDIRECT=False` hasta tener TLS |
