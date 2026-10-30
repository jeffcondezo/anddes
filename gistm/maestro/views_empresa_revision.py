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

@administrador_required
@require_GET
def empresa_revision_list(request: HttpRequest, pk: int) -> HttpResponse:
    from .models import RevisionCatalogoCriterio

    empresa = get_object_or_404(Empresa, pk=pk)
    revisiones = []
    for revision in Revision.objects.filter(empresa=empresa).annotate(
        total_documentos=Count("documentos"),
        docs_activos=Count("documentos", filter=Q(documentos__activo=True)),
    ):
        asegurar_snapshot_catalogo_revision(revision)
        desactivados_ids = set(
            RevisionCriterioDesactivado.objects.filter(
                revision=revision
            ).values_list("criterio_id", flat=True)
        )
        snap_ids = set(
            RevisionCatalogoCriterio.objects.filter(revision=revision).values_list(
                "criterio_id", flat=True
            )
        )
        total_aplicables = len(snap_ids - desactivados_ids)
        criterios_cubiertos = (
            EmpresaDocumentoCriterio.objects.filter(
                empresa_documento__revision=revision,
                empresa_documento__activo=True,
                criterio_id__in=snap_ids,
            )
            .exclude(criterio_id__in=desactivados_ids)
            .values("criterio_id")
            .distinct()
            .count()
        )
        avance_pct = (
            round((criterios_cubiertos / total_aplicables) * 100, 1) if total_aplicables else 0
        )
        revisiones.append(
            {
                "obj": revision,
                "criterios_cubiertos": criterios_cubiertos,
                "total_criterios": total_aplicables,
                "avance_pct": avance_pct,
            }
        )
    return render(
        request,
        "maestro/empresa_revision_list.html",
        {"empresa": empresa, "revisiones": revisiones},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_revision_create(request: HttpRequest, pk: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    form = RevisionForm(request.POST or None, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        revision = form.save()
        crear_snapshot_catalogo_revision(revision)
        aplicar_deshabilitados_catalogo_a_revision(revision)
        messages.success(
            request,
            "Revisión creada. Se congeló el catálogo GISTM aplicable a esta revisión.",
        )
        return redirect("maestro:empresa_revision_list", pk=empresa.pk)
    return render(
        request,
        "maestro/empresa_revision_form.html",
        {"empresa": empresa, "form": form, "titulo": "Nueva revisión"},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_revision_edit(request: HttpRequest, pk: int, rev_id: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    form = RevisionForm(request.POST or None, instance=revision, empresa=empresa)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Revisión actualizada correctamente.")
        return redirect("maestro:empresa_revision_list", pk=empresa.pk)
    return render(
        request,
        "maestro/empresa_revision_form.html",
        {
            "empresa": empresa,
            "revision": revision,
            "form": form,
            "titulo": "Editar revisión",
        },
    )


@administrador_required
@require_GET
def empresa_documento_list(request: HttpRequest, pk: int, rev_id: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    documentos_qs = (
        EmpresaDocumento.objects.filter(revision=revision)
        .select_related("consultor_responsable")
        .annotate(total_criterios=Count("vinculos_criterio"))
        .order_by("codigo")
    )
    consultores = consultores_candidatos_empresa(empresa)
    consultor_unico = consultores[0] if len(consultores) == 1 else None
    docs_total = documentos_qs.count()
    docs_activos = documentos_qs.filter(activo=True).count()

    doc_q = (request.GET.get("doc_q") or "").strip()
    doc_estado = (request.GET.get("doc_estado") or "").strip()
    if doc_q:
        documentos_qs = documentos_qs.filter(
            Q(codigo__icontains=doc_q)
            | Q(nombre__icontains=doc_q)
            | Q(tipo_documento__icontains=doc_q)
            | Q(origen__icontains=doc_q)
        )
    if doc_estado == "activo":
        documentos_qs = documentos_qs.filter(activo=True)
    elif doc_estado == "inactivo":
        documentos_qs = documentos_qs.filter(activo=False)

    documentos = list(documentos_qs)
    docs_sin_criterios = [
        d for d in documentos if d.activo and (d.total_criterios or 0) == 0
    ]
    docs_sin_criterios_count = len(docs_sin_criterios)

    from .models import RevisionCatalogoCriterio

    asegurar_snapshot_catalogo_revision(revision)
    desactivados_qs = RevisionCriterioDesactivado.objects.filter(
        revision=revision
    ).select_related("criterio", "criterio__requisito", "criterio__principio", "criterio__tema")
    desactivados_ids = set(desactivados_qs.values_list("criterio_id", flat=True))
    desactivados_map = {d.criterio_id: d for d in desactivados_qs}
    snap_ids = set(
        RevisionCatalogoCriterio.objects.filter(revision=revision).values_list(
            "criterio_id", flat=True
        )
    )
    snap_by_criterio = {
        s.criterio_id: s
        for s in RevisionCatalogoCriterio.objects.filter(revision=revision)
    }
    total_aplicables = len(snap_ids - desactivados_ids)

    vinculos_revision = EmpresaDocumentoCriterio.objects.filter(
        empresa_documento__revision=revision,
        empresa_documento__activo=True,
        criterio_id__in=snap_ids,
    ).exclude(criterio_id__in=desactivados_ids)
    criterios_cubiertos_ids = set(
        vinculos_revision.values_list("criterio_id", flat=True).distinct()
    )
    criterios_cubiertos = len(criterios_cubiertos_ids)
    avance_pct = (
        round((criterios_cubiertos / total_aplicables) * 100, 1) if total_aplicables else 0
    )
    criterios_huerfanos_count = total_aplicables - criterios_cubiertos
    criterios_desactivados_count = len(desactivados_ids)

    q = (request.GET.get("q") or "").strip()
    tab = (request.GET.get("tab") or "huerfanos").strip()
    if tab not in ("huerfanos", "cubiertos", "no_aplican"):
        tab = "huerfanos"

    criterios_base = (
        Criterio.objects.filter(pk__in=snap_ids)
        .select_related("requisito", "principio", "tema")
        .order_by("codigo")
    )
    if q:
        # Busca también en textos congelados del snapshot.
        snap_match_ids = {
            sid
            for sid, snap in snap_by_criterio.items()
            if q.lower()
            in " ".join(
                [
                    snap.codigo,
                    snap.descripcion,
                    snap.principio_codigo,
                    snap.requisito_codigo,
                    snap.tema_codigo,
                ]
            ).lower()
        }
        criterios_base = criterios_base.filter(
            Q(pk__in=snap_match_ids)
            | Q(codigo__icontains=q)
            | Q(descripcion__icontains=q)
            | Q(principio__codigo__icontains=q)
            | Q(requisito__codigo__icontains=q)
        )

    pesos = ponderaciones_revision(revision)

    if tab == "cubiertos":
        criterios = list(
            criterios_base.filter(pk__in=criterios_cubiertos_ids).prefetch_related(
                Prefetch(
                    "vinculos_empresa_documento",
                    queryset=EmpresaDocumentoCriterio.objects.filter(
                        empresa_documento__revision=revision,
                        empresa_documento__activo=True,
                    ).select_related("empresa_documento", "area"),
                    to_attr="vinculos_en_revision",
                )
            )
        )
    elif tab == "no_aplican":
        criterios = list(criterios_base.filter(pk__in=desactivados_ids & snap_ids))
    else:
        criterios = list(
            criterios_base.exclude(pk__in=criterios_cubiertos_ids).exclude(
                pk__in=desactivados_ids
            )
        )

    for criterio in criterios:
        snap = snap_by_criterio.get(criterio.pk)
        if snap:
            criterio.codigo = snap.codigo
            criterio.descripcion = snap.descripcion
        if tab == "no_aplican":
            criterio.desactivacion = desactivados_map.get(criterio.pk)
            criterio.ponderacion_revision = None
        else:
            criterio.ponderacion_revision = pesos.get(criterio.pk)

    return render(
        request,
        "maestro/empresa_documento_list.html",
        {
            "empresa": empresa,
            "revision": revision,
            "documentos": documentos,
            "docs_activos": docs_activos,
            "docs_total": docs_total,
            "docs_sin_criterios_count": docs_sin_criterios_count,
            "criterios_cubiertos": criterios_cubiertos,
            "criterios_huerfanos_count": criterios_huerfanos_count,
            "criterios_desactivados_count": criterios_desactivados_count,
            "total_criterios": total_aplicables,
            # Catálogo congelado de la revisión (incluye no aplicables).
            "total_catalogo": len(snap_ids),
            "avance_pct": avance_pct,
            "criterios": criterios,
            "filtro_q": q,
            "filtro_doc_q": doc_q,
            "filtro_doc_estado": doc_estado,
            "tab": tab,
            "consultores": consultores,
            "consultor_unico": consultor_unico,
        },
    )


@administrador_required
@require_http_methods(["POST"])
def empresa_documento_asignar_consultor(
    request: HttpRequest, pk: int, rev_id: int, doc_id: int
) -> HttpResponse:
    """Asigna o quita el consultor responsable de un documento de la revisión."""
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    documento = get_object_or_404(EmpresaDocumento, pk=doc_id, revision=revision)
    candidatos = {u.pk: u for u in consultores_candidatos_empresa(empresa)}
    raw = (request.POST.get("consultor_responsable") or "").strip()
    if not raw:
        documento.consultor_responsable = None
        documento.save(update_fields=["consultor_responsable", "actualizado_en"])
        messages.success(
            request,
            f"{documento.codigo}: se quitó el consultor responsable.",
        )
    elif raw.isdigit() and int(raw) in candidatos:
        documento.consultor_responsable = candidatos[int(raw)]
        documento.save(update_fields=["consultor_responsable", "actualizado_en"])
        nombre = (
            documento.consultor_responsable.get_full_name()
            or documento.consultor_responsable.email
        )
        messages.success(
            request,
            f"{documento.codigo}: consultor responsable → {nombre}.",
        )
    else:
        messages.error(request, "Consultor no válido para esta empresa.")
    return redirect("maestro:empresa_documento_list", pk=empresa.pk, rev_id=revision.pk)


@administrador_required
@require_http_methods(["POST"])
def empresa_criterios_desactivar(request: HttpRequest, pk: int, rev_id: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    motivo = (request.POST.get("motivo") or "").strip()
    ids_raw = request.POST.getlist("criterio_ids")
    tab = (request.POST.get("tab") or "huerfanos").strip()
    q = (request.POST.get("q") or "").strip()

    if not motivo:
        messages.error(request, "Debes indicar el motivo de desactivación.")
        return redirect(
            f"{reverse('maestro:empresa_documento_list', args=[empresa.pk, revision.pk])}"
            f"?tab={tab}" + (f"&q={q}" if q else "")
        )

    criterio_ids = [int(x) for x in ids_raw if str(x).isdigit()]
    if not criterio_ids:
        messages.error(request, "Selecciona al menos un criterio.")
        return redirect(
            f"{reverse('maestro:empresa_documento_list', args=[empresa.pk, revision.pk])}"
            f"?tab={tab}" + (f"&q={q}" if q else "")
        )

    creados = 0
    with transaction.atomic():
        for criterio in Criterio.objects.filter(pk__in=criterio_ids):
            _, created = RevisionCriterioDesactivado.objects.update_or_create(
                revision=revision,
                criterio=criterio,
                defaults={"motivo": motivo},
            )
            if created:
                creados += 1

    messages.success(
        request,
        f"Se marcaron {len(criterio_ids)} criterio(s) como no aplicables en esta revisión.",
    )
    return redirect(
        f"{reverse('maestro:empresa_documento_list', args=[empresa.pk, revision.pk])}"
        f"?tab=no_aplican" + (f"&q={q}" if q else "")
    )


@administrador_required
@require_http_methods(["POST"])
def empresa_criterios_reactivar(request: HttpRequest, pk: int, rev_id: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    ids_raw = request.POST.getlist("criterio_ids")
    q = (request.POST.get("q") or "").strip()
    criterio_ids = [int(x) for x in ids_raw if str(x).isdigit()]

    if not criterio_ids:
        messages.error(request, "Selecciona al menos un criterio para reactivar.")
        return redirect(
            f"{reverse('maestro:empresa_documento_list', args=[empresa.pk, revision.pk])}"
            f"?tab=no_aplican" + (f"&q={q}" if q else "")
        )

    eliminados, _ = RevisionCriterioDesactivado.objects.filter(
        revision=revision, criterio_id__in=criterio_ids
    ).delete()
    messages.success(
        request,
        f"Se reactivaron {eliminados} criterio(s) para esta revisión.",
    )
    return redirect(
        f"{reverse('maestro:empresa_documento_list', args=[empresa.pk, revision.pk])}"
        f"?tab=huerfanos" + (f"&q={q}" if q else "")
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_documento_activar_catalogo(request: HttpRequest, pk: int, rev_id: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    form = ActivarDocumentosCatalogoForm(request.POST or None, revision=revision)
    if request.method == "POST" and form.is_valid():
        creados = 0
        consultor_def = consultor_default_empresa(empresa)
        with transaction.atomic():
            marcar_prio = bool(form.cleaned_data.get("prioritario"))
            for doc in form.cleaned_data["documentos"]:
                defaults = {
                    "codigo": doc.codigo,
                    "nombre": doc.nombre,
                    "tipo_documento": doc.tipo_documento,
                    "origen": OrigenEmpresaDocumento.CATALOGO,
                    "activo": True,
                    "prioritario": marcar_prio,
                }
                if consultor_def is not None:
                    defaults["consultor_responsable"] = consultor_def
                _, created = EmpresaDocumento.objects.get_or_create(
                    revision=revision,
                    documento=doc,
                    defaults=defaults,
                )
                if created:
                    creados += 1
        messages.success(request, f"Se activaron {creados} documento(s) del catálogo.")
        return redirect("maestro:empresa_documento_list", pk=empresa.pk, rev_id=revision.pk)
    return render(
        request,
        "maestro/empresa_documento_activar.html",
        {"empresa": empresa, "revision": revision, "form": form},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_documento_crear_propio(request: HttpRequest, pk: int, rev_id: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    form = EmpresaDocumentoPropioForm(request.POST or None, revision=revision)
    if request.method == "POST" and form.is_valid():
        doc = form.save(commit=False)
        if doc.consultor_responsable_id is None:
            doc.consultor_responsable = consultor_default_empresa(empresa)
        doc.save()
        messages.success(request, "Documento propio creado correctamente.")
        return redirect("maestro:empresa_documento_list", pk=empresa.pk, rev_id=revision.pk)
    return render(
        request,
        "maestro/empresa_documento_propio_form.html",
        {
            "empresa": empresa,
            "revision": revision,
            "form": form,
            "titulo": "Nuevo documento propio",
        },
    )


@administrador_required
@require_http_methods(["POST"])
def empresa_documento_toggle_activo(
    request: HttpRequest, pk: int, rev_id: int, doc_id: int
) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    documento = get_object_or_404(EmpresaDocumento, pk=doc_id, revision=revision)
    documento.activo = not documento.activo
    documento.save(update_fields=["activo", "actualizado_en"])
    estado = "activado" if documento.activo else "desactivado"
    messages.success(request, f"Documento {documento.codigo} {estado}.")
    return redirect("maestro:empresa_documento_list", pk=empresa.pk, rev_id=revision.pk)


@administrador_required
@require_http_methods(["POST"])
def empresa_documento_toggle_prioritario(
    request: HttpRequest, pk: int, rev_id: int, doc_id: int
) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    documento = get_object_or_404(EmpresaDocumento, pk=doc_id, revision=revision)
    documento.prioritario = not documento.prioritario
    documento.save(update_fields=["prioritario", "actualizado_en"])
    estado = "marcado como prioritario" if documento.prioritario else "ya no es prioritario"
    messages.success(request, f"Documento {documento.codigo} {estado}.")
    return redirect("maestro:empresa_documento_list", pk=empresa.pk, rev_id=revision.pk)


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_documento_criterios(
    request: HttpRequest, pk: int, rev_id: int, doc_id: int
) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    documento = get_object_or_404(
        EmpresaDocumento.objects.select_related("documento"),
        pk=doc_id,
        revision=revision,
    )
    form = EmpresaDocumentoCriterioForm(request.POST or None, empresa_documento=documento)
    tema_id = (request.GET.get("tema") or "").strip()
    principio_id = (request.GET.get("principio") or "").strip()
    if request.method == "GET":
        qs = form.fields["criterios"].queryset
        if tema_id.isdigit():
            qs = qs.filter(tema_id=int(tema_id))
        if principio_id.isdigit():
            qs = qs.filter(principio_id=int(principio_id))
        form.fields["criterios"].queryset = qs

    if request.method == "POST" and form.is_valid():
        creados = form.save()
        messages.success(request, f"Se asociaron {len(creados)} criterio(s).")
        return redirect(
            "maestro:empresa_documento_criterios",
            pk=empresa.pk,
            rev_id=revision.pk,
            doc_id=documento.pk,
        )

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
    vinculos_como_secundario = (
        EmpresaDocumentoCriterio.objects.filter(
            empresa_documento_secundario=documento
        )
        .select_related(
            "criterio",
            "criterio__tema",
            "empresa_documento",
            "area",
        )
        .order_by("criterio__codigo")
    )
    return render(
        request,
        "maestro/empresa_documento_criterios.html",
        {
            "empresa": empresa,
            "revision": revision,
            "documento": documento,
            "form": form,
            "vinculos": vinculos,
            "vinculos_como_secundario": vinculos_como_secundario,
            "temas": Tema.objects.all(),
            "principios": Principio.objects.select_related("tema").all(),
            "filtro_tema": tema_id,
            "filtro_principio": principio_id,
        },
    )


@administrador_required
@require_http_methods(["POST"])
def empresa_documento_criterio_eliminar(
    request: HttpRequest, pk: int, rev_id: int, doc_id: int, vinculo_id: int
) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    documento = get_object_or_404(EmpresaDocumento, pk=doc_id, revision=revision)
    vinculo = get_object_or_404(
        EmpresaDocumentoCriterio,
        pk=vinculo_id,
        empresa_documento=documento,
    )
    vinculo.delete()
    messages.success(request, "Vínculo eliminado.")
    return redirect(
        "maestro:empresa_documento_criterios",
        pk=empresa.pk,
        rev_id=revision.pk,
        doc_id=documento.pk,
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_documento_aplicar_estandar(
    request: HttpRequest, pk: int, rev_id: int
) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    form = AplicarEstandarRevisionForm(request.POST or None, revision=revision)

    pares_estandar = list(
        DocumentoCriterio.objects.select_related(
            "documento", "documento_secundario", "criterio"
        )
    )
    doc_ids_estandar: set[int] = set()
    for rel in pares_estandar:
        doc_ids_estandar.add(rel.documento_id)
        if rel.documento_secundario_id:
            doc_ids_estandar.add(rel.documento_secundario_id)
    ya_docs = set(
        revision.documentos.filter(documento_id__isnull=False).values_list(
            "documento_id", flat=True
        )
    )
    docs_a_activar = len(doc_ids_estandar - ya_docs)

    desactivados_ids = set(
        RevisionCriterioDesactivado.objects.filter(revision=revision).values_list(
            "criterio_id", flat=True
        )
    )
    criterios_ya_asignados = set(
        EmpresaDocumentoCriterio.objects.filter(revision=revision).values_list(
            "criterio_id", flat=True
        )
    )
    vinculos_a_crear = sum(
        1
        for rel in pares_estandar
        if rel.criterio_id not in desactivados_ids
        and rel.criterio_id not in criterios_ya_asignados
    )

    if request.method == "POST" and form.is_valid():
        area = form.cleaned_data["area"]
        with transaction.atomic():
            docs_creados, vinculos_creados = aplicar_estandar_a_revision(
                revision,
                area,
                consultor_responsable=consultor_default_empresa(empresa),
            )
        messages.success(
            request,
            f"Estándar aplicado: {docs_creados} documento(s) activados, "
            f"{vinculos_creados} vínculo(s) creados.",
        )
        return redirect("maestro:empresa_documento_list", pk=empresa.pk, rev_id=revision.pk)

    return render(
        request,
        "maestro/empresa_documento_aplicar_estandar.html",
        {
            "empresa": empresa,
            "revision": revision,
            "form": form,
            "docs_a_activar": docs_a_activar,
            "vinculos_a_crear": vinculos_a_crear,
            "total_pares_estandar": len(pares_estandar),
            "tiene_areas": form.fields["area"].queryset.exists(),
        },
    )


