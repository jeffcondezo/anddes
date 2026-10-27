from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.finders import find
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Acuerdo, PlantillaCorreo, TipoNotificacionAcuerdo

# Plantillas editables: solo control de flujo seguro + variables.
# Evita SSTI (p. ej. {% include %}, {% load %}, {% ssi %}, |safe).
_TAG_NAME_RE = re.compile(r"\{%\s*([a-zA-Z_]+)")
_ALLOWED_TAGS = frozenset(
    {
        "if",
        "elif",
        "else",
        "endif",
        "for",
        "empty",
        "endfor",
        "comment",
        "endcomment",
    }
)
_FORBIDDEN_FILTER_RE = re.compile(r"\|\s*(safe|safeseq)\b", re.IGNORECASE)


def validar_html_plantilla_correo(html: str) -> str | None:
    """Devuelve mensaje de error si el HTML no es seguro para Template(); None si OK."""
    for match in _TAG_NAME_RE.finditer(html or ""):
        tag = match.group(1).lower()
        if tag not in _ALLOWED_TAGS:
            return (
                f"Etiqueta de plantilla no permitida: {{% {tag} %}}. "
                "Solo se admiten if / for / comment (y sus cierres)."
            )
    if _FORBIDDEN_FILTER_RE.search(html or ""):
        return "Filtro no permitido (|safe). El escape HTML es obligatorio."
    return None


def render_plantilla_html(html: str, ctx: dict) -> str:
    """Renderiza HTML de plantilla de correo con autoescape y validación anti-SSTI."""
    error = validar_html_plantilla_correo(html)
    if error:
        raise ValueError(error)
    return Template(html).render(Context(ctx, autoescape=True))


def _logo_filesystem_path() -> Path | None:
    found = find("maestro/img/logotipo.png")
    if found:
        return Path(found)
    static_root = getattr(settings, "STATIC_ROOT", None)
    if static_root:
        candidate = Path(static_root) / "maestro" / "img" / "logotipo.png"
        if candidate.is_file():
            return candidate
    # Desarrollo: carpeta estática de la app
    app_static = Path(__file__).resolve().parent / "static" / "maestro" / "img" / "logotipo.png"
    if app_static.is_file():
        return app_static
    return None


def logo_cid() -> str:
    return "anddes-logo"


def contexto_correo_acuerdo(
    acuerdo: Acuerdo,
    *,
    es_recordatorio: bool = False,
    mensaje_extra: str = "",
    tipo_regla: str | None = None,
    dias_regla: int | None = None,
    logo_src: str | None = None,
    es_preview: bool = False,
    cta_url: str = "",
) -> dict:
    empresa_nombre = acuerdo.empresa.nombre if acuerdo.empresa_id else "—"
    fecha = (
        timezone.localtime(acuerdo.fecha_compromiso).strftime("%d/%m/%Y %H:%M")
        if acuerdo.fecha_compromiso
        else "—"
    )
    detalle = (acuerdo.descripcion or "").strip()
    if mensaje_extra.strip():
        detalle = (
            f"{detalle}\n\nNota: {mensaje_extra.strip()}"
            if detalle
            else f"Nota: {mensaje_extra.strip()}"
        )

    from .actividad_mail import documentos_aún_pendientes  # evita import circular

    docs = list(acuerdo.documentos.order_by("codigo")) if acuerdo.pk else []
    pendientes = documentos_aún_pendientes(docs) if docs else []
    es_post = tipo_regla == TipoNotificacionAcuerdo.POST

    documentos_ctx: list[dict] = []
    docs_titulo = ""
    if docs:
        if es_recordatorio:
            lista = pendientes
            docs_titulo = "Documentos aún pendientes de aprobación"
            for d in lista:
                documentos_ctx.append(
                    {"codigo": d.codigo, "nombre": d.nombre, "estado": "Pendiente", "ok": False}
                )
        else:
            docs_titulo = "Documentos del acuerdo"
            for d in docs:
                ok = d not in pendientes
                documentos_ctx.append(
                    {
                        "codigo": d.codigo,
                        "nombre": d.nombre,
                        "estado": "Aprobado" if ok else "Pendiente",
                        "ok": ok,
                    }
                )

    dias_relativo = ""
    if tipo_regla == TipoNotificacionAcuerdo.ANTES and dias_regla is not None:
        if dias_regla == 0:
            dias_relativo = "Vence hoy"
            badge_texto = "Vence hoy"
            titulo_correo = "Recordatorio: acuerdo vence hoy"
            intro = (
                f"Le recordamos un compromiso GISTM con {empresa_nombre} "
                "cuya fecha de vencimiento es hoy."
            )
        else:
            dias_relativo = f"Faltan {dias_regla} día(s)"
            badge_texto = f"{dias_regla} día(s) antes"
            titulo_correo = "Aviso previo al vencimiento"
            intro = (
                f"Le avisamos con anticipación sobre un acuerdo GISTM con {empresa_nombre} "
                f"que vence en {dias_regla} día(s)."
            )
    elif es_post:
        badge_texto = "Post vencimiento"
        titulo_correo = "Recordatorio: acuerdo vencido"
        intro = (
            f"El acuerdo GISTM con {empresa_nombre} ya superó su fecha de compromiso. "
            "Le recordamos el seguimiento pendiente."
        )
        if dias_regla:
            dias_relativo = (
                f"{dias_regla} día(s) después del vencimiento"
                if dias_regla != 1
                else "1 día después del vencimiento"
            )
    elif es_recordatorio:
        badge_texto = "Recordatorio"
        titulo_correo = "Recordatorio de acuerdo"
        intro = f"Recordatorio de un acuerdo GISTM con {empresa_nombre}."
    else:
        badge_texto = "Nuevo acuerdo"
        titulo_correo = "Nuevo acuerdo registrado"
        intro = f"Se registró un acuerdo GISTM con {empresa_nombre}."

    cierre = (
        "Si ya cargaron las evidencias y fueron aprobadas, pueden ignorar este mensaje."
        if es_recordatorio
        else "Por favor avancen lo comprometido en la plataforma cuando corresponda."
    )

    return {
        "asunto": "",  # se completa fuera
        "badge_texto": badge_texto,
        "titulo_correo": titulo_correo,
        "intro": intro,
        "acuerdo_titulo": acuerdo.titulo,
        "empresa_nombre": empresa_nombre,
        "fecha_compromiso": fecha,
        "dias_relativo": dias_relativo,
        "detalle": detalle,
        "documentos": documentos_ctx,
        "docs_titulo": docs_titulo,
        "cierre": cierre,
        "cta_url": cta_url,
        "cta_texto": "Ingresar a GISTM",
        "logo_src": logo_src,
        "es_post": es_post,
        "es_preview": es_preview,
        "es_recordatorio": es_recordatorio,
    }


def _html_plantilla_acuerdo_default() -> str:
    path = (
        Path(__file__).resolve().parent
        / "templates"
        / "maestro"
        / "email"
        / "acuerdo_notificacion.html"
    )
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return "<html><body><p>{{ titulo_correo }}</p><p>{{ intro }}</p></body></html>"


def asegurar_plantilla_acuerdo() -> PlantillaCorreo:
    """Crea la plantilla editable si aún no existe (contenido = archivo HTML actual)."""
    plantilla, created = PlantillaCorreo.objects.get_or_create(
        codigo=PlantillaCorreo.CODIGO_ACUERDO,
        defaults={
            "nombre": "Notificación de acuerdos / recordatorios",
            "descripcion": (
                "Plantilla general de correos de acuerdos. Variables: "
                "{{ logo_src }}, {{ badge_texto }}, {{ titulo_correo }}, {{ intro }}, "
                "{{ acuerdo_titulo }}, {{ empresa_nombre }}, {{ fecha_compromiso }}, "
                "{{ dias_relativo }}, {{ detalle }}, {{ documentos }}, {{ docs_titulo }}, "
                "{{ cierre }}, {{ cta_url }}, {{ cta_texto }}, {{ es_post }}, {{ es_preview }}, "
                "{{ asunto }}."
            ),
            "html": _html_plantilla_acuerdo_default(),
            "activo": True,
        },
    )
    if created and not plantilla.html.strip():
        plantilla.html = _html_plantilla_acuerdo_default()
        plantilla.save(update_fields=["html", "actualizado_en"])
    return plantilla


def render_correo_acuerdo_html(ctx: dict) -> str:
    """Usa la plantilla editable en BD si está activa; si no, el archivo del repo."""
    plantilla = (
        PlantillaCorreo.objects.filter(
            codigo=PlantillaCorreo.CODIGO_ACUERDO, activo=True
        )
        .only("html")
        .first()
    )
    if plantilla and (plantilla.html or "").strip():
        try:
            return render_plantilla_html(plantilla.html, ctx)
        except ValueError:
            # Plantilla inválida en BD: no bloquear el envío; caer al HTML del repo.
            pass
    return render_to_string("maestro/email/acuerdo_notificacion.html", ctx)


def cuerpo_texto_plano(ctx: dict) -> str:
    lineas = [
        ctx["titulo_correo"],
        "",
        ctx["intro"],
        "",
        f"Acuerdo: {ctx['acuerdo_titulo']}",
        f"Empresa: {ctx['empresa_nombre']}",
        f"Fecha compromiso: {ctx['fecha_compromiso']}",
    ]
    if ctx.get("dias_relativo"):
        lineas.append(f"Situación: {ctx['dias_relativo']}")
    if ctx.get("detalle"):
        lineas.extend(["", "Detalle:", ctx["detalle"]])
    if ctx.get("documentos"):
        lineas.extend(["", ctx.get("docs_titulo") or "Documentos:"])
        for d in ctx["documentos"]:
            est = f" ({d['estado']})" if d.get("estado") else ""
            lineas.append(f"- {d['codigo']} · {d['nombre']}{est}")
    lineas.extend(["", ctx.get("cierre") or "", "", "Saludos,", "Anddes · GISTM"])
    return "\n".join(lineas)


def leer_bytes_logo() -> bytes | None:
    path = _logo_filesystem_path()
    if not path:
        return None
    return path.read_bytes()
