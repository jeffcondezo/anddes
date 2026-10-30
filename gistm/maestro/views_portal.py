from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, Prefetch, Q
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .documento_criterio_excel import (
    format_import_message,
    importar_documento_criterio_excel,
    plantilla_bytes,
)

from .entra import build_auth_url, claims_from_id_token, exchange_code_for_token, new_oauth_state
from .actividad_mail import (
    emails_desde_responsables,
    enviar_correo_actividad,
    enviar_correo_acuerdo,
    publicar_mensajes_acuerdo,
    responsables_con_correo,
)
from .avisos import (
    avisar_documento_propio,
    avisar_mensaje_cliente,
    avisar_nueva_carga,
)
from .calendar_utils import mes_anterior, mes_calendario, mes_siguiente, parse_anio_mes
from .indicadores import (
    DOC_SUBTABS,
    TABS,
    avance_por_principios,
    chart_general,
    chart_principios,
    chart_temas,
    tablero_progreso,
    tablero_estandar,
    cobertura_estandar,
    curva_avance,
    documentos_indicadores,
    documentos_prioritarios,
    documentos_por_vencer,
    DIAS_POR_VENCER,
    filtrar_documentos,
    filtrar_documentos_query,
    opciones_filtros_documentos,
    filas_empresas_indicadores,
    glosario_items,
)
from .forms import (
    ActivarDocumentosCatalogoForm,
    ActividadForm,
    AcuerdoForm,
    AcuerdoRecordatorioForm,
    AplicarEstandarRevisionForm,
    AreaCreateForm,
    EmpresaNotificacionReglaForm,
    ResponsableForm,
    ClienteDocumentoPropioCargaForm,
    CriterioForm,
    DocumentoCargaForm,
    DocumentoCriterioForm,
    DocumentoForm,
    DocumentoMensajeForm,
    EmpresaDocumentoCriterioForm,
    EmpresaDocumentoPropioForm,
    EmpresaForm,
    PreguntaForm,
    PrincipioForm,
    RechazoDocumentoCargaForm,
    DocumentoRevisionConsultorForm,
    RequisitoForm,
    RevisionForm,
    DocumentoVigenciaAprobacionForm,
    RevisionParcialCargaForm,
    TemaForm,
    GlosarioTerminoForm,
    UsuarioForm,
)
from .models import (
    Actividad,
    Acuerdo,
    AlcanceActividad,
    Area,
    AvisoConsultor,
    CorreoEnvio,
    PlantillaCorreo,
    Responsable,
    TipoCorreoEnvio,
    TipoNotificacionAcuerdo,
    Criterio,
    EstadoAcuerdo,
    TipoActividad,
    Documento,
    DocumentoCarga,
    DocumentoCriterio,
    DocumentoMensaje,
    Empresa,
    EmpresaDocumento,
    EmpresaDocumentoCriterio,
    EmpresaDocumentoEtapaHistorial,
    EstadoActividad,
    EstadoRevisionCarga,
    EstadoRevisionCriterio,
    IntentoAcceso,
    MotivoIntentoAcceso,
    OrigenEmpresaDocumento,
    PerfilUsuario,
    Pregunta,
    Principio,
    RegistroLoginExterno,
    Requisito,
    Revision,
    RevisionCriterioDesactivado,
    Tema,
    GlosarioTermino,
    TipoDocumentoMensaje,
    TIPOS_CLIENTE,
    TipoUsuario,
)
from .models import (
    aplicar_deshabilitados_catalogo_a_revision,
    aplicar_estandar_a_revision,
    asegurar_snapshot_catalogo_revision,
    crear_snapshot_catalogo_revision,
    ponderaciones_revision,
    sincronizar_actividades_pasadas,
    sincronizar_acuerdos_vencidos,
    sincronizar_estado_carga_documento,
    sincronizar_estado_criterio_desde_preguntas,
)
from .messaging import autor_es_consultor, registrar_mensaje, sembrar_mensaje_rechazo_si_falta
from .permissions import (
    administrador_required,
    actividades_visibles,
    cliente_carga_required,
    cliente_required,
    consultor_default_empresa,
    consultores_candidatos_empresa,
    empresas_visibles,
    get_perfil,
    gestor_admin_o_cliente_required,
    gestor_o_admin_required,
    puede_ver_actividad,
    puede_ver_empresa,
    puede_ver_revision,
    revision_cliente_actual,
    revisiones_visibles_cliente,
    usuario_cliente_puede_cargar,
    usuario_es_administrador,
    usuario_es_cliente,
    usuario_es_gestor,
    usuarios_asignables,
)
from .progress import (
    calcular_progreso_revision,
    revision_activa_empresa,
    revision_activa_por_id,
    revisiones_activas_empresa,
)
from .activity import eventos_monitoreo_empresa
from .plan_entrega import arbol_plan_entrega

User = get_user_model()

from .view_helpers import (
    rev_id_param as _rev_id_param,
    revision_seguimiento as _revision_seguimiento,
    seguimiento_url as _seguimiento_url,
)

def _cliente_contexto_revision(request: HttpRequest) -> tuple[Empresa, Revision | None]:
    perfil = get_perfil(request.user)
    assert perfil and perfil.empresa_id
    empresa = perfil.empresa
    revision = revision_cliente_actual(request)
    return empresa, revision


@cliente_required
@require_GET
def mi_empresa_dashboard(request: HttpRequest) -> HttpResponse:
    """Redirige al dashboard de indicadores de la empresa del cliente."""
    empresa, revision = _cliente_contexto_revision(request)
    url = reverse("maestro:indicadores_empresa", args=[empresa.pk])
    if revision:
        url = f"{url}?rev={revision.pk}"
    return redirect(url)


@cliente_required
@require_GET
def mi_empresa_documentos(request: HttpRequest) -> HttpResponse:
    empresa, revision = _cliente_contexto_revision(request)
    progreso = calcular_progreso_revision(revision) if revision else None
    estado_tab = (request.GET.get("estado") or "por_subir").strip().lower()
    if estado_tab not in {"aprobados", "en_revision", "rechazados", "por_subir", "todos"}:
        estado_tab = "por_subir"
    documentos = []
    if progreso:
        if estado_tab == "aprobados":
            documentos = progreso.documentos_aprobados
        elif estado_tab == "en_revision":
            documentos = progreso.documentos_en_revision
        elif estado_tab == "rechazados":
            documentos = progreso.documentos_rechazados
        elif estado_tab == "por_subir":
            documentos = progreso.documentos_por_subir
        else:
            documentos = progreso.documentos
    return render(
        request,
        "maestro/mi_empresa_documentos.html",
        {
            "empresa": empresa,
            "revision": revision,
            "revisiones_cliente": list(revisiones_visibles_cliente(request.user)),
            "progreso": progreso,
            "estado_tab": estado_tab,
            "documentos": documentos,
            "cliente_puede_cargar": usuario_cliente_puede_cargar(request.user),
        },
    )


@cliente_carga_required
@require_http_methods(["GET", "POST"])
def mi_empresa_documento_cargar(request: HttpRequest, doc_id: int) -> HttpResponse:
    empresa, revision = _cliente_contexto_revision(request)
    if not revision:
        messages.warning(request, "No hay una revisión activa para tu empresa.")
        return redirect("maestro:mi_empresa_dashboard")
    documento = get_object_or_404(
        EmpresaDocumento,
        pk=doc_id,
        revision=revision,
        activo=True,
    )
    if not documento.vinculos_criterio.exists():
        messages.warning(
            request,
            "Este documento no tiene criterios asociados y no forma parte de la carga solicitada.",
        )
        return redirect("maestro:mi_empresa_documentos")
    form = DocumentoCargaForm(
        request.POST or None,
        request.FILES or None,
        empresa=empresa,
    )
    if request.method == "POST" and form.is_valid():
        carga = form.save(commit=False)
        carga.empresa_documento = documento
        carga.subido_por = request.user
        carga.activo = True
        carga.save()
        from .auditoria import auditar_carga

        auditar_carga(request, carga=carga)
        obs = (carga.observaciones or "").strip()
        if carga.es_url:
            detalle = f"se registró la URL {carga.etiqueta_evidencia}"
            ok_msg = (
                f"URL registrada para {documento.codigo}. "
                "Quedó en revisión del consultor."
            )
        else:
            detalle = f"se cargó {carga.etiqueta_evidencia}"
            ok_msg = (
                f"Archivo cargado para {documento.codigo}. "
                "Quedó en revisión del consultor."
            )
        texto = (
            f"Versión {carga.version}: {detalle}."
            + (f"\n\nObservaciones: {obs}" if obs else "")
        )
        registrar_mensaje(
            empresa_documento=documento,
            texto=texto,
            autor=request.user,
            es_consultor=False,
            tipo=TipoDocumentoMensaje.VERSION,
            carga=carga,
        )
        try:
            avisar_nueva_carga(carga=carga, actor=request.user)
        except Exception:  # noqa: BLE001
            pass
        messages.success(request, ok_msg)
        return redirect("maestro:mi_empresa_documentos")
    carga_actual = (
        DocumentoCarga.objects.filter(empresa_documento=documento, activo=True)
        .order_by("-creado_en")
        .first()
    )
    return render(
        request,
        "maestro/mi_empresa_documento_cargar.html",
        {
            "empresa": empresa,
            "revision": revision,
            "documento": documento,
            "form": form,
            "carga_actual": carga_actual,
            "conversacion_url": reverse(
                "maestro:documento_conversacion", args=[documento.pk]
            ),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def documento_conversacion(request: HttpRequest, doc_id: int) -> HttpResponse:
    """Hilo de mensajes consultor ↔ minera sobre un documento (ventana dedicada)."""
    documento = get_object_or_404(
        EmpresaDocumento.objects.select_related("revision", "revision__empresa"),
        pk=doc_id,
        activo=True,
    )
    empresa = documento.revision.empresa
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a este documento.")
        return redirect("maestro:home")

    es_cliente = usuario_es_cliente(request.user)
    es_consultor = autor_es_consultor(request.user)
    if not (es_cliente or es_consultor):
        messages.error(request, "No tienes permisos para esta conversación.")
        return redirect("maestro:home")
    if es_cliente and not puede_ver_revision(request.user, documento.revision):
        messages.error(request, "No tienes acceso a la revisión de este documento.")
        return redirect("maestro:mi_empresa_documentos")

    puede_cargar = (not es_cliente) or usuario_cliente_puede_cargar(request.user)
    # Sembrar rechazos previos sin hilo
    for carga in DocumentoCarga.objects.filter(
        empresa_documento=documento, estado_revision=EstadoRevisionCarga.RECHAZADO
    ):
        sembrar_mensaje_rechazo_si_falta(carga)

    form = DocumentoMensajeForm(request.POST or None)
    if request.method == "POST":
        if es_cliente and not puede_cargar:
            messages.error(
                request,
                "Tu perfil es de solo lectura; no puedes enviar mensajes.",
            )
            return redirect("maestro:documento_conversacion", doc_id=documento.pk)
        if form.is_valid():
            tipo = form.cleaned_data.get("tipo") or TipoDocumentoMensaje.OBSERVACION
            if tipo not in {
                TipoDocumentoMensaje.OBSERVACION,
                TipoDocumentoMensaje.ACUERDO,
                TipoDocumentoMensaje.RESPUESTA,
            }:
                tipo = TipoDocumentoMensaje.OBSERVACION
            texto_msg = form.cleaned_data["texto"]
            registrar_mensaje(
                empresa_documento=documento,
                texto=texto_msg,
                autor=request.user,
                es_consultor=es_consultor,
                tipo=tipo,
            )
            if es_cliente and not es_consultor:
                try:
                    avisar_mensaje_cliente(
                        documento=documento,
                        texto=texto_msg,
                        actor=request.user,
                    )
                except Exception:  # noqa: BLE001
                    pass
            messages.success(request, "Mensaje enviado.")
            return redirect("maestro:documento_conversacion", doc_id=documento.pk)

    mensajes_qs = (
        DocumentoMensaje.objects.filter(empresa_documento=documento)
        .select_related("autor", "carga", "criterio")
        .order_by("creado_en")
    )
    carga_actual = (
        DocumentoCarga.objects.filter(empresa_documento=documento, activo=True)
        .order_by("-creado_en")
        .first()
    )
    versiones = list(
        DocumentoCarga.objects.filter(empresa_documento=documento)
        .order_by("-version", "-creado_en")
    )
    return render(
        request,
        "maestro/documento_conversacion.html",
        {
            "empresa": empresa,
            "revision": documento.revision,
            "documento": documento,
            "mensajes": mensajes_qs,
            "form": form,
            "carga_actual": carga_actual,
            "versiones": versiones,
            "es_cliente": es_cliente,
            "es_consultor": es_consultor,
            "cliente_puede_cargar": puede_cargar if es_cliente else False,
            "solo_lectura_cliente": es_cliente and not puede_cargar,
        },
    )


@cliente_carga_required
@require_http_methods(["GET", "POST"])
def mi_empresa_documento_nuevo(request: HttpRequest) -> HttpResponse:
    empresa, revision = _cliente_contexto_revision(request)
    if not revision:
        messages.warning(request, "No hay una revisión activa para tu empresa.")
        return redirect("maestro:mi_empresa_dashboard")
    progreso = calcular_progreso_revision(revision)
    if progreso.criterios_huerfanos_count <= 0:
        messages.info(
            request,
            "Solo puedes registrar un documento adicional cuando hay criterios sin documento asignado.",
        )
        return redirect("maestro:mi_empresa_documentos")
    form = ClienteDocumentoPropioCargaForm(
        request.POST or None,
        request.FILES or None,
        revision=revision,
    )
    if request.method == "POST" and form.is_valid():
        documento = form.save(user=request.user)
        carga = getattr(form, "_carga_creada", None)
        if carga is not None:
            from .auditoria import auditar_carga

            auditar_carga(
                request,
                carga=carga,
                detalle=(
                    f"Documento propio {documento.codigo} con evidencia v{carga.version}"
                ),
            )
        try:
            avisar_documento_propio(
                documento=documento,
                carga=carga,
                actor=request.user,
            )
        except Exception:  # noqa: BLE001
            pass
        tipo_ev = "URL" if (carga and carga.es_url) else "archivo"
        messages.success(
            request,
            f"Documento propio creado con evidencia por {tipo_ev}. "
            "El consultor asociará los criterios después.",
        )
        return redirect("maestro:mi_empresa_documentos")
    return render(
        request,
        "maestro/mi_empresa_documento_nuevo.html",
        {
            "empresa": empresa,
            "revision": revision,
            "form": form,
            "huerfanos": progreso.criterios_huerfanos_count,
        },
    )


