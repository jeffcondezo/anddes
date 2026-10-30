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

SESSION_APROBACION_PENDIENTE = "aprobacion_pendiente_vigencia"

from .view_helpers import (  # noqa: E402
    rev_id_param as _rev_id_param,
    revision_seguimiento as _revision_seguimiento,
    seguimiento_url as _seguimiento_url,
)


def _doc_tiene_vigencia(doc: EmpresaDocumento) -> bool:
    if not (
        doc.frecuencia_actualizacion
        and doc.fecha_ultima_revision
        and doc.fecha_caducidad
    ):
        return False
    if doc.frecuencia_actualizacion == "OTRO" and not (doc.frecuencia_otro or "").strip():
        return False
    return True


def _quedaria_aprobado(carga: DocumentoCarga, decisions: list) -> bool:
    """True si, tras aplicar decisions, todas las preguntas quedarían CUMPLE."""
    from .models import EstadoRevisionPregunta, EmpresaDocumentoPregunta

    estados = {
        p.pk: p.estado_revision
        for p in EmpresaDocumentoPregunta.objects.filter(
            vinculo_criterio__empresa_documento_id=carga.empresa_documento_id
        )
    }
    if not estados:
        return False
    for pregunta, estado, _obs in decisions:
        estados[pregunta.pk] = estado
    return all(e == EstadoRevisionPregunta.CUMPLE for e in estados.values())


def _guardar_aprobacion_pendiente(
    request: HttpRequest,
    *,
    empresa: Empresa,
    doc: EmpresaDocumento,
    carga: DocumentoCarga,
    decisions: list,
    ubicaciones: list | None = None,
) -> None:
    request.session[SESSION_APROBACION_PENDIENTE] = {
        "empresa_id": empresa.pk,
        "doc_id": doc.pk,
        "carga_id": carga.pk,
        "decisions": [
            {"pregunta_id": p.pk, "estado": estado, "obs": obs}
            for p, estado, obs in decisions
        ],
        "ubicaciones": [
            {
                "vinculo_id": v.pk,
                "paginas": paginas,
                "fragmento": fragmento,
            }
            for v, paginas, fragmento in (ubicaciones or [])
        ],
    }
    request.session.modified = True


def _aplicar_ubicaciones_criterio(ubicaciones: list) -> int:
    """Persiste páginas/fragmento por vínculo de esta evaluación. Devuelve cuántos cambió."""
    n = 0
    for vinculo, paginas, fragmento in ubicaciones:
        paginas = (paginas or "").strip()[:120]
        fragmento = (fragmento or "").strip()
        if (vinculo.paginas or "") == paginas and (vinculo.fragmento or "") == fragmento:
            continue
        vinculo.paginas = paginas
        vinculo.fragmento = fragmento
        vinculo.save(update_fields=["paginas", "fragmento", "actualizado_en"])
        n += 1
    return n


def _ubicaciones_desde_pendiente(data: dict, doc: EmpresaDocumento) -> list:
    ids = [u["vinculo_id"] for u in data.get("ubicaciones") or []]
    vinculos = {
        v.pk: v
        for v in EmpresaDocumentoCriterio.objects.filter(
            pk__in=ids,
            empresa_documento=doc,
        )
    }
    out = []
    for u in data.get("ubicaciones") or []:
        v = vinculos.get(u["vinculo_id"])
        if v:
            out.append((v, u.get("paginas") or "", u.get("fragmento") or ""))
    return out


def _leer_aprobacion_pendiente(request: HttpRequest, doc_id: int) -> dict | None:
    data = request.session.get(SESSION_APROBACION_PENDIENTE) or {}
    if data.get("doc_id") != doc_id:
        return None
    return data


def _limpiar_aprobacion_pendiente(request: HttpRequest) -> None:
    if SESSION_APROBACION_PENDIENTE in request.session:
        del request.session[SESSION_APROBACION_PENDIENTE]
        request.session.modified = True


def _decisions_desde_pendiente(data: dict, doc: EmpresaDocumento) -> list:
    from .models import EmpresaDocumentoPregunta

    ids = [d["pregunta_id"] for d in data.get("decisions") or []]
    preguntas = {
        p.pk: p
        for p in EmpresaDocumentoPregunta.objects.filter(
            pk__in=ids,
            vinculo_criterio__empresa_documento=doc,
        )
    }
    out = []
    for d in data.get("decisions") or []:
        p = preguntas.get(d["pregunta_id"])
        if p:
            out.append((p, d["estado"], d.get("obs") or ""))
    return out


def _revertir_aprobacion_sin_vigencia(doc: EmpresaDocumento, *, user) -> None:
    """Devuelve preguntas/criterios/carga al estado pendiente (cancelar vigencia)."""
    from .models import EmpresaDocumentoPregunta, EstadoRevisionPregunta

    EmpresaDocumentoPregunta.objects.filter(
        vinculo_criterio__empresa_documento=doc,
        estado_revision=EstadoRevisionPregunta.CUMPLE,
    ).update(
        estado_revision=EstadoRevisionPregunta.PENDIENTE,
        observacion="",
        revisado_por=None,
        revisado_en=None,
    )
    for vinculo in EmpresaDocumentoCriterio.objects.filter(empresa_documento=doc):
        sincronizar_estado_criterio_desde_preguntas(vinculo, revisado_por=user)
    sincronizar_estado_carga_documento(doc, revisado_por=user)
    doc.frecuencia_actualizacion = ""
    doc.frecuencia_otro = ""
    doc.fecha_ultima_revision = None
    doc.fecha_caducidad = None
    doc.save(
        update_fields=[
            "frecuencia_actualizacion",
            "frecuencia_otro",
            "fecha_ultima_revision",
            "fecha_caducidad",
            "actualizado_en",
        ]
    )


@gestor_o_admin_required
@require_http_methods(["GET", "POST"])
def seguimiento_documento_cargar(
    request: HttpRequest, pk: int, doc_id: int
) -> HttpResponse:
    """
    Carga de evidencia por administrador/gestor (punto 1.3).
    Reutiliza DocumentoCarga; deja traza en subido_por sin suplantar al cliente.
    """
    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        return redirect("maestro:seguimiento_list")

    documento = get_object_or_404(
        EmpresaDocumento.objects.select_related("revision", "revision__empresa"),
        pk=doc_id,
        revision__empresa=empresa,
        revision__activo=True,
        activo=True,
    )
    revision = documento.revision
    if not documento.vinculos_criterio.exists():
        messages.warning(
            request,
            "Este documento no tiene criterios asociados; asócialos antes de cargar evidencia.",
        )
        return redirect(
            _seguimiento_url("maestro:seguimiento_empresa_pendientes", empresa.pk, revision)
        )

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
        from .models import resetear_criterios_no_aprobados

        resetear_criterios_no_aprobados(documento)
        sincronizar_estado_carga_documento(documento)
        carga.refresh_from_db()
        from .auditoria import auditar_carga

        auditar_carga(
            request,
            carga=carga,
            detalle=(
                f"Carga por consultor {request.user.get_full_name() or request.user.email}: "
                f"{carga.etiqueta_evidencia}"
            ),
        )
        obs = (carga.observaciones or "").strip()
        if carga.es_url:
            detalle = f"el consultor registró la URL {carga.etiqueta_evidencia}"
            ok_msg = (
                f"URL registrada para {documento.codigo}. "
                "Quedó pendiente de revisión."
            )
        else:
            detalle = f"el consultor cargó {carga.etiqueta_evidencia}"
            ok_msg = (
                f"Archivo cargado para {documento.codigo}. "
                "Quedó pendiente de revisión."
            )
        texto = (
            f"Versión {carga.version}: {detalle}."
            + (f"\n\nObservaciones: {obs}" if obs else "")
        )
        registrar_mensaje(
            empresa_documento=documento,
            texto=texto,
            autor=request.user,
            es_consultor=True,
            tipo=TipoDocumentoMensaje.VERSION,
            carga=carga,
        )
        messages.success(request, ok_msg)
        return redirect(
            _seguimiento_url("maestro:seguimiento_empresa_revision", empresa.pk, revision)
        )

    carga_actual = (
        DocumentoCarga.objects.filter(empresa_documento=documento, activo=True)
        .select_related("subido_por")
        .order_by("-creado_en")
        .first()
    )
    return render(
        request,
        "maestro/seguimiento_documento_cargar.html",
        {
            "empresa": empresa,
            "revision": revision,
            "revisiones_activas": list(revisiones_activas_empresa(empresa.pk)),
            "documento": documento,
            "form": form,
            "carga_actual": carga_actual,
            "volver_url": _seguimiento_url(
                "maestro:seguimiento_empresa_pendientes", empresa.pk, revision
            ),
            "conversacion_url": reverse(
                "maestro:documento_conversacion", args=[documento.pk]
            ),
        },
    )


@gestor_o_admin_required
@require_http_methods(["GET", "POST"])
def seguimiento_documento_asociar_criterios(
    request: HttpRequest, pk: int, doc_id: int
) -> HttpResponse:
    """Revisar evidencia del cliente y asociar criterios huérfanos de la revisión."""
    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        return redirect("maestro:seguimiento_list")

    documento = get_object_or_404(
        EmpresaDocumento.objects.select_related("documento", "revision"),
        pk=doc_id,
        revision__empresa=empresa,
        revision__activo=True,
        activo=True,
        origen=OrigenEmpresaDocumento.PROPIO,
    )
    revision = documento.revision
    carga = (
        DocumentoCarga.objects.filter(empresa_documento=documento, activo=True)
        .select_related("subido_por")
        .order_by("-creado_en")
        .first()
    )

    form = EmpresaDocumentoCriterioForm(request.POST or None, empresa_documento=documento)
    form.fields["criterios"].label_from_instance = (
        lambda c: f"{c.codigo} — {(c.descripcion or '')[:90]}"
    )
    form.fields["criterios"].help_text = (
        "Solo criterios huérfanos de esta revisión (aún sin documento). "
        "Al asociarlos, el documento pasa a contar en el avance del cliente."
    )
    form.fields["criterios"].widget.attrs["size"] = "16"

    tema_id = (request.GET.get("tema") or "").strip()
    principio_id = (request.GET.get("principio") or "").strip()
    if request.method == "GET":
        qs = form.fields["criterios"].queryset
        if tema_id.isdigit():
            qs = qs.filter(tema_id=int(tema_id))
        if principio_id.isdigit():
            qs = qs.filter(principio_id=int(principio_id))
        form.fields["criterios"].queryset = qs

    huerfanos_disponibles = form.fields["criterios"].queryset.count()

    if request.method == "POST" and form.is_valid():
        creados = form.save()
        if creados:
            messages.success(
                request,
                f"Se asociaron {len(creados)} criterio(s) huérfano(s) a {documento.codigo}. "
                "El avance del cliente se actualizará con esta evidencia.",
            )
        else:
            messages.info(request, "No se crearon nuevos vínculos.")
        return redirect(_seguimiento_url("maestro:seguimiento_empresa_revision", empresa.pk, revision))

    vinculos = (
        EmpresaDocumentoCriterio.objects.filter(empresa_documento=documento)
        .select_related(
            "criterio",
            "criterio__tema",
            "criterio__principio",
            "area",
            "empresa_documento_secundario",
        )
        .order_by("criterio__codigo")
    )
    return render(
        request,
        "maestro/seguimiento_documento_asociar.html",
        {
            "empresa": empresa,
            "revision": revision,
            "revisiones_activas": list(revisiones_activas_empresa(empresa.pk)),
            "documento": documento,
            "carga": carga,
            "form": form,
            "vinculos": vinculos,
            "huerfanos_disponibles": huerfanos_disponibles,
            "temas": Tema.objects.all(),
            "principios": Principio.objects.select_related("tema").all(),
            "filtro_tema": tema_id,
            "filtro_principio": principio_id,
        },
    )


@gestor_o_admin_required
@require_GET
def seguimiento_empresa_monitoreo(request: HttpRequest, pk: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        return redirect("maestro:seguimiento_list")
    revisiones_activas = list(revisiones_activas_empresa(empresa.pk))
    revision = _revision_seguimiento(empresa, request)
    eventos = eventos_monitoreo_empresa(empresa.pk, limite=120)
    return render(
        request,
        "maestro/seguimiento_monitoreo.html",
        {
            "empresa": empresa,
            "revision": revision,
            "revisiones_activas": revisiones_activas,
            "eventos": eventos,
        },
    )


@gestor_o_admin_required
@require_GET
def seguimiento_empresa_revision(request: HttpRequest, pk: int) -> HttpResponse:
    """Cola de evidencias pendientes de aprobar/rechazar (y asociar criterios)."""
    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        return redirect("maestro:seguimiento_list")
    revisiones_activas = list(revisiones_activas_empresa(empresa.pk))
    revision = _revision_seguimiento(empresa, request)
    progreso = calcular_progreso_revision(revision) if revision else None
    pendientes = progreso.documentos_por_revisar if progreso else []
    return render(
        request,
        "maestro/seguimiento_revision.html",
        {
            "empresa": empresa,
            "revision": revision,
            "revisiones_activas": revisiones_activas,
            "progreso": progreso,
            "pendientes": pendientes,
            "rechazo_form": RechazoDocumentoCargaForm(),
        },
    )


@gestor_o_admin_required
@require_GET
def seguimiento_empresa_pendientes(request: HttpRequest, pk: int) -> HttpResponse:
    """Backlog: documentos sin entregar / rechazados y criterios sin documento."""
    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        return redirect("maestro:seguimiento_list")
    revisiones_activas = list(revisiones_activas_empresa(empresa.pk))
    revision = _revision_seguimiento(empresa, request)
    progreso = calcular_progreso_revision(revision) if revision else None
    documentos = progreso.documentos_pendientes_entrega if progreso else []
    criterios_huerfanos = progreso.criterios_huerfanos if progreso else []
    return render(
        request,
        "maestro/seguimiento_pendientes.html",
        {
            "empresa": empresa,
            "revision": revision,
            "revisiones_activas": revisiones_activas,
            "progreso": progreso,
            "documentos": documentos,
            "criterios_huerfanos": criterios_huerfanos,
        },
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_revision_plan(request: HttpRequest, pk: int, rev_id: int) -> HttpResponse:
    """
    Plan de entrega por documento (configuración previa al seguimiento).
    No genera notificaciones; plantilla de fechas límite del consultor.
    """
    from datetime import datetime as dt

    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)

    if request.method == "POST":
        docs = {
            d.pk: d
            for d in EmpresaDocumento.objects.filter(revision=revision, activo=True)
        }
        actualizados = 0
        errores = 0
        for doc_id, doc in docs.items():
            raw = (request.POST.get(f"fecha_limite_{doc_id}") or "").strip()
            nueva = None
            if raw:
                try:
                    nueva = dt.strptime(raw, "%Y-%m-%d").date()
                except ValueError:
                    errores += 1
                    continue
            if doc.fecha_limite_entrega != nueva:
                doc.fecha_limite_entrega = nueva
                doc.save(update_fields=["fecha_limite_entrega", "actualizado_en"])
                actualizados += 1
        if errores:
            messages.warning(
                request,
                f"Plan guardado con {actualizados} cambio(s). "
                f"{errores} fecha(s) con formato inválido se omitieron.",
            )
        else:
            messages.success(
                request,
                f"Plan de entrega guardado ({actualizados} documento(s) actualizado(s)).",
            )
        return redirect(
            "maestro:empresa_revision_plan",
            pk=empresa.pk,
            rev_id=revision.pk,
        )

    arbol = arbol_plan_entrega(revision)
    total_docs = sum(len(p.documentos) for t in arbol for p in t.principios)
    con_fecha = sum(
        1
        for t in arbol
        for p in t.principios
        for d in p.documentos
        if d.fecha_limite_entrega
    )
    return render(
        request,
        "maestro/empresa_plan.html",
        {
            "empresa": empresa,
            "revision": revision,
            "arbol": arbol,
            "total_docs": total_docs,
            "con_fecha": con_fecha,
        },
    )


def _aplicar_revision_parcial_carga(
    *,
    carga: DocumentoCarga,
    decisions: list,
    user,
    request: HttpRequest | None = None,
) -> tuple[int, int]:
    """Aplica decisiones por pregunta, sync criterios y carga. Devuelve (cumplen, no_cumplen)."""
    from .models import EstadoRevisionPregunta

    ahora = timezone.now()
    n_ok = 0
    n_no = 0
    vinculos_tocados: dict[int, EmpresaDocumentoCriterio] = {}
    for respuesta, estado, obs in decisions:
        respuesta.estado_revision = estado
        respuesta.revisado_por = user
        respuesta.revisado_en = ahora
        if estado == EstadoRevisionPregunta.CUMPLE:
            respuesta.observacion = ""
            n_ok += 1
        else:
            respuesta.observacion = obs
            n_no += 1
        respuesta.save(
            update_fields=[
                "estado_revision",
                "observacion",
                "revisado_por",
                "revisado_en",
                "actualizado_en",
            ]
        )
        vinculos_tocados[respuesta.vinculo_criterio_id] = respuesta.vinculo_criterio
        if estado == EstadoRevisionPregunta.NO_CUMPLE:
            registrar_mensaje(
                empresa_documento=carga.empresa_documento,
                texto=f"{respuesta.pregunta.codigo}: {obs}",
                autor=user,
                es_consultor=True,
                tipo=TipoDocumentoMensaje.OBSERVACION,
                carga=carga,
                criterio=respuesta.vinculo_criterio.criterio,
            )
    for vinculo in vinculos_tocados.values():
        sincronizar_estado_criterio_desde_preguntas(vinculo, revisado_por=user)
    sincronizar_estado_carga_documento(
        carga.empresa_documento, revisado_por=user
    )
    if request is not None:
        from .auditoria import auditar_aprobacion

        carga.refresh_from_db()
        auditar_aprobacion(
            request,
            carga=carga,
            parcial=carga.estado_revision == EstadoRevisionCarga.PARCIAL,
        )
    return n_ok, n_no


@gestor_o_admin_required
@require_http_methods(["POST"])
def seguimiento_documento_revision_consultor(
    request: HttpRequest, pk: int, doc_id: int
) -> HttpResponse:
    """Define o actualiza la etiqueta/% de revisión del consultor (no afecta el avance)."""
    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        return redirect("maestro:seguimiento_list")
    doc = get_object_or_404(
        EmpresaDocumento.objects.select_related("revision"),
        pk=doc_id,
        revision__empresa=empresa,
    )
    form = DocumentoRevisionConsultorForm(request.POST)
    next_url = (request.POST.get("next") or "").strip()
    fallback = _seguimiento_url(
        "maestro:seguimiento_empresa_revision",
        empresa.pk,
        doc.revision,
    )
    if not form.is_valid():
        for err in form.non_field_errors():
            messages.error(request, err)
        for field_errs in form.errors.values():
            for err in field_errs:
                messages.error(request, err)
        if not form.errors:
            messages.error(request, "Revisa la etiqueta, el porcentaje y la fecha.")
        return redirect(next_url or fallback)

    texto = (form.cleaned_data.get("revision_texto") or "").strip()
    pct = form.cleaned_data.get("revision_porcentaje")
    fecha_hito = form.cleaned_data.get("fecha_hito")
    doc.revision_texto = texto
    doc.revision_porcentaje = pct
    doc.save(update_fields=["revision_texto", "revision_porcentaje", "actualizado_en"])
    if texto and fecha_hito:
        EmpresaDocumentoEtapaHistorial.objects.create(
            empresa_documento=doc,
            etapa_texto=texto,
            fecha_hito=fecha_hito,
            revision_porcentaje=pct,
            registrado_por=request.user,
        )
        msg = (
            f"{doc.codigo}: etapa «{texto}» registrada "
            f"({fecha_hito:%d/%m/%Y})"
        )
        if pct is not None:
            msg += f" · {pct}%"
        messages.success(request, msg + ".")
    elif texto or pct is not None:
        msg = f"{doc.codigo}: revisión «{texto or '—'}»"
        if pct is not None:
            msg += f" · {pct}%"
        messages.success(request, msg + ".")
    else:
        messages.success(request, f"{doc.codigo}: se quitó la revisión del consultor.")
    return redirect(next_url or fallback)


@gestor_o_admin_required
@require_http_methods(["POST"])
def seguimiento_carga_revisar(request: HttpRequest, pk: int, carga_id: int) -> HttpResponse:
    """Revisión parcial: aprobar u observar criterios individuales (o aprobar todos)."""
    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        return redirect("maestro:seguimiento_list")
    carga = get_object_or_404(
        DocumentoCarga.objects.select_related(
            "empresa_documento", "empresa_documento__revision"
        ),
        pk=carga_id,
        activo=True,
        empresa_documento__revision__empresa=empresa,
    )
    doc = carga.empresa_documento
    revision = doc.revision
    if doc.origen == OrigenEmpresaDocumento.PROPIO and not doc.vinculos_criterio.exists():
        messages.error(
            request,
            "Antes de revisar un documento del cliente debes asociarle al menos un criterio huérfano.",
        )
        return redirect(
            "maestro:seguimiento_documento_asociar_criterios",
            pk=empresa.pk,
            doc_id=doc.pk,
        )
    if carga.estado_revision not in (
        EstadoRevisionCarga.PENDIENTE,
        EstadoRevisionCarga.PARCIAL,
    ):
        messages.info(request, "Esta evidencia ya no está pendiente de revisión.")
        return redirect(
            _seguimiento_url("maestro:seguimiento_empresa_revision", empresa.pk, revision)
        )

    form = RevisionParcialCargaForm(request.POST, carga=carga)
    if not form.is_valid():
        for err in form.non_field_errors():
            messages.error(request, err)
        if not form.non_field_errors():
            messages.error(request, "No se pudo guardar la revisión parcial.")
        return redirect(
            _seguimiento_url("maestro:seguimiento_empresa_revision", empresa.pk, revision)
        )

    decisions = form.cleaned_data["decisions"]
    ubicaciones = form.cleaned_data.get("ubicaciones") or []
    # Aprobación total solo se confirma al guardar vigencia.
    if decisions and _quedaria_aprobado(carga, decisions) and not _doc_tiene_vigencia(doc):
        _guardar_aprobacion_pendiente(
            request,
            empresa=empresa,
            doc=doc,
            carga=carga,
            decisions=decisions,
            ubicaciones=ubicaciones,
        )
        messages.info(
            request,
            f"{doc.codigo}: para confirmar la aprobación debes registrar la vigencia "
            "(frecuencia, Rev. 0 y caducidad). Si cancelas, no se aprueba.",
        )
        return redirect(
            "maestro:seguimiento_documento_vigencia",
            pk=empresa.pk,
            doc_id=doc.pk,
        )

    _aplicar_ubicaciones_criterio(ubicaciones)
    if decisions:
        n_ok, n_no = _aplicar_revision_parcial_carga(
            carga=carga,
            decisions=decisions,
            user=request.user,
            request=request,
        )
    else:
        n_ok, n_no = 0, 0
    carga.refresh_from_db()
    if decisions and carga.estado_revision == EstadoRevisionCarga.APROBADO:
        messages.success(
            request,
            f"{doc.codigo}: documento aprobado (vigencia ya estaba registrada).",
        )
    else:
        parts = []
        if n_ok:
            parts.append(f"{n_ok} pregunta(s) cumplen")
        if n_no:
            parts.append(f"{n_no} no cumplen")
        if not decisions and ubicaciones:
            parts.append("ubicación (páginas/fragmento) actualizada")
        messages.success(
            request,
            f"{doc.codigo}: {', '.join(parts) or 'revisión guardada'}. "
            "El avance parcial de las preguntas ya suma a criterios, requisitos, principios y temas.",
        )
    return redirect(
        _seguimiento_url("maestro:seguimiento_empresa_revision", empresa.pk, revision)
    )


@gestor_o_admin_required
@require_http_methods(["POST"])
def seguimiento_carga_aprobar(request: HttpRequest, pk: int, carga_id: int) -> HttpResponse:
    """Atajo: aprobar todos los criterios pendientes del documento."""
    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        return redirect("maestro:seguimiento_list")
    carga = get_object_or_404(
        DocumentoCarga.objects.select_related(
            "empresa_documento", "empresa_documento__revision"
        ),
        pk=carga_id,
        activo=True,
        empresa_documento__revision__empresa=empresa,
    )
    doc = carga.empresa_documento
    revision = doc.revision
    if doc.origen == OrigenEmpresaDocumento.PROPIO and not doc.vinculos_criterio.exists():
        messages.error(
            request,
            "Antes de aprobar un documento del cliente debes asociarle al menos un criterio huérfano.",
        )
        return redirect(
            "maestro:seguimiento_documento_asociar_criterios",
            pk=empresa.pk,
            doc_id=doc.pk,
        )
    if carga.estado_revision not in (
        EstadoRevisionCarga.PENDIENTE,
        EstadoRevisionCarga.PARCIAL,
    ):
        messages.info(request, "Esta evidencia ya no está pendiente de revisión.")
        return redirect(
            _seguimiento_url("maestro:seguimiento_empresa_revision", empresa.pk, revision)
        )
    form = RevisionParcialCargaForm({"accion": "aprobar_todos"}, carga=carga)
    if not form.is_valid():
        messages.error(request, "No hay preguntas pendientes para marcar como cumplen.")
        return redirect(
            _seguimiento_url("maestro:seguimiento_empresa_revision", empresa.pk, revision)
        )
    decisions = form.cleaned_data["decisions"]
    ubicaciones = form.cleaned_data.get("ubicaciones") or []
    if _quedaria_aprobado(carga, decisions) and not _doc_tiene_vigencia(doc):
        _guardar_aprobacion_pendiente(
            request,
            empresa=empresa,
            doc=doc,
            carga=carga,
            decisions=decisions,
            ubicaciones=ubicaciones,
        )
        messages.info(
            request,
            f"{doc.codigo}: para confirmar la aprobación debes registrar la vigencia. "
            "Si cancelas, el documento no se aprueba.",
        )
        return redirect(
            "maestro:seguimiento_documento_vigencia",
            pk=empresa.pk,
            doc_id=doc.pk,
        )
    _aplicar_ubicaciones_criterio(ubicaciones)
    _aplicar_revision_parcial_carga(
        carga=carga,
        decisions=decisions,
        user=request.user,
        request=request,
    )
    messages.success(
        request,
        f"Evidencia de {doc.codigo}: documento aprobado (vigencia ya registrada).",
    )
    return redirect(
        _seguimiento_url("maestro:seguimiento_empresa_revision", empresa.pk, revision)
    )


@gestor_o_admin_required
@require_http_methods(["GET", "POST"])
def seguimiento_documento_vigencia(
    request: HttpRequest, pk: int, doc_id: int
) -> HttpResponse:
    """Paso obligatorio: vigencia. Sin guardar, la aprobación no se confirma."""
    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        return redirect("maestro:seguimiento_list")
    doc = get_object_or_404(
        EmpresaDocumento.objects.select_related("revision"),
        pk=doc_id,
        revision__empresa=empresa,
    )
    carga = (
        DocumentoCarga.objects.filter(empresa_documento=doc, activo=True)
        .order_by("-creado_en")
        .first()
    )
    pendiente = _leer_aprobacion_pendiente(request, doc.pk)
    aprobado_sin_vigencia = bool(
        carga
        and carga.estado_revision == EstadoRevisionCarga.APROBADO
        and not _doc_tiene_vigencia(doc)
    )
    aprobado_con_vigencia = bool(
        carga
        and carga.estado_revision == EstadoRevisionCarga.APROBADO
        and _doc_tiene_vigencia(doc)
    )
    if not pendiente and not aprobado_sin_vigencia and not aprobado_con_vigencia:
        messages.warning(
            request,
            "No hay una aprobación pendiente. Vuelve a marcar el documento como cumple.",
        )
        return redirect(
            _seguimiento_url(
                "maestro:seguimiento_empresa_revision", empresa.pk, doc.revision
            )
        )

    # Cancelar: revierte / descarta aprobación
    if request.method == "POST" and (request.POST.get("accion") or "").strip() == "cancelar":
        if pendiente:
            _limpiar_aprobacion_pendiente(request)
            messages.warning(
                request,
                f"{doc.codigo}: se canceló el registro de vigencia. "
                "La aprobación no se aplicó; el documento sigue en su estado anterior.",
            )
        elif aprobado_sin_vigencia:
            _revertir_aprobacion_sin_vigencia(doc, user=request.user)
            messages.warning(
                request,
                f"{doc.codigo}: se revirtió la aprobación porque la vigencia es obligatoria.",
            )
        else:
            messages.info(request, "No había cambios que cancelar.")
        return redirect(
            _seguimiento_url(
                "maestro:seguimiento_empresa_revision", empresa.pk, doc.revision
            )
        )

    initial = {
        "frecuencia_actualizacion": doc.frecuencia_actualizacion or "ANUAL",
        "frecuencia_otro": doc.frecuencia_otro or "",
        "fecha_ultima_revision": doc.fecha_ultima_revision or timezone.localdate(),
        "fecha_caducidad": doc.fecha_caducidad,
    }
    if request.method == "POST":
        form = DocumentoVigenciaAprobacionForm(request.POST)
    else:
        form = DocumentoVigenciaAprobacionForm(initial=initial)

    if request.method == "POST" and form.is_valid():
        # Primero vigencia, luego (si hay pendiente) aplicar la aprobación.
        doc.frecuencia_actualizacion = form.cleaned_data["frecuencia_actualizacion"]
        doc.frecuencia_otro = form.cleaned_data.get("frecuencia_otro") or ""
        doc.fecha_ultima_revision = form.cleaned_data["fecha_ultima_revision"]
        doc.fecha_caducidad = form.cleaned_data["fecha_caducidad"]
        doc.save(
            update_fields=[
                "frecuencia_actualizacion",
                "frecuencia_otro",
                "fecha_ultima_revision",
                "fecha_caducidad",
                "actualizado_en",
            ]
        )
        if pendiente:
            _aplicar_ubicaciones_criterio(
                _ubicaciones_desde_pendiente(pendiente, doc)
            )
            decisions = _decisions_desde_pendiente(pendiente, doc)
            if decisions:
                _aplicar_revision_parcial_carga(
                    carga=carga,
                    decisions=decisions,
                    user=request.user,
                    request=request,
                )
            _limpiar_aprobacion_pendiente(request)
        freq_txt = doc.get_frecuencia_actualizacion_display()
        if doc.frecuencia_actualizacion == "OTRO" and doc.frecuencia_otro:
            freq_txt = f"{freq_txt}: {doc.frecuencia_otro}"
        registrar_mensaje(
            empresa_documento=doc,
            texto=(
                "Documento aprobado con vigencia: "
                f"frecuencia {freq_txt}, "
                f"Rev. 0 {doc.fecha_ultima_revision:%d/%m/%Y}, "
                f"caduca {doc.fecha_caducidad:%d/%m/%Y}."
            ),
            autor=request.user,
            es_consultor=True,
            tipo=TipoDocumentoMensaje.SISTEMA,
            carga=carga,
        )
        messages.success(
            request,
            f"{doc.codigo}: vigencia guardada y documento aprobado.",
        )
        return redirect(
            _seguimiento_url(
                "maestro:seguimiento_empresa_revision", empresa.pk, doc.revision
            )
        )

    return render(
        request,
        "maestro/seguimiento_documento_vigencia.html",
        {
            "empresa": empresa,
            "documento": doc,
            "revision": doc.revision,
            "carga": carga,
            "form": form,
            "pendiente_aprobacion": bool(pendiente),
            "aprobado_sin_vigencia": aprobado_sin_vigencia,
        },
    )


@gestor_o_admin_required
@require_http_methods(["POST"])
def seguimiento_carga_revertir(request: HttpRequest, pk: int, carga_id: int) -> HttpResponse:
    """Revierte aprobación de un criterio o de todos los aprobados (error de usuario)."""
    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        return redirect("maestro:seguimiento_list")
    carga = get_object_or_404(
        DocumentoCarga.objects.select_related(
            "empresa_documento", "empresa_documento__revision"
        ),
        pk=carga_id,
        activo=True,
        empresa_documento__revision__empresa=empresa,
    )
    doc = carga.empresa_documento
    revision = doc.revision
    next_url = (request.POST.get("next") or "").strip()
    fallback = _seguimiento_url(
        "maestro:seguimiento_empresa_revision", empresa.pk, revision
    )

    from .models import EmpresaDocumentoPregunta, EstadoRevisionPregunta

    vinculo_id = (request.POST.get("vinculo_id") or "").strip()
    pregunta_id = (request.POST.get("pregunta_id") or "").strip()
    ahora = timezone.now()

    if pregunta_id.isdigit():
        respuesta = get_object_or_404(
            EmpresaDocumentoPregunta.objects.select_related(
                "pregunta", "vinculo_criterio", "vinculo_criterio__criterio"
            ),
            pk=int(pregunta_id),
            vinculo_criterio__empresa_documento=doc,
            estado_revision=EstadoRevisionPregunta.CUMPLE,
        )
        respuesta.estado_revision = EstadoRevisionPregunta.PENDIENTE
        respuesta.observacion = ""
        respuesta.revisado_por = request.user
        respuesta.revisado_en = ahora
        respuesta.save(
            update_fields=[
                "estado_revision",
                "observacion",
                "revisado_por",
                "revisado_en",
                "actualizado_en",
            ]
        )
        sincronizar_estado_criterio_desde_preguntas(
            respuesta.vinculo_criterio, revisado_por=request.user
        )
        sincronizar_estado_carga_documento(doc, revisado_por=request.user)
        texto = (
            f"Se revirtió «cumple» de la pregunta {respuesta.pregunta.codigo}. "
            "Vuelve a quedar pendiente."
        )
        registrar_mensaje(
            empresa_documento=doc,
            texto=texto,
            autor=request.user,
            es_consultor=True,
            tipo=TipoDocumentoMensaje.SISTEMA,
            carga=carga,
            criterio=respuesta.vinculo_criterio.criterio,
        )
        messages.success(request, f"{doc.codigo}: {texto}")
        return redirect(next_url or fallback)

    qs = EmpresaDocumentoCriterio.objects.filter(
        empresa_documento=doc,
        estado_revision=EstadoRevisionCriterio.APROBADO,
    ).select_related("criterio")
    if vinculo_id.isdigit():
        qs = qs.filter(pk=int(vinculo_id))
    vinculos = list(qs)
    if not vinculos:
        # También permitir revertir todas las preguntas CUMPLE del documento
        respuestas = list(
            EmpresaDocumentoPregunta.objects.filter(
                vinculo_criterio__empresa_documento=doc,
                estado_revision=EstadoRevisionPregunta.CUMPLE,
            ).select_related("pregunta", "vinculo_criterio", "vinculo_criterio__criterio")
        )
        if not respuestas:
            messages.info(request, "No hay aprobaciones para revertir en este documento.")
            return redirect(next_url or fallback)
        for respuesta in respuestas:
            respuesta.estado_revision = EstadoRevisionPregunta.PENDIENTE
            respuesta.observacion = ""
            respuesta.revisado_por = request.user
            respuesta.revisado_en = ahora
            respuesta.save(
                update_fields=[
                    "estado_revision",
                    "observacion",
                    "revisado_por",
                    "revisado_en",
                    "actualizado_en",
                ]
            )
        tocados = {r.vinculo_criterio_id: r.vinculo_criterio for r in respuestas}
        for vinculo in tocados.values():
            sincronizar_estado_criterio_desde_preguntas(
                vinculo, revisado_por=request.user
            )
        sincronizar_estado_carga_documento(doc, revisado_por=request.user)
        texto = (
            f"Se revirtió el cumplimiento de {len(respuestas)} pregunta(s). "
            "Vuelven a quedar pendientes."
        )
        registrar_mensaje(
            empresa_documento=doc,
            texto=texto,
            autor=request.user,
            es_consultor=True,
            tipo=TipoDocumentoMensaje.SISTEMA,
            carga=carga,
        )
        messages.success(request, f"{doc.codigo}: {texto}")
        return redirect(next_url or fallback)

    codigos: list[str] = []
    for vinculo in vinculos:
        EmpresaDocumentoPregunta.objects.filter(
            vinculo_criterio=vinculo,
            estado_revision=EstadoRevisionPregunta.CUMPLE,
        ).update(
            estado_revision=EstadoRevisionPregunta.PENDIENTE,
            observacion="",
            revisado_por=request.user,
            revisado_en=ahora,
        )
        sincronizar_estado_criterio_desde_preguntas(
            vinculo, revisado_por=request.user
        )
        codigos.append(vinculo.criterio.codigo)

    sincronizar_estado_carga_documento(doc, revisado_por=request.user)
    carga.refresh_from_db()

    if len(codigos) == 1:
        texto = (
            f"Se revirtió el cumplimiento del criterio {codigos[0]} "
            "(todas sus preguntas). Vuelve a quedar pendiente."
        )
    else:
        texto = (
            f"Se revirtió el cumplimiento de {len(codigos)} criterio(s) "
            f"({', '.join(codigos)}). Vuelven a quedar pendientes."
        )
    registrar_mensaje(
        empresa_documento=doc,
        texto=texto,
        autor=request.user,
        es_consultor=True,
        tipo=TipoDocumentoMensaje.SISTEMA,
        carga=carga,
        criterio=vinculos[0].criterio if len(vinculos) == 1 else None,
    )
    messages.success(request, f"{doc.codigo}: {texto}")
    return redirect(next_url or fallback)


@gestor_o_admin_required
@require_http_methods(["POST"])
def seguimiento_carga_rechazar(request: HttpRequest, pk: int, carga_id: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        return redirect("maestro:seguimiento_list")
    carga = get_object_or_404(
        DocumentoCarga.objects.select_related(
            "empresa_documento", "empresa_documento__revision"
        ),
        pk=carga_id,
        activo=True,
        empresa_documento__revision__empresa=empresa,
    )
    revision = carga.empresa_documento.revision
    form = RechazoDocumentoCargaForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Indica un motivo de rechazo claro para la minera (mín. 10 caracteres).")
        return redirect(
            _seguimiento_url("maestro:seguimiento_empresa_revision", empresa.pk, revision)
        )
    if carga.estado_revision not in (
        EstadoRevisionCarga.PENDIENTE,
        EstadoRevisionCarga.PARCIAL,
    ):
        messages.info(request, "Esta evidencia ya fue revisada.")
        return redirect(
            _seguimiento_url("maestro:seguimiento_empresa_revision", empresa.pk, revision)
        )
    hay_aprobados = EmpresaDocumentoCriterio.objects.filter(
        empresa_documento=carga.empresa_documento,
        estado_revision=EstadoRevisionCriterio.APROBADO,
    ).exists()
    if hay_aprobados:
        messages.error(
            request,
            "Ya hay criterios aprobados en este documento. "
            "Usa observaciones por criterio en lugar de rechazar toda la evidencia.",
        )
        return redirect(
            _seguimiento_url("maestro:seguimiento_empresa_revision", empresa.pk, revision)
        )

    carga.estado_revision = EstadoRevisionCarga.RECHAZADO
    carga.motivo_rechazo = form.cleaned_data["motivo_rechazo"].strip()
    carga.revisado_por = request.user
    carga.revisado_en = timezone.now()
    carga.save(
        update_fields=[
            "estado_revision",
            "motivo_rechazo",
            "revisado_por",
            "revisado_en",
        ]
    )
    from .auditoria import auditar_rechazo

    auditar_rechazo(request, carga=carga)
    registrar_mensaje(
        empresa_documento=carga.empresa_documento,
        texto=carga.motivo_rechazo,
        autor=request.user,
        es_consultor=True,
        tipo=TipoDocumentoMensaje.RECHAZO,
        carga=carga,
    )
    messages.success(
        request,
        f"Evidencia de {carga.empresa_documento.codigo} rechazada. El cliente verá el motivo en la conversación.",
    )
    return redirect(
        _seguimiento_url("maestro:seguimiento_empresa_revision", empresa.pk, revision)
    )


@gestor_o_admin_required
@require_GET
def seguimiento_list(request: HttpRequest) -> HttpResponse:
    empresas = list(empresas_visibles(request.user).filter(activo=True))
    filas = []
    for empresa in empresas:
        revisiones = list(revisiones_activas_empresa(empresa.pk))
        if not revisiones:
            filas.append({"empresa": empresa, "revision": None, "progreso": None})
            continue
        for revision in revisiones:
            filas.append(
                {
                    "empresa": empresa,
                    "revision": revision,
                    "progreso": calcular_progreso_revision(revision),
                }
            )
    return render(
        request,
        "maestro/seguimiento_list.html",
        {"filas": filas},
    )


@gestor_o_admin_required
@require_GET
def seguimiento_empresa_detail(request: HttpRequest, pk: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        return redirect("maestro:seguimiento_list")
    revisiones_activas = list(revisiones_activas_empresa(empresa.pk))
    revision = _revision_seguimiento(empresa, request)
    progreso = calcular_progreso_revision(revision) if revision else None
    docs_tab = (request.GET.get("docs") or "aprobados").strip().lower()
    if docs_tab not in {"aprobados", "todos", "pendientes"}:
        docs_tab = "aprobados"
    origen_tab = (request.GET.get("origen") or "todos").strip().lower()
    if origen_tab not in {"todos", "precargados", "cliente"}:
        origen_tab = "todos"

    documentos_filtrados: list = []
    if progreso:
        # Detalle limpio: solo documentos aplicables (con criterios).
        if docs_tab == "pendientes":
            base = list(progreso.documentos_pendientes_entrega)
        else:
            base = list(progreso.documentos)
            if docs_tab == "aprobados":
                base = [d for d in base if d.cargado]
        if origen_tab == "precargados":
            documentos_filtrados = [d for d in base if d.es_catalogo]
        elif origen_tab == "cliente":
            documentos_filtrados = [d for d in base if d.es_propio]
        else:
            documentos_filtrados = base

    return render(
        request,
        "maestro/seguimiento_empresa.html",
        {
            "empresa": empresa,
            "revision": revision,
            "revisiones_activas": revisiones_activas,
            "progreso": progreso,
            "docs_tab": docs_tab,
            "origen_tab": origen_tab,
            "documentos_filtrados": documentos_filtrados,
        },
    )


