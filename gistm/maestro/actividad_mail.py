from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .messaging import registrar_mensaje
from .models import (
    Actividad,
    Acuerdo,
    AlcanceActividad,
    DocumentoCarga,
    EmpresaDocumento,
    EstadoRevisionCarga,
    PerfilUsuario,
    TipoActividad,
    TipoDocumentoMensaje,
    TipoUsuario,
)


def destinatarios_cliente(empresa_id: int) -> list[str]:
    perfiles = (
        PerfilUsuario.objects.filter(
            tipo=TipoUsuario.CLIENTE,
            activo=True,
            empresa_id=empresa_id,
            user__is_active=True,
        )
        .exclude(user__email="")
        .select_related("user")
    )
    return sorted({p.user.email.strip() for p in perfiles if p.user.email})


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


def _enviar(asunto: str, cuerpo: str, destinatarios: list[str]) -> None:
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "noreply@anddes.com"
    send_mail(
        subject=asunto,
        message=cuerpo,
        from_email=from_email,
        recipient_list=destinatarios,
        fail_silently=False,
    )


def enviar_correo_actividad(actividad: Actividad) -> tuple[bool, str]:
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
            f"Enlace: {enlace}\n\n"
            f"Agenda / tenor:\n{actividad.descripcion or '(Sin detalle)'}\n\n"
            f"Documentos relacionados:\n{docs_txt}\n\n"
            f"Saludos,\nAnddes · GISTM\n"
        )

    _enviar(asunto, cuerpo, destinatarios)
    actividad.correo_enviado_en = timezone.now()
    actividad.save(update_fields=["correo_enviado_en", "actualizado_en"])
    return True, f"Correo enviado a {len(destinatarios)} destinatario(s)."


def enviar_correo_acuerdo(
    acuerdo: Acuerdo,
    *,
    es_recordatorio: bool = False,
    mensaje_extra: str = "",
) -> tuple[bool, str]:
    """
    Correo de acuerdo o recordatorio.
    - Solo texto: siempre se envía.
    - Con documentos: en recordatorio solo se mencionan los aún no aprobados;
      si todos cumplidos, no se envía.
    """
    if not acuerdo.empresa_id:
        return False, "El acuerdo no tiene empresa asociada."

    destinatarios = destinatarios_cliente(acuerdo.empresa_id)
    if not destinatarios:
        return False, "La empresa no tiene usuarios cliente con correo activo."

    docs = list(acuerdo.documentos.order_by("codigo"))
    pendientes = documentos_aún_pendientes(docs) if docs else []
    solo_texto = not docs

    if es_recordatorio and docs and not pendientes:
        return False, "No hay nada pendiente: los documentos del acuerdo ya están aprobados."

    fecha = (
        timezone.localtime(acuerdo.fecha_compromiso).strftime("%d/%m/%Y %H:%M")
        if acuerdo.fecha_compromiso
        else "—"
    )
    detalle = acuerdo.descripcion or "(Sin detalle adicional)"
    if mensaje_extra.strip():
        detalle = f"{detalle}\n\nNota del recordatorio: {mensaje_extra.strip()}"

    if es_recordatorio:
        if solo_texto:
            docs_bloque = (
                "Este acuerdo es de texto (sin documentos asociados); "
                "por favor confirmen su avance con el consultor."
            )
        else:
            docs_bloque = "Documentos aún pendientes de aprobación:\n" + "\n".join(
                f"- {d.codigo} · {d.nombre}" for d in pendientes
            )
        asunto = f"[GISTM] Recordatorio de acuerdo · {acuerdo.titulo} · {acuerdo.empresa.nombre}"
        cuerpo = (
            f"Hola,\n\n"
            f"Recordatorio de un acuerdo GISTM con {acuerdo.empresa.nombre}.\n\n"
            f"Acuerdo: {acuerdo.titulo}\n"
            f"Fecha compromiso: {fecha}\n\n"
            f"Detalle:\n{detalle}\n\n"
            f"{docs_bloque}\n\n"
            f"Si ya cargaron las evidencias y fueron aprobadas, pueden ignorar este recordatorio.\n\n"
            f"Saludos,\nAnddes · GISTM\n"
        )
        acuerdo.ultimo_recordatorio_en = timezone.now()
        acuerdo.save(update_fields=["ultimo_recordatorio_en", "actualizado_en"])
    else:
        if solo_texto:
            docs_bloque = "Sin documentos asociados (compromiso de texto)."
        else:
            lineas = []
            for d in docs:
                ok = d not in pendientes
                estado = "cumplido / aprobado" if ok else "pendiente"
                lineas.append(f"- {d.codigo} · {d.nombre} ({estado})")
            docs_bloque = "Documentos del acuerdo:\n" + "\n".join(lineas)
        asunto = f"[GISTM] Acuerdo · {acuerdo.titulo} · {acuerdo.empresa.nombre}"
        cuerpo = (
            f"Hola,\n\n"
            f"Se registró un acuerdo GISTM con {acuerdo.empresa.nombre}.\n\n"
            f"Acuerdo: {acuerdo.titulo}\n"
            f"Fecha compromiso: {fecha}\n\n"
            f"Detalle:\n{detalle}\n\n"
            f"{docs_bloque}\n\n"
            f"Por favor avancen lo comprometido en la plataforma cuando corresponda.\n\n"
            f"Saludos,\nAnddes · GISTM\n"
        )
        acuerdo.correo_enviado_en = timezone.now()
        acuerdo.save(update_fields=["correo_enviado_en", "actualizado_en"])

    _enviar(asunto, cuerpo, destinatarios)
    return True, f"Correo enviado a {len(destinatarios)} destinatario(s)."


def publicar_mensajes_acuerdo(
    acuerdo: Acuerdo,
    autor,
    *,
    solo_pendientes: bool = False,
) -> int:
    """Mensaje en el hilo de cada documento del acuerdo."""
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
