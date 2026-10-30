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



@administrador_required
@require_GET
def tema_list(request: HttpRequest) -> HttpResponse:
    temas = Tema.objects.all()
    return render(request, "maestro/tema_list.html", {"temas": temas})


@administrador_required
@require_http_methods(["GET", "POST"])
def tema_create(request: HttpRequest) -> HttpResponse:
    form = TemaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Tema creado correctamente.")
        return redirect("maestro:tema_list")
    return render(request, "maestro/tema_form.html", {"form": form, "titulo": "Nuevo tema"})


@administrador_required
@require_http_methods(["GET", "POST"])
def tema_edit(request: HttpRequest, pk: int) -> HttpResponse:
    tema = get_object_or_404(Tema, pk=pk)
    form = TemaForm(request.POST or None, instance=tema)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Tema actualizado correctamente.")
        return redirect("maestro:tema_list")
    return render(
        request,
        "maestro/tema_form.html",
        {"form": form, "titulo": "Editar tema", "tema": tema},
    )


@administrador_required
@require_http_methods(["POST"])
def tema_toggle_activo(request: HttpRequest, pk: int) -> HttpResponse:
    tema = get_object_or_404(Tema, pk=pk)
    tema.activo = not tema.activo
    tema.save(update_fields=["activo", "actualizado_en"])
    estado = "habilitado" if tema.activo else "deshabilitado"
    messages.success(
        request,
        f"Tema {tema.codigo} {estado}. Solo afecta revisiones nuevas.",
    )
    return redirect("maestro:tema_list")


@administrador_required
@require_GET
def glosario_termino_list(request: HttpRequest) -> HttpResponse:
    terminos = GlosarioTermino.objects.all()
    q = (request.GET.get("q") or "").strip()
    if q:
        terminos = terminos.filter(
            Q(concepto__icontains=q) | Q(descripcion__icontains=q)
        )
    return render(
        request,
        "maestro/glosario_termino_list.html",
        {"terminos": terminos, "q": q},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def glosario_termino_create(request: HttpRequest) -> HttpResponse:
    form = GlosarioTerminoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Término de glosario creado correctamente.")
        return redirect("maestro:glosario_termino_list")
    return render(
        request,
        "maestro/glosario_termino_form.html",
        {"form": form, "titulo": "Nuevo término"},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def glosario_termino_edit(request: HttpRequest, pk: int) -> HttpResponse:
    termino = get_object_or_404(GlosarioTermino, pk=pk)
    form = GlosarioTerminoForm(request.POST or None, instance=termino)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Término de glosario actualizado correctamente.")
        return redirect("maestro:glosario_termino_list")
    return render(
        request,
        "maestro/glosario_termino_form.html",
        {"form": form, "titulo": "Editar término", "termino": termino},
    )


@administrador_required
@require_GET
def principio_list(request: HttpRequest) -> HttpResponse:
    principios = Principio.objects.select_related("tema").all()
    return render(request, "maestro/principio_list.html", {"principios": principios})


@administrador_required
@require_http_methods(["GET", "POST"])
def principio_create(request: HttpRequest) -> HttpResponse:
    form = PrincipioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Principio creado correctamente.")
        return redirect("maestro:principio_list")
    return render(
        request,
        "maestro/principio_form.html",
        {"form": form, "titulo": "Nuevo principio"},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def principio_edit(request: HttpRequest, pk: int) -> HttpResponse:
    principio = get_object_or_404(Principio, pk=pk)
    form = PrincipioForm(request.POST or None, instance=principio)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Principio actualizado correctamente.")
        return redirect("maestro:principio_list")
    return render(
        request,
        "maestro/principio_form.html",
        {"form": form, "titulo": "Editar principio", "principio": principio},
    )


@administrador_required
@require_http_methods(["POST"])
def principio_toggle_activo(request: HttpRequest, pk: int) -> HttpResponse:
    principio = get_object_or_404(Principio, pk=pk)
    principio.activo = not principio.activo
    principio.save(update_fields=["activo", "actualizado_en"])
    estado = "habilitado" if principio.activo else "deshabilitado"
    messages.success(
        request,
        f"Principio {principio.codigo} {estado}. Solo afecta revisiones nuevas.",
    )
    return redirect("maestro:principio_list")


@administrador_required
@require_GET
def requisito_list(request: HttpRequest) -> HttpResponse:
    requisitos = Requisito.objects.select_related("principio", "principio__tema").all()
    principio_id = (request.GET.get("principio") or "").strip()
    if principio_id.isdigit():
        requisitos = requisitos.filter(principio_id=int(principio_id))
    return render(
        request,
        "maestro/requisito_list.html",
        {
            "requisitos": requisitos,
            "principios": Principio.objects.select_related("tema").all(),
            "filtro_principio": principio_id,
        },
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def requisito_create(request: HttpRequest) -> HttpResponse:
    form = RequisitoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Requisito creado correctamente.")
        return redirect("maestro:requisito_list")
    return render(
        request,
        "maestro/requisito_form.html",
        {"form": form, "titulo": "Nuevo requisito"},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def requisito_edit(request: HttpRequest, pk: int) -> HttpResponse:
    requisito = get_object_or_404(
        Requisito.objects.select_related("principio", "principio__tema"),
        pk=pk,
    )
    form = RequisitoForm(request.POST or None, instance=requisito)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Requisito actualizado correctamente.")
        return redirect("maestro:requisito_list")
    return render(
        request,
        "maestro/requisito_form.html",
        {"form": form, "titulo": "Editar requisito", "requisito": requisito},
    )


@administrador_required
@require_http_methods(["POST"])
def requisito_toggle_activo(request: HttpRequest, pk: int) -> HttpResponse:
    requisito = get_object_or_404(Requisito, pk=pk)
    requisito.activo = not requisito.activo
    requisito.save(update_fields=["activo", "actualizado_en"])
    estado = "habilitado" if requisito.activo else "deshabilitado"
    messages.success(
        request,
        f"Requisito {requisito.codigo} {estado}. Solo afecta revisiones nuevas.",
    )
    return redirect("maestro:requisito_list")


@administrador_required
@require_GET
def criterio_list(request: HttpRequest) -> HttpResponse:
    criterios = Criterio.objects.select_related(
        "requisito", "principio", "tema"
    ).all()
    tema_id = (request.GET.get("tema") or "").strip()
    principio_id = (request.GET.get("principio") or "").strip()
    requisito_id = (request.GET.get("requisito") or "").strip()
    if tema_id.isdigit():
        criterios = criterios.filter(tema_id=int(tema_id))
    if principio_id.isdigit():
        criterios = criterios.filter(principio_id=int(principio_id))
    if requisito_id.isdigit():
        criterios = criterios.filter(requisito_id=int(requisito_id))
    return render(
        request,
        "maestro/criterio_list.html",
        {
            "criterios": criterios,
            "temas": Tema.objects.all(),
            "principios": Principio.objects.select_related("tema").all(),
            "requisitos": Requisito.objects.select_related("principio").all(),
            "filtro_tema": tema_id,
            "filtro_principio": principio_id,
            "filtro_requisito": requisito_id,
        },
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def criterio_create(request: HttpRequest) -> HttpResponse:
    form = CriterioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Criterio creado correctamente.")
        return redirect("maestro:criterio_list")
    return render(
        request,
        "maestro/criterio_form.html",
        {"form": form, "titulo": "Nuevo criterio"},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def criterio_edit(request: HttpRequest, pk: int) -> HttpResponse:
    criterio = get_object_or_404(
        Criterio.objects.select_related("requisito", "principio", "tema"),
        pk=pk,
    )
    form = CriterioForm(request.POST or None, instance=criterio)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Criterio actualizado correctamente.")
        return redirect("maestro:criterio_list")
    return render(
        request,
        "maestro/criterio_form.html",
        {"form": form, "titulo": "Editar criterio", "criterio": criterio},
    )


@administrador_required
@require_http_methods(["POST"])
def criterio_toggle_activo(request: HttpRequest, pk: int) -> HttpResponse:
    criterio = get_object_or_404(Criterio, pk=pk)
    criterio.activo = not criterio.activo
    criterio.save(update_fields=["activo", "actualizado_en"])
    estado = "habilitado" if criterio.activo else "deshabilitado"
    messages.success(
        request,
        f"Criterio {criterio.codigo} {estado}. Solo afecta revisiones nuevas.",
    )
    return redirect("maestro:criterio_list")


@administrador_required
@require_GET
def pregunta_list(request: HttpRequest) -> HttpResponse:
    preguntas = Pregunta.objects.select_related(
        "criterio", "criterio__requisito", "criterio__principio", "criterio__tema"
    ).all()
    criterio_id = (request.GET.get("criterio") or "").strip()
    if criterio_id.isdigit():
        preguntas = preguntas.filter(criterio_id=int(criterio_id))
    return render(
        request,
        "maestro/pregunta_list.html",
        {
            "preguntas": preguntas,
            "criterios": Criterio.objects.order_by("codigo"),
            "filtro_criterio": criterio_id,
        },
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def pregunta_create(request: HttpRequest) -> HttpResponse:
    form = PreguntaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Pregunta creada correctamente.")
        return redirect("maestro:pregunta_list")
    return render(
        request,
        "maestro/pregunta_form.html",
        {"form": form, "titulo": "Nueva pregunta"},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def pregunta_edit(request: HttpRequest, pk: int) -> HttpResponse:
    pregunta = get_object_or_404(
        Pregunta.objects.select_related("criterio"),
        pk=pk,
    )
    form = PreguntaForm(request.POST or None, instance=pregunta)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Pregunta actualizada correctamente.")
        return redirect("maestro:pregunta_list")
    return render(
        request,
        "maestro/pregunta_form.html",
        {"form": form, "titulo": "Editar pregunta", "pregunta": pregunta},
    )


@administrador_required
@require_http_methods(["POST"])
def pregunta_toggle_activo(request: HttpRequest, pk: int) -> HttpResponse:
    pregunta = get_object_or_404(Pregunta, pk=pk)
    pregunta.activo = not pregunta.activo
    pregunta.save(update_fields=["activo", "actualizado_en"])
    estado = "habilitada" if pregunta.activo else "deshabilitada"
    messages.success(
        request,
        f"Pregunta {pregunta.codigo} {estado}. Solo afecta revisiones nuevas.",
    )
    return redirect("maestro:pregunta_list")


@administrador_required
@require_GET
def documento_list(request: HttpRequest) -> HttpResponse:
    documentos = Documento.objects.all()
    tipo = (request.GET.get("tipo") or "").strip()
    if tipo:
        documentos = documentos.filter(tipo_documento=tipo)
    tipos = (
        Documento.objects.order_by("tipo_documento")
        .values_list("tipo_documento", flat=True)
        .distinct()
    )
    return render(
        request,
        "maestro/documento_list.html",
        {
            "documentos": documentos,
            "tipos": tipos,
            "filtro_tipo": tipo,
        },
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def documento_create(request: HttpRequest) -> HttpResponse:
    form = DocumentoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Documento creado correctamente.")
        return redirect("maestro:documento_list")
    return render(
        request,
        "maestro/documento_form.html",
        {"form": form, "titulo": "Nuevo documento"},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def documento_edit(request: HttpRequest, pk: int) -> HttpResponse:
    documento = get_object_or_404(Documento, pk=pk)
    form = DocumentoForm(request.POST or None, instance=documento)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Documento actualizado correctamente.")
        return redirect("maestro:documento_list")
    return render(
        request,
        "maestro/documento_form.html",
        {"form": form, "titulo": "Editar documento", "documento": documento},
    )


@administrador_required
@require_GET
def documento_criterio_list(request: HttpRequest) -> HttpResponse:
    relaciones = DocumentoCriterio.objects.select_related(
        "documento",
        "documento_secundario",
        "criterio",
        "criterio__tema",
        "criterio__principio",
    ).all()
    documento_id = (request.GET.get("documento") or "").strip()
    tema_id = (request.GET.get("tema") or "").strip()
    principio_id = (request.GET.get("principio") or "").strip()
    if documento_id.isdigit():
        doc_pk = int(documento_id)
        relaciones = relaciones.filter(
            Q(documento_id=doc_pk) | Q(documento_secundario_id=doc_pk)
        )
    if tema_id.isdigit():
        relaciones = relaciones.filter(criterio__tema_id=int(tema_id))
    if principio_id.isdigit():
        relaciones = relaciones.filter(criterio__principio_id=int(principio_id))
    return render(
        request,
        "maestro/documento_criterio_list.html",
        {
            "relaciones": relaciones,
            "documentos": Documento.objects.order_by("codigo"),
            "temas": Tema.objects.all(),
            "principios": Principio.objects.select_related("tema").all(),
            "filtro_documento": documento_id,
            "filtro_tema": tema_id,
            "filtro_principio": principio_id,
            "total": relaciones.count(),
            "total_relaciones": DocumentoCriterio.objects.count(),
        },
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def documento_criterio_create(request: HttpRequest) -> HttpResponse:
    form = DocumentoCriterioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Relación estándar creada correctamente.")
        return redirect("maestro:documento_criterio_list")
    return render(
        request,
        "maestro/documento_criterio_form.html",
        {"form": form, "titulo": "Nueva relación estándar"},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def documento_criterio_edit(request: HttpRequest, pk: int) -> HttpResponse:
    relacion = get_object_or_404(
        DocumentoCriterio.objects.select_related("documento", "criterio"),
        pk=pk,
    )
    form = DocumentoCriterioForm(request.POST or None, instance=relacion)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Relación estándar actualizada correctamente.")
        return redirect("maestro:documento_criterio_list")
    return render(
        request,
        "maestro/documento_criterio_form.html",
        {"form": form, "titulo": "Editar relación estándar", "relacion": relacion},
    )


@administrador_required
@require_http_methods(["POST"])
def documento_criterio_delete(request: HttpRequest, pk: int) -> HttpResponse:
    relacion = get_object_or_404(DocumentoCriterio, pk=pk)
    relacion.delete()
    messages.success(request, "Relación estándar eliminada.")
    return redirect("maestro:documento_criterio_list")


@administrador_required
@require_GET
def documento_criterio_plantilla(request: HttpRequest) -> HttpResponse:
    from datetime import date
    from io import BytesIO

    buffer = BytesIO(plantilla_bytes())
    buffer.seek(0)
    filename = f"relacion_estandar_{date.today().isoformat()}.xlsx"
    return FileResponse(
        buffer,
        as_attachment=True,
        filename=filename,
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )


@administrador_required
@require_http_methods(["POST"])
def documento_criterio_importar(request: HttpRequest) -> HttpResponse:
    archivo = request.FILES.get("archivo")
    if archivo is None:
        messages.error(
            request,
            "Selecciona un archivo Excel (.xlsx) para importar. "
            "Puedes descargar la plantilla y completarla.",
        )
        return redirect("maestro:documento_criterio_list")

    nombre = (archivo.name or "").lower()
    if not nombre.endswith(".xlsx"):
        messages.error(request, "El archivo debe ser .xlsx.")
        return redirect("maestro:documento_criterio_list")

    try:
        stats = importar_documento_criterio_excel(archivo)
        messages.success(
            request,
            "Importación desde Excel completada. " + format_import_message(stats),
        )
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"No se pudo importar: {exc}")
    return redirect("maestro:documento_criterio_list")


@administrador_required
@require_http_methods(["POST"])
def documento_criterio_borrar_todas(request: HttpRequest) -> HttpResponse:
    """
    Borra la plantilla global Documento↔Criterio.
    Antes del delete genera y descarga un Excel de respaldo; deja traza en auditoría.
    No modifica revisiones de empresa que ya aplicaron el estándar.
    """
    from datetime import date
    from io import BytesIO

    total = DocumentoCriterio.objects.count()
    if total == 0:
        messages.info(request, "No había relaciones estándar para eliminar.")
        return redirect("maestro:documento_criterio_list")

    # Respaldo ANTES del borrado (punto 2.3).
    backup = plantilla_bytes()
    eliminadas, _ = DocumentoCriterio.objects.all().delete()
    from .auditoria import auditar_archivo

    auditar_archivo(
        request,
        detalle=(
            f"Borrado masivo de relación estándar: {eliminadas} fila(s). "
            "Se generó Excel de respaldo automático antes del delete."
        ),
        objeto_tipo="documentocriterio",
    )
    messages.success(
        request,
        f"Se eliminaron {eliminadas} relación(es) del estándar. "
        "Se descargó el Excel de respaldo automáticamente.",
    )
    buffer = BytesIO(backup)
    buffer.seek(0)
    filename = f"respaldo_relacion_estandar_{date.today().isoformat()}.xlsx"
    return FileResponse(
        buffer,
        as_attachment=True,
        filename=filename,
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )


