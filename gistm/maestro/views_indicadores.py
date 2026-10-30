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


from .indicadores_export import exportar_documentos_excel, exportar_documentos_pdf
from .view_helpers import revision_seguimiento as _revision_seguimiento


@gestor_admin_o_cliente_required
@require_GET
def indicadores_list(request: HttpRequest) -> HttpResponse:
    if usuario_es_cliente(request.user):
        perfil = get_perfil(request.user)
        return redirect("maestro:indicadores_empresa", pk=perfil.empresa_id)
    empresas = list(empresas_visibles(request.user).filter(activo=True))
    filas = filas_empresas_indicadores(empresas)
    return render(request, "maestro/indicadores_list.html", {"filas": filas})


def _parse_fecha_get(raw: str):
    from datetime import datetime as dt

    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return dt.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _documentos_indicadores_filtrados(request: HttpRequest, empresa, revision, progreso):
    """Lista de documentos de indicadores con subtab + filtros de query."""
    docs_sub = (request.GET.get("docs") or "todos").strip().lower()
    if docs_sub not in DOC_SUBTABS:
        docs_sub = "todos"
    todos = documentos_indicadores(revision, progreso, empresa=empresa)
    base = filtrar_documentos(todos, docs_sub)
    filtrados = filtrar_documentos_query(
        base,
        q=(request.GET.get("doc_q") or "").strip(),
        consultor=(request.GET.get("consultor") or "").strip(),
        area=(request.GET.get("area") or "").strip(),
        estado=(request.GET.get("estado") or "").strip(),
        fecha_desde=_parse_fecha_get(request.GET.get("fecha_desde") or ""),
        fecha_hasta=_parse_fecha_get(request.GET.get("fecha_hasta") or ""),
    )
    return todos, docs_sub, filtrados


@gestor_admin_o_cliente_required
@require_GET
def indicadores_empresa(request: HttpRequest, pk: int) -> HttpResponse:
    from django.core.paginator import Paginator

    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        if usuario_es_cliente(request.user):
            return redirect("maestro:home")
        return redirect("maestro:indicadores_list")

    tab = (request.GET.get("tab") or "general").strip().lower()
    if tab not in TABS:
        tab = "general"
    docs_sub = (request.GET.get("docs") or "todos").strip().lower()
    if docs_sub not in DOC_SUBTABS:
        docs_sub = "todos"
    glosario_q = (request.GET.get("q") or "").strip()

    es_cliente = usuario_es_cliente(request.user)
    if es_cliente:
        revisiones_activas = list(revisiones_visibles_cliente(request.user))
        revision = revision_cliente_actual(request)
        if revision and revision.empresa_id != empresa.pk:
            revision = None
    else:
        revisiones_activas = list(revisiones_activas_empresa(empresa.pk))
        revision = _revision_seguimiento(empresa, request)
    progreso = calcular_progreso_revision(revision) if revision else None

    context: dict = {
        "empresa": empresa,
        "revision": revision,
        "revisiones_activas": revisiones_activas,
        "progreso": progreso,
        "tab": tab,
        "tabs": TABS,
        "docs_sub": docs_sub,
        "glosario_q": glosario_q,
        "solo_lectura": es_cliente,
        "principios_avance": [],
        "documentos_ind": [],
        "cobertura": None,
        "glosario": [],
        "glosario_json": [],
        # Objetos Python → |json_script en la plantilla (sin |safe / XSS).
        "chart_general": {},
        "chart_temas": {},
        "chart_principios": {},
        "chart_tablero_progreso": {},
        "chart_tablero_estandar": {},
        "chart_curva": {},
        "docs_prioritarios": [],
        "docs_por_vencer": [],
        "dias_por_vencer": DIAS_POR_VENCER,
        "docs_filtros": {
            "doc_q": (request.GET.get("doc_q") or "").strip(),
            "consultor": (request.GET.get("consultor") or "").strip(),
            "area": (request.GET.get("area") or "").strip(),
            "estado": (request.GET.get("estado") or "").strip(),
            "fecha_desde": (request.GET.get("fecha_desde") or "").strip(),
            "fecha_hasta": (request.GET.get("fecha_hasta") or "").strip(),
        },
        "docs_opciones": {"areas": [], "estados": [], "consultores": []},
        "docs_page_obj": None,
        "docs_page_numbers": [],
        "docs_total_filtrados": 0,
    }

    if progreso:
        principios = avance_por_principios(progreso)
        context["principios_avance"] = principios
        context["chart_general"] = chart_general(progreso)
        context["chart_temas"] = chart_temas(progreso)
        context["chart_principios"] = chart_principios(principios)
        if tab == "progreso":
            context["chart_tablero_progreso"] = tablero_progreso(
                progreso, empresa=empresa
            )
        if tab == "general":
            context["chart_curva"] = curva_avance(revision, progreso)
            context["docs_prioritarios"] = documentos_prioritarios(
                revision, progreso
            )
            context["docs_por_vencer"] = documentos_por_vencer(revision, progreso)
        if tab == "documentos":
            todos, docs_sub, filtrados = _documentos_indicadores_filtrados(
                request, empresa, revision, progreso
            )
            context["docs_sub"] = docs_sub
            context["docs_count_todos"] = len(todos)
            context["docs_count_ref"] = len(filtrar_documentos(todos, "referenciales"))
            context["docs_count_multi"] = len(
                filtrar_documentos(todos, "multidisciplinario")
            )
            context["docs_count_multiprincipio"] = len(
                filtrar_documentos(todos, "multiprincipio")
            )
            # Opciones de filtro sobre el subtab (sin filtros avanzados)
            base_sub = filtrar_documentos(todos, docs_sub)
            context["docs_opciones"] = opciones_filtros_documentos(base_sub)
            context["docs_total_filtrados"] = len(filtrados)
            paginator = Paginator(filtrados, 15)
            page_obj = paginator.get_page(request.GET.get("page") or 1)
            context["docs_page_obj"] = page_obj
            context["documentos_ind"] = list(page_obj.object_list)
            # Ventana de páginas alrededor de la actual (±2) + primera/última
            total_pages = paginator.num_pages
            current = page_obj.number
            window = set(range(max(1, current - 2), min(total_pages, current + 2) + 1))
            window.add(1)
            window.add(total_pages)
            page_nums: list[int | None] = []
            prev_n: int | None = None
            for n in sorted(window):
                if prev_n is not None and n - prev_n > 1:
                    page_nums.append(None)  # ellipsis
                page_nums.append(n)
                prev_n = n
            context["docs_page_numbers"] = page_nums
        if tab == "estandar" and revision:
            context["cobertura"] = cobertura_estandar(revision, progreso)

    if tab == "estandar":
        context["chart_tablero_estandar"] = tablero_estandar()

    if tab == "glosario":
        items = glosario_items(glosario_q)
        context["glosario"] = items
        context["glosario_json"] = [
            {"id": i.id, "concepto": i.concepto, "descripcion": i.descripcion}
            for i in items
        ]

    return render(request, "maestro/indicadores_empresa.html", context)


@gestor_admin_o_cliente_required
@require_GET
def indicadores_documentos_export(request: HttpRequest, pk: int, formato: str) -> HttpResponse:
    from .indicadores_export import exportar_documentos_excel, exportar_documentos_pdf

    empresa = get_object_or_404(Empresa, pk=pk)
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta empresa.")
        if usuario_es_cliente(request.user):
            return redirect("maestro:home")
        return redirect("maestro:indicadores_list")
    revision = _revision_seguimiento(empresa, request)
    if not revision:
        messages.warning(request, "No hay revisión activa para exportar.")
        return redirect("maestro:indicadores_empresa", pk=empresa.pk)
    progreso = calcular_progreso_revision(revision)
    _todos, _sub, filtrados = _documentos_indicadores_filtrados(
        request, empresa, revision, progreso
    )
    etiqueta = revision.etiqueta
    from .auditoria import auditar_exportacion

    if formato == "xlsx":
        auditar_exportacion(
            request,
            empresa=empresa,
            detalle=f"Exportación Excel de documentos · {etiqueta}",
            objeto_tipo="revision",
            objeto_id=revision.pk,
        )
        return exportar_documentos_excel(
            filtrados,
            empresa_nombre=empresa.nombre,
            revision_etiqueta=etiqueta,
        )
    if formato == "pdf":
        auditar_exportacion(
            request,
            empresa=empresa,
            detalle=f"Exportación PDF de documentos · {etiqueta}",
            objeto_tipo="revision",
            objeto_id=revision.pk,
        )
        return exportar_documentos_pdf(
            filtrados,
            empresa_nombre=empresa.nombre,
            revision_etiqueta=etiqueta,
        )
    messages.error(request, "Formato de exportación no válido.")
    return redirect(
        f"{reverse('maestro:indicadores_empresa', args=[empresa.pk])}"
        f"?tab=documentos&rev={revision.pk}"
    )


