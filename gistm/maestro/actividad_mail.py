from __future__ import annotations

from email.mime.image import MIMEImage

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import QuerySet
from django.utils import timezone

from .email_templates import (
    cuerpo_texto_plano,
    contexto_correo_acuerdo,
    logo_cid,
    leer_bytes_logo,
    render_correo_acuerdo_html,
)
from .messaging import registrar_mensaje
from .models import (
    Actividad,
    Acuerdo,
    AlcanceActividad,
    CorreoEnvio,
    DocumentoCarga,
    EmpresaDocumento,
    EstadoRevisionCarga,
    PerfilUsuario,
    Responsable,
    TipoActividad,
    TipoCorreoEnvio,
    TipoDocumentoMensaje,
)


def acuerdo_es_solo_consultor(acuerdo: Acuerdo) -> bool:
    """True si el acuerdo nace de una actividad «Solo consultor»."""
    act = getattr(acuerdo, "actividad", None)
    if act is None and acuerdo.actividad_id:
        from .models import Actividad as Act

        act = Act.objects.filter(pk=acuerdo.actividad_id).only("alcance").first()
    return bool(act and act.alcance == AlcanceActividad.PERSONAL)


def destinatarios_consultor_acuerdo(acuerdo: Acuerdo) -> list[str]:
    """Correos del consultor (asignado / creador de la actividad o del acuerdo)."""
    emails: set[str] = set()
    act = getattr(acuerdo, "actividad", None)
    if act is None and acuerdo.actividad_id:
        act = (
            Actividad.objects.select_related("asignado_a", "creado_por")
            .filter(pk=acuerdo.actividad_id)
            .first()
        )
    for user in (
        getattr(act, "asignado_a", None) if act else None,
        getattr(act, "creado_por", None) if act else None,
        getattr(acuerdo, "creado_por", None),
    ):
        if user is None:
            continue
        mail = (getattr(user, "email", None) or "").strip()
        if mail:
            emails.add(mail)
    return sorted(emails)


def destinatarios_cliente(empresa_id: int) -> list[str]:
    from .models import TIPOS_CLIENTE  # evita import circular en carga de módulos

    perfiles = (
        PerfilUsuario.objects.filter(
            tipo__in=TIPOS_CLIENTE,
            activo=True,
            empresa_id=empresa_id,
            user__is_active=True,
        )
        .exclude(user__email="")
        .select_related("user")
    )
    return sorted({p.user.email.strip() for p in perfiles if p.user.email})


def responsables_con_correo(empresa_id: int) -> QuerySet[Responsable]:
    """Responsables activos de áreas activas de la empresa, con correo."""
    return (
        Responsable.objects.filter(
            area__empresa_id=empresa_id,
            area__activo=True,
            activo=True,
        )
        .exclude(correo="")
        .select_related("area")
        .order_by("area__nombre", "nombre")
    )


def emails_desde_responsables(responsables) -> list[str]:
    return sorted(
        {
            (r.correo or "").strip().lower()
            for r in responsables
            if (r.correo or "").strip()
        }
    )


def documentos_aún_pendientes(documentos) -> list[EmpresaDocumento]:
    """Documentos cuya evidencia vigente no está aprobada (o no hay carga)."""
    pendientes: list[EmpresaDocumento] = []
    for doc in documentos:
        carga = (
            DocumentoCarga.objects.filter(empresa_documento=doc, activo=True)
            .order_by("-creado_en")
            .first()
        )
        if not carga or carga.estado_revision != EstadoRevisionCarga.APROBADO:
            pendientes.append(doc)
    return pendientes


def _registrar_correo_envio(
    *,
    asunto: str,
    emails: list[str],
    tipo: str,
    empresa=None,
    acuerdo=None,
    actividad=None,
    automatico: bool = False,
    enviado_por=None,
    exito: bool = True,
    error: str = "",
) -> CorreoEnvio:
    return CorreoEnvio.objects.create(
        tipo=tipo,
        asunto=asunto[:255],
        destinatarios_to=emails[0] if emails else "",
        destinatarios_cc=", ".join(emails[1:]) if len(emails) > 1 else "",
        empresa=empresa,
        acuerdo=acuerdo,
        actividad=actividad,
        automatico=automatico,
        enviado_por=enviado_por,
        exito=exito,
        error=(error or "")[:2000],
    )


def _enviar(
    asunto: str,
    cuerpo: str,
    destinatarios: list[str],
    *,
    html: str | None = None,
    adjuntar_logo: bool = False,
    tipo: str = TipoCorreoEnvio.OTRO,
    empresa=None,
    acuerdo=None,
    actividad=None,
    automatico: bool = False,
    enviado_por=None,
) -> None:
    """Envía con el primero en To y el resto en CC (copia). Opcionalmente HTML + logo CID."""
    if empresa is not None and not getattr(empresa, "notificaciones_activas", True):
        raise ValueError(
            "La empresa tiene desactivado el envío de notificaciones por correo."
        )
    emails = [e.strip() for e in destinatarios if (e or "").strip()]
    if not emails:
        raise ValueError("No hay destinatarios de correo.")
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "noreply@anddes.com"
    msg = EmailMultiAlternatives(
        subject=asunto,
        body=cuerpo,
        from_email=from_email,
        to=[emails[0]],
        cc=emails[1:] or None,
    )
    if html:
        msg.attach_alternative(html, "text/html")
        if adjuntar_logo:
            logo_bytes = leer_bytes_logo()
            if logo_bytes:
                # multipart/related + Content-ID → ACS lo envía inline (cid:…).
                msg.mixed_subtype = "related"
                image = MIMEImage(logo_bytes, _subtype="png")
                cid = logo_cid()
                image.add_header("Content-ID", f"<{cid}>")
                image.add_header("Content-Disposition", "inline", filename="logotipo.png")
                msg.attach(image)
    try:
        msg.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001
        _registrar_correo_envio(
            asunto=asunto,
            emails=emails,
            tipo=tipo,
            empresa=empresa,
            acuerdo=acuerdo,
            actividad=actividad,
            automatico=automatico,
            enviado_por=enviado_por,
            exito=False,
            error=str(exc),
        )
        raise
    _registrar_correo_envio(
        asunto=asunto,
        emails=emails,
        tipo=tipo,
        empresa=empresa,
        acuerdo=acuerdo,
        actividad=actividad,
        automatico=automatico,
        enviado_por=enviado_por,
        exito=True,
    )


def enviar_correo_actividad(
    actividad: Actividad, *, enviado_por=None, automatico: bool = False
) -> tuple[bool, str]:
    """Notificación al crear una actividad con cliente."""
    if actividad.alcance != AlcanceActividad.CON_CLIENTE or not actividad.empresa_id:
        return False, "Solo se envía correo en actividades con cliente."

    destinatarios = destinatarios_cliente(actividad.empresa_id)
    if not destinatarios:
        return False, "La empresa no tiene usuarios cliente con correo activo."

    docs = list(actividad.documentos.order_by("codigo"))
    docs_txt = (
        "\n".join(f"- {d.codigo} · {d.nombre}" for d in docs)
        if docs
        else "(Sin documentos asociados)"
    )
    inicio = timezone.localtime(actividad.inicio).strftime("%d/%m/%Y %H:%M")
    fin = (
        timezone.localtime(actividad.fin).strftime("%d/%m/%Y %H:%M")
        if actividad.fin
        else "—"
    )
    enlace = actividad.enlace_reunion or "(Sin enlace de reunión)"
    if actividad.responsable_id:
        r = actividad.responsable
        responsable = r.nombre
        extras = []
        if r.correo:
            extras.append(r.correo)
        if r.telefono:
            extras.append(r.telefono)
        if extras:
            responsable = f"{responsable} ({', '.join(extras)})"
    else:
        responsable = "—"
    area_nombre = actividad.area.nombre if actividad.area_id else "—"

    if actividad.tipo == TipoActividad.RECORDATORIO:
        pendientes = documentos_aún_pendientes(docs)
        if docs and not pendientes:
            return False, "Todos los documentos asociados ya están aprobados; no hay recordatorio que enviar."
        if pendientes:
            docs_txt = "\n".join(f"- {d.codigo} · {d.nombre}" for d in pendientes)
        asunto = f"[GISTM] Recordatorio · {actividad.titulo} · {actividad.empresa.nombre}"
        cuerpo = (
            f"Hola,\n\n"
            f"Recordatorio GISTM para {actividad.empresa.nombre}.\n\n"
            f"{actividad.titulo}\n"
            f"Fecha: {inicio}\n\n"
            f"{actividad.descripcion or 'Pendiente completar lo acordado.'}\n\n"
            f"Documentos pendientes:\n{docs_txt}\n\n"
            f"Ingresen a GISTM para cargar o reemplazar las evidencias.\n\n"
            f"Saludos,\nAnddes · GISTM\n"
        )
    else:
        asunto = f"[GISTM] {actividad.titulo} · {actividad.empresa.nombre}"
        cuerpo = (
            f"Hola,\n\n"
            f"Se programó una actividad GISTM con {actividad.empresa.nombre}.\n\n"
            f"Título: {actividad.titulo}\n"
            f"Inicio: {inicio}\n"
            f"Fin: {fin}\n"
            f"Responsable: {responsable}\n"
            f"Área responsable: {area_nombre}\n"
            f"Enlace: {enlace}\n\n"
            f"Agenda / tenor:\n{actividad.descripcion or '(Sin detalle)'}\n\n"
            f"Documentos relacionados:\n{docs_txt}\n\n"
            f"Saludos,\nAnddes · GISTM\n"
        )

    _enviar(
        asunto,
        cuerpo,
        destinatarios,
        tipo=TipoCorreoEnvio.ACTIVIDAD,
        empresa=actividad.empresa,
        actividad=actividad,
        automatico=automatico,
        enviado_por=enviado_por,
    )
    actividad.correo_enviado_en = timezone.now()
    actividad.save(update_fields=["correo_enviado_en", "actualizado_en"])
    return True, f"Correo enviado a {len(destinatarios)} destinatario(s)."


def enviar_correo_acuerdo(
    acuerdo: Acuerdo,
    *,
    es_recordatorio: bool = False,
    mensaje_extra: str = "",
    destinatarios: list[str] | None = None,
    tipo_regla: str | None = None,
    dias_regla: int | None = None,
    automatico: bool = False,
    enviado_por=None,
) -> tuple[bool, str]:
    """
    Correo de acuerdo o recordatorio (HTML Anddes + texto plano).
    - Solo consultor: destinatarios = consultor (nunca cliente).
    - Con cliente: destinatarios = clientes de la empresa (o lista explícita).
    """
    solo_consultor = acuerdo_es_solo_consultor(acuerdo)

    if not solo_consultor and not acuerdo.empresa_id:
        return False, "El acuerdo no tiene empresa asociada."

    if destinatarios is None:
        if solo_consultor:
            destinatarios = destinatarios_consultor_acuerdo(acuerdo)
            if not destinatarios:
                return False, "No hay correo del consultor (asignado o creador) para notificar."
        else:
            destinatarios = destinatarios_cliente(acuerdo.empresa_id)
            if not destinatarios:
                return False, "La empresa no tiene usuarios cliente con correo activo."
    else:
        destinatarios = sorted({(e or "").strip() for e in destinatarios if (e or "").strip()})
        if not destinatarios:
            return False, "Selecciona al menos un responsable con correo."
        # Acuerdos solo consultor: nunca enviar a clientes aunque pasen lista.
        if solo_consultor:
            consultor = set(destinatarios_consultor_acuerdo(acuerdo))
            destinatarios = sorted(consultor) if consultor else destinatarios
            if not destinatarios:
                return False, "No hay correo del consultor para notificar."

    docs = list(acuerdo.documentos.order_by("codigo"))
    pendientes = documentos_aún_pendientes(docs) if docs else []

    if es_recordatorio and docs and not pendientes:
        return False, "No hay nada pendiente: los documentos del acuerdo ya están aprobados."

    site_url = (getattr(settings, "SITE_URL", None) or "").rstrip("/")
    cta_url = f"{site_url}/" if site_url else ""
    empresa_nombre = acuerdo.empresa.nombre if acuerdo.empresa_id else "interno"

    ctx = contexto_correo_acuerdo(
        acuerdo,
        es_recordatorio=es_recordatorio,
        mensaje_extra=mensaje_extra,
        tipo_regla=tipo_regla,
        dias_regla=dias_regla,
        logo_src=f"cid:{logo_cid()}",
        es_preview=False,
        cta_url=cta_url,
    )
    if es_recordatorio:
        asunto = f"[GISTM] Recordatorio de acuerdo · {acuerdo.titulo} · {empresa_nombre}"
        acuerdo.ultimo_recordatorio_en = timezone.now()
        acuerdo.save(update_fields=["ultimo_recordatorio_en", "actualizado_en"])
    else:
        asunto = f"[GISTM] Acuerdo · {acuerdo.titulo} · {empresa_nombre}"
        acuerdo.correo_enviado_en = timezone.now()
        acuerdo.save(update_fields=["correo_enviado_en", "actualizado_en"])

    ctx["asunto"] = asunto
    cuerpo = cuerpo_texto_plano(ctx)
    html = render_correo_acuerdo_html(ctx)
    _enviar(
        asunto,
        cuerpo,
        destinatarios,
        html=html,
        adjuntar_logo=True,
        tipo=(
            TipoCorreoEnvio.ACUERDO_RECORDATORIO
            if es_recordatorio
            else TipoCorreoEnvio.ACUERDO_NUEVO
        ),
        empresa=acuerdo.empresa if acuerdo.empresa_id else None,
        acuerdo=acuerdo,
        actividad=acuerdo.actividad if acuerdo.actividad_id else None,
        automatico=automatico,
        enviado_por=enviado_por,
    )
    destino = "consultor" if solo_consultor else "destinatario(s)"
    return True, f"Correo enviado a {len(destinatarios)} {destino}."


def publicar_mensajes_acuerdo(
    acuerdo: Acuerdo,
    autor,
    *,
    solo_pendientes: bool = False,
) -> int:
    """Mensaje en el hilo de cada documento (solo acuerdos con cliente; nunca solo consultor)."""
    if acuerdo_es_solo_consultor(acuerdo):
        return 0
    docs = list(acuerdo.documentos.select_related("revision").order_by("codigo"))
    if solo_pendientes:
        docs = documentos_aún_pendientes(docs)
    if not docs:
        return 0

    fecha = (
        timezone.localtime(acuerdo.fecha_compromiso).strftime("%d/%m/%Y")
        if acuerdo.fecha_compromiso
        else timezone.localtime(timezone.now()).strftime("%d/%m/%Y")
    )
    base = acuerdo.descripcion.strip() if acuerdo.descripcion else acuerdo.titulo
    prefijo = "Recordatorio de acuerdo" if solo_pendientes else "Acuerdo"
    texto = f"[{prefijo} · {fecha}] {acuerdo.titulo}\n\n{base}"

    n = 0
    for doc in docs:
        registrar_mensaje(
            empresa_documento=doc,
            texto=texto,
            autor=autor,
            es_consultor=True,
            tipo=TipoDocumentoMensaje.ACUERDO,
        )
        n += 1
    return n


def publicar_mensajes_en_documentos(
    actividad: Actividad,
    autor,
    *,
    solo_pendientes: bool = False,
) -> int:
    """Compat: mensajes desde actividad (p. ej. recordatorio de actividad)."""
    docs = list(actividad.documentos.select_related("revision").order_by("codigo"))
    if solo_pendientes:
        docs = documentos_aún_pendientes(docs)
    if not docs:
        return 0
    fecha = timezone.localtime(actividad.inicio).strftime("%d/%m/%Y")
    base = actividad.descripcion.strip() if actividad.descripcion else actividad.titulo
    texto = f"[{actividad.get_tipo_display()} · {fecha}] {actividad.titulo}\n\n{base}"
    n = 0
    for doc in docs:
        registrar_mensaje(
            empresa_documento=doc,
            texto=texto,
            autor=autor,
            es_consultor=True,
            tipo=TipoDocumentoMensaje.OBSERVACION,
        )
        n += 1
    return n
