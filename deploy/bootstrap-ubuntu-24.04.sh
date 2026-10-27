#!/usr/bin/env bash
# =============================================================================
# GISTM — instalación en Ubuntu 24.04 LTS (producción, sin venv)
#
# Deja la app sirviendo por HTTP en la IP del servidor (sin dominio / sin SSL;
# eso queda pendiente de lo que provea Anddes).
#
# - Instala Python 3.13 (PPA deadsnakes) si falta
# - Instala PostgreSQL 16 (apt Ubuntu 24.04) si falta
# - requirements.lock.txt (Django 5.2.18, psycopg[c])
# - Apache 2 + mod_wsgi (HTTP :80)
# - Pide solo la IP → DJANGO_ALLOWED_HOSTS
#
# Uso:
#   chmod +x deploy/bootstrap-ubuntu-24.04.sh
#   sudo ./deploy/bootstrap-ubuntu-24.04.sh
#   sudo ./deploy/bootstrap-ubuntu-24.04.sh --ip 203.0.113.10
#
# Opciones:
#   --ip ADDR            IP del servidor (si no se pasa, se pregunta)
#   --app-root DIR       Raíz del repo (default: padre de deploy/)
#   --python BIN         default: python3.13
#   --skip-apt           No instalar paquetes apt (ni Python/Postgres vía apt)
#   --skip-python        No instalar/actualizar Python 3.13
#   --skip-postgres      No instalar PostgreSQL 16
#   --skip-db            No crear rol/BD PostgreSQL de la app
#   --skip-apache        No configurar Apache/WSGI
#   --no-pip             No reinstalar dependencias pip
#   --no-migrate         No ejecutar migrate/collectstatic
#   --wsgi-processes N   default: 2
#   --wsgi-threads N     default: 15
#   -h | --help
# =============================================================================
set -euo pipefail
# Si un comando falla, muestra línea (útil cuando parece “pararse” sin error).
trap 'die "Falló en línea ${LINENO} (comando: ${BASH_COMMAND})"' ERR

APP_ROOT=""
PYTHON_BIN="python3.13"
SERVER_IP=""
DO_APT=1
DO_PYTHON=1
DO_POSTGRES=1
DO_DB=1
DO_MIGRATE=1
DO_PIP=1
DO_APACHE=1
WSGI_PROCESSES=2
WSGI_THREADS=15

log()  { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,40p' "$0" | sed 's/^# \?//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ip)             SERVER_IP="${2:-}"; shift 2 ;;
    --app-root)       APP_ROOT="${2:-}"; shift 2 ;;
    --python)         PYTHON_BIN="${2:-}"; shift 2 ;;
    --skip-apt)       DO_APT=0; shift ;;
    --skip-python)    DO_PYTHON=0; shift ;;
    --skip-postgres)  DO_POSTGRES=0; shift ;;
    --skip-db)        DO_DB=0; shift ;;
    --skip-apache)    DO_APACHE=0; shift ;;
    --no-pip)         DO_PIP=0; shift ;;
    --no-migrate)     DO_MIGRATE=0; shift ;;
    --wsgi-processes) WSGI_PROCESSES="${2:-2}"; shift 2 ;;
    --wsgi-threads)   WSGI_THREADS="${2:-15}"; shift 2 ;;
    -h|--help)        usage ;;
    *) die "Opción desconocida: $1 (usa --help)" ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "$APP_ROOT" ]]; then
  APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi
APP_ROOT="$(cd "$APP_ROOT" && pwd)"
GISTM_DIR="${APP_ROOT}/gistm"
GISTM_PACKAGE_DIR="${GISTM_DIR}/gistm"
WSGI_FILE="${GISTM_PACKAGE_DIR}/wsgi.py"
LOCK_FILE="${APP_ROOT}/requirements.lock.txt"
ENV_FILE="${APP_ROOT}/.env"
ENV_EXAMPLE="${APP_ROOT}/.env.example"
APACHE_TEMPLATE="${SCRIPT_DIR}/apache/gistm-site.conf.template"
APACHE_SITE_AVAIL="/etc/apache2/sites-available/gistm.conf"
APACHE_WSGI_LOAD="/etc/apache2/mods-available/wsgi_gistm.load"
STATIC_ROOT_DEFAULT="${GISTM_DIR}/staticfiles"

[[ -d "$GISTM_DIR" ]] || die "No existe ${GISTM_DIR}. ¿--app-root incorrecto?"
[[ -f "$LOCK_FILE" ]] || die "Falta ${LOCK_FILE}"
[[ -f "$WSGI_FILE" ]] || die "Falta ${WSGI_FILE}"

SUDO=()
if [[ "$(id -u)" -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || die "Ejecuta como root o con sudo."
  SUDO=(sudo)
fi
run() { "${SUDO[@]}" "$@"; }

load_env_var() {
  # Siempre return 0: con set -e, un [[ fallido dentro de $(load_env_var …)
  # abortaba el script en silencio (p. ej. tras GRANT si no hay DJANGO_STATIC_ROOT).
  local key="$1" line
  [[ -f "$ENV_FILE" ]] || return 0
  line="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n1 || true)"
  if [[ -n "$line" ]]; then
    printf '%s' "${line#*=}"
  fi
  return 0
}

# Crea o reemplaza KEY=valor en .env (sin tocar el resto).
set_env_var() {
  local key="$1" value="$2"
  touch "$ENV_FILE"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    # Evitar sed -i portabilidad: reescribe archivo
    local tmp
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$value" '
      BEGIN { done=0 }
      $0 ~ "^" k "=" { print k "=" v; done=1; next }
      { print }
      END { if (!done) print k "=" v }
    ' "$ENV_FILE" > "$tmp"
    mv "$tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

is_ipv4() {
  local ip="$1"
  [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  local IFS=.
  # shellcheck disable=SC2086
  set -- $ip
  for o in "$1" "$2" "$3" "$4"; do
    [[ "$o" -ge 0 && "$o" -le 255 ]] || return 1
  done
  return 0
}

# -----------------------------------------------------------------------------
# IP del servidor → ALLOWED_HOSTS (sin dominio / sin SSL)
# -----------------------------------------------------------------------------
log "IP del servidor (HTTP, sin SSL ni dominio por ahora)"
if [[ -z "$SERVER_IP" ]]; then
  # Intento detectar IP primaria (no loopback)
  DETECTED="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  if [[ -n "$DETECTED" ]] && is_ipv4 "$DETECTED"; then
    printf "  Detectada: %s\n" "$DETECTED"
    read -r -p "  IP para ALLOWED_HOSTS [${DETECTED}]: " SERVER_IP || true
    SERVER_IP="${SERVER_IP:-$DETECTED}"
  else
    read -r -p "  IP del servidor (ej. 203.0.113.10): " SERVER_IP
  fi
fi
SERVER_IP="$(echo "$SERVER_IP" | tr -d '[:space:]')"
[[ -n "$SERVER_IP" ]] || die "Debes indicar la IP del servidor."
is_ipv4 "$SERVER_IP" || die "IP inválida: '${SERVER_IP}' (se espera IPv4, ej. 10.0.0.5)."
echo "  Usando IP: ${SERVER_IP}"
echo "  Acceso previsto: http://${SERVER_IP}/  (sin HTTPS hasta que provean dominio/certificado)"

# -----------------------------------------------------------------------------
# 1) Comprobaciones de SO
# -----------------------------------------------------------------------------
log "Comprobando Ubuntu"
if [[ -r /etc/os-release ]]; then
  # shellcheck source=/dev/null
  . /etc/os-release
  echo "  OS: ${PRETTY_NAME:-$ID $VERSION_ID}"
  [[ "${ID:-}" == "ubuntu" ]] || warn "Pensado para Ubuntu 24.04."
  [[ "${VERSION_ID:-}" == "24.04" ]] || warn "VERSION_ID=${VERSION_ID:-?} (esperado 24.04)."
else
  warn "No se pudo leer /etc/os-release."
fi

# -----------------------------------------------------------------------------
# 2) apt base + Python 3.13 + PostgreSQL 16
# -----------------------------------------------------------------------------
if [[ "$DO_APT" -eq 1 ]]; then
  # Export (no VAR=cmd vía run): como root SUDO=() y "DEBIAN_FRONTEND=…" sería el comando.
  export DEBIAN_FRONTEND=noninteractive
  log "Instalando paquetes base (build, Apache, libpq…)"
  run apt-get update -y
  run apt-get install -y \
    software-properties-common ca-certificates gnupg \
    build-essential pkg-config libssl-dev libffi-dev \
    libpq-dev git curl \
    apache2 apache2-dev

  if [[ "$DO_PYTHON" -eq 1 ]]; then
    log "Python 3.13 (PPA deadsnakes si hace falta)"
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
      echo "  '${PYTHON_BIN}' no encontrado → añadiendo ppa:deadsnakes/ppa"
      run add-apt-repository -y ppa:deadsnakes/ppa
      run apt-get update -y
    else
      echo "  Ya existe $(command -v "$PYTHON_BIN")"
    fi
    # Instalar/asegurar intérprete + headers (psycopg-c / mod_wsgi)
    run apt-get install -y \
      python3.13 python3.13-dev python3.13-venv
    # pip para este intérprete (sin sustituir /usr/bin/python3 del sistema)
    if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
      run "$PYTHON_BIN" -m ensurepip --upgrade || \
        run apt-get install -y python3-pip
    fi
    # Si ensurepip no registró pip en 3.13, get-pip
    if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
      # Solo HTTPS (origen y redirects): evita downgrade a HTTP (Sonar / supply-chain).
      curl -fsSL --proto '=https' --proto-redir '=https' --tlsv1.2 \
        https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
      run "$PYTHON_BIN" /tmp/get-pip.py --break-system-packages
      rm -f /tmp/get-pip.py
    fi
  else
    log "Omitiendo instalación de Python (--skip-python)"
  fi

  if [[ "$DO_POSTGRES" -eq 1 ]]; then
    log "PostgreSQL 16 (paquete Ubuntu 24.04)"
    # En noble, `postgresql` / `postgresql-16` aportan la serie 16
    if apt-cache show postgresql-16 >/dev/null 2>&1; then
      run apt-get install -y postgresql-16 postgresql-contrib
    else
      run apt-get install -y postgresql postgresql-contrib
    fi
    run systemctl enable --now postgresql
    # Esperar a que acepte conexiones locales
    pg_ok=0
    for _i in 1 2 3 4 5 6 7 8 9 10; do
      if [[ "$(id -u)" -eq 0 ]]; then
        if runuser -u postgres -- psql -c 'SELECT 1' >/dev/null 2>&1 || \
           su -s /bin/bash postgres -c "psql -c 'SELECT 1'" >/dev/null 2>&1; then
          pg_ok=1; break
        fi
      else
        if sudo -u postgres psql -c 'SELECT 1' >/dev/null 2>&1; then
          pg_ok=1; break
        fi
      fi
      sleep 1
    done
    [[ "$pg_ok" -eq 1 ]] || warn "PostgreSQL instalado pero aún no responde a psql; la creación de rol/BD puede fallar."
  else
    log "Omitiendo instalación de PostgreSQL (--skip-postgres)"
  fi
else
  log "Omitiendo apt (--skip-apt)"
fi

# -----------------------------------------------------------------------------
# 3) Verificar Python / Postgres tras instalación
# -----------------------------------------------------------------------------
log "Verificando Python / PostgreSQL"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "No está '${PYTHON_BIN}'. Instálalo o quita --skip-python."
PY_VER="$("$PYTHON_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
PY_PREFIX="$("$PYTHON_BIN" -c 'import sys; print(sys.prefix)')"
echo "  Python: ${PY_VER} ($(command -v "$PYTHON_BIN"))"
echo "  prefix: ${PY_PREFIX}"
case "$PY_VER" in 3.13.*) ;; *) warn "Se esperaba Python 3.13.x (tienes ${PY_VER})." ;; esac

if command -v psql >/dev/null 2>&1; then
  PG_VER_LINE="$(psql --version | head -n1)"
  echo "  ${PG_VER_LINE}"
  if ! echo "$PG_VER_LINE" | grep -qE ' 16(\.|$)'; then
    warn "Se esperaba PostgreSQL 16 (encontrado: ${PG_VER_LINE})."
  fi
elif [[ "$DO_POSTGRES" -eq 1 || "$DO_DB" -eq 1 ]]; then
  die "psql no está en PATH. Quita --skip-postgres o instala PostgreSQL a mano."
else
  warn "psql no está en PATH (omitido: --skip-postgres y --skip-db)."
fi

# -----------------------------------------------------------------------------
# 4) pip al sistema (sin venv)
# -----------------------------------------------------------------------------
if [[ "$DO_PIP" -eq 1 ]]; then
  log "Instalando requirements.lock.txt en ${PYTHON_BIN} (sistema)"
  run "$PYTHON_BIN" -m pip install -U pip wheel setuptools
  run "$PYTHON_BIN" -m pip install --break-system-packages --ignore-installed -r "$LOCK_FILE"
  "$PYTHON_BIN" -c "import django, psycopg; print('  Django', django.get_version()); print('  psycopg', psycopg.__version__)"
  "$PYTHON_BIN" -c "import psycopg_c" 2>/dev/null && echo "  psycopg-c: OK" || warn "psycopg_c no importó."
else
  log "Omitiendo pip (--no-pip)"
fi

# -----------------------------------------------------------------------------
# 5) .env + IP en ALLOWED_HOSTS
# -----------------------------------------------------------------------------
log "Configurando .env"
if [[ ! -f "$ENV_FILE" ]]; then
  [[ -f "$ENV_EXAMPLE" ]] || die "No hay .env.example"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  echo "  Creado desde .env.example"
fi

set_env_var "DJANGO_DEBUG" "False"
set_env_var "DJANGO_ALLOWED_HOSTS" "${SERVER_IP},localhost,127.0.0.1"
# CSRF con HTTP + IP (sin SSL por ahora)
set_env_var "DJANGO_CSRF_TRUSTED_ORIGINS" "http://${SERVER_IP},http://localhost,http://127.0.0.1"
# Redirect SSL off hasta que haya certificado
set_env_var "DJANGO_SECURE_SSL_REDIRECT" "False"
# HOST/PORT vacíos en .env rompen la conexión (socket + prompt de password).
if [[ -z "$(load_env_var DB_HOST)" ]]; then
  set_env_var "DB_HOST" "127.0.0.1"
  echo "  DB_HOST=127.0.0.1 (default)"
fi
if [[ -z "$(load_env_var DB_PORT)" ]]; then
  set_env_var "DB_PORT" "5432"
  echo "  DB_PORT=5432 (default)"
fi
# Entra redirect por IP (ajustar cuando haya dominio)
CURRENT_REDIRECT="$(load_env_var AZURE_AD_REDIRECT_URI)"
if [[ -z "$CURRENT_REDIRECT" || "$CURRENT_REDIRECT" == *"DOMINIO"* ]]; then
  set_env_var "AZURE_AD_REDIRECT_URI" "http://${SERVER_IP}/auth/callback/"
fi

if [[ -z "$(load_env_var DJANGO_SECRET_KEY)" ]]; then
  GEN_KEY="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(50))')"
  set_env_var "DJANGO_SECRET_KEY" "$GEN_KEY"
  echo "  Generado DJANGO_SECRET_KEY"
fi

echo "  DJANGO_ALLOWED_HOSTS=$(load_env_var DJANGO_ALLOWED_HOSTS)"
warn "Completa en .env si aún faltan: DB_*, Azure Blob, ACS, Entra (AZURE_AD_*)."

# -----------------------------------------------------------------------------
# 6) PostgreSQL (rol/BD de la app)
# -----------------------------------------------------------------------------
if [[ "$DO_DB" -eq 1 ]]; then
  log "PostgreSQL: rol/BD según .env"
  DB_NAME="$(load_env_var DB_NAME)"
  DB_USER="$(load_env_var DB_USER)"
  DB_PASSWORD="$(load_env_var DB_PASSWORD)"
  if [[ -z "$DB_NAME" || -z "$DB_USER" ]]; then
    warn "DB_NAME/DB_USER vacíos en .env — omitiendo creación de BD. Rellena .env y re-ejecuta sin --skip-db."
  else
    as_postgres() {
      if [[ "$(id -u)" -eq 0 ]]; then
        if command -v runuser >/dev/null 2>&1; then runuser -u postgres -- "$@"
        else su -s /bin/bash postgres -c "$(printf '%q ' "$@")"; fi
      else sudo -u postgres "$@"; fi
    }
    sql_quote() { printf "%s" "${1//\'/\'\'}"; }
    PW_SQL="$(sql_quote "$DB_PASSWORD")"
    if as_postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -qx 1; then
      echo "  Rol '${DB_USER}' ya existe."
      [[ -n "$DB_PASSWORD" ]] && as_postgres psql -v ON_ERROR_STOP=1 -c "ALTER USER \"${DB_USER}\" WITH PASSWORD '${PW_SQL}';"
    else
      as_postgres psql -v ON_ERROR_STOP=1 -c "CREATE USER \"${DB_USER}\" WITH PASSWORD '${PW_SQL}';"
      echo "  Rol '${DB_USER}' creado."
    fi
    if as_postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -qx 1; then
      echo "  Base '${DB_NAME}' ya existe."
    else
      as_postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"${DB_NAME}\" OWNER \"${DB_USER}\";"
      echo "  Base '${DB_NAME}' creada."
    fi
    as_postgres psql -v ON_ERROR_STOP=1 -c "GRANT ALL PRIVILEGES ON DATABASE \"${DB_NAME}\" TO \"${DB_USER}\";"
    as_postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" -c "GRANT ALL ON SCHEMA public TO \"${DB_USER}\";" || true
    echo "  Rol/BD listos."
  fi
else
  log "Omitiendo BD (--skip-db)"
fi

# -----------------------------------------------------------------------------
# 7) migrate / collectstatic
# -----------------------------------------------------------------------------
STATIC_ROOT="$(load_env_var DJANGO_STATIC_ROOT)"
STATIC_ROOT="${STATIC_ROOT:-$STATIC_ROOT_DEFAULT}"

if [[ "$DO_MIGRATE" -eq 1 ]]; then
  log "Django: migrate + collectstatic + check"
  echo "  DB_HOST=$(load_env_var DB_HOST) DB_PORT=$(load_env_var DB_PORT) DB_NAME=$(load_env_var DB_NAME)"
  if [[ -z "$(load_env_var AZURE_STORAGE_CONNECTION_STRING)" ]]; then
    warn "AZURE_STORAGE_CONNECTION_STRING vacío: migrate/check pueden fallar (Blob obligatorio fuera de tests)."
  fi
  cd "$GISTM_DIR"
  export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-gistm.settings}"
  export PYTHONUNBUFFERED=1
  # -u: salida inmediata (si migrate se cuelga, ya viste este paso)
  "$PYTHON_BIN" -u manage.py migrate --noinput
  "$PYTHON_BIN" -u manage.py collectstatic --noinput
  "$PYTHON_BIN" -u manage.py check || warn "manage.py check reportó avisos/errores."
else
  log "Omitiendo migrate (--no-migrate)"
fi

# -----------------------------------------------------------------------------
# 8) Apache HTTP :80 (sin SSL)
# -----------------------------------------------------------------------------
if [[ "$DO_APACHE" -eq 1 ]]; then
  log "Apache + mod_wsgi (solo HTTP :80, ServerName=${SERVER_IP})"
  [[ -f "$APACHE_TEMPLATE" ]] || die "Falta ${APACHE_TEMPLATE}"
  command -v apache2ctl >/dev/null 2>&1 || die "apache2 no instalado."

  run "$PYTHON_BIN" -m pip install --break-system-packages --upgrade "mod_wsgi>=5.0.0"
  EXPRESS="$("$PYTHON_BIN" -c 'import sysconfig; from pathlib import Path; print(Path(sysconfig.get_path("scripts")) / "mod_wsgi-express")')"
  [[ -x "$EXPRESS" ]] || EXPRESS="$(command -v mod_wsgi-express || true)"
  [[ -n "$EXPRESS" && -x "$EXPRESS" ]] || die "No se encontró mod_wsgi-express."
  MODULE_CONFIG="$("$EXPRESS" module-config)"
  [[ -n "$MODULE_CONFIG" ]] || die "module-config vacío."

  echo "$MODULE_CONFIG" | run tee "$APACHE_WSGI_LOAD" >/dev/null
  run a2dismod wsgi 2>/dev/null || true
  run a2dismod ssl 2>/dev/null || true
  run a2enmod headers
  run ln -sfn ../mods-available/wsgi_gistm.load /etc/apache2/mods-enabled/wsgi_gistm.load

  if [[ ! -d "$STATIC_ROOT" ]] || [[ -z "$(ls -A "$STATIC_ROOT" 2>/dev/null || true)" ]]; then
    cd "$GISTM_DIR"
    "$PYTHON_BIN" manage.py collectstatic --noinput
  fi

  TMP_SITE="$(mktemp)"
  sed \
    -e "s|{{LOAD_MODULE_LINES}}|# LoadModule: ${APACHE_WSGI_LOAD}|" \
    -e "s|{{SERVER_NAME}}|${SERVER_IP}|g" \
    -e "s|{{STATIC_ROOT}}|${STATIC_ROOT}|g" \
    -e "s|{{PYTHON_HOME}}|${PY_PREFIX}|g" \
    -e "s|{{GISTM_DIR}}|${GISTM_DIR}|g" \
    -e "s|{{GISTM_PACKAGE_DIR}}|${GISTM_PACKAGE_DIR}|g" \
    -e "s|{{WSGI_FILE}}|${WSGI_FILE}|g" \
    -e "s|{{WSGI_PROCESSES}}|${WSGI_PROCESSES}|g" \
    -e "s|{{WSGI_THREADS}}|${WSGI_THREADS}|g" \
    "$APACHE_TEMPLATE" > "$TMP_SITE"
  run cp "$TMP_SITE" "$APACHE_SITE_AVAIL"
  rm -f "$TMP_SITE"

  run chown -R root:www-data "$APP_ROOT"
  run find "$APP_ROOT" -type d -exec chmod 755 {} \;
  run find "$APP_ROOT" -type f -exec chmod 644 {} \;
  [[ -f "$ENV_FILE" ]] && run chmod 640 "$ENV_FILE"
  run mkdir -p "$STATIC_ROOT"
  run chown -R www-data:www-data "$STATIC_ROOT"
  [[ -d "${GISTM_DIR}/media" ]] && run chown -R www-data:www-data "${GISTM_DIR}/media"

  run a2dissite 000-default 2>/dev/null || true
  run a2ensite gistm
  run apache2ctl configtest
  run systemctl enable apache2
  run systemctl restart apache2
else
  log "Omitiendo Apache (--skip-apache)"
fi

# -----------------------------------------------------------------------------
log "Instalación lista (HTTP por IP, sin SSL)"
cat <<EOF
  URL        : http://${SERVER_IP}/
  ALLOWED    : $(load_env_var DJANGO_ALLOWED_HOSTS)
  App        : ${APP_ROOT}
  Python     : ${PY_VER} (sistema, sin venv)
  Apache     : $([[ "$DO_APACHE" -eq 1 ]] && echo "HTTP :80 OK" || echo "omitido")

Pendiente (cuando Anddes provea):
  - Dominio + certificado TLS
  - Actualizar DJANGO_ALLOWED_HOSTS, CSRF, AZURE_AD_REDIRECT_URI y VirtualHost

Revisa/completa ${ENV_FILE} (DB, Blob, ACS, Entra) si aún faltan secretos.
EOF
