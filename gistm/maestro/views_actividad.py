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



def _documentos_para_actividad(empresa: Empresa | None) -> list:
    """Docs pendientes / por revisar de todas las revisiones activas."""
    if not empresa:
        return []
    seen: set[int] = set()
    ids: list[int] = []
    for revision in revisiones_activas_empresa(empresa.pk):
        progreso = calcular_progreso_revision(revision)
        for d in (
            list(progreso.documentos_por_revisar)
            + list(progreso.documentos_pendientes_entrega)
        ):
            if d.id in seen:
                continue
            seen.add(d.id)
            ids.append(d.id)
    return list(
        EmpresaDocumento.objects.filter(pk__in=ids, activo=True).order_by("codigo")
    )


@gestor_admin_o_cliente_required
@require_GET
def actividad_calendario(request: HttpRequest) -> HttpResponse:
    sincronizar_actividades_pasadas()
    anio, mes = parse_anio_mes(request.GET)
    empresas = empresas_visibles(request.user)
    qs = (
        actividades_visibles(request.user)
        .filter(Q(empresa__isnull=True) | Q(empresa__in=empresas))
        .select_related("empresa", "creado_por", "asignado_a")
        .prefetch_related("documentos")
        .exclude(estado=EstadoActividad.CANCELADA)
    )
    prev_y, prev_m = mes_anterior(anio, mes)
    next_y, next_m = mes_siguiente(anio, mes)
    # Cargar actividades del mes visible (+ bordes del grid)
    inicio_rango = timezone.make_aware(datetime(prev_y, prev_m, 15))
    fin_rango = timezone.make_aware(datetime(next_y, next_m, 15)) + timedelta(days=20)
    actividades_mes = list(qs.filter(inicio__gte=inicio_rango, inicio__lt=fin_rango))
    semanas = mes_calendario(anio, mes, actividades_mes)
    proximas = list(
        qs.filter(inicio__gte=timezone.now(), estado=EstadoActividad.PROGRAMADA).order_by(
            "inicio"
        )[:12]
    )
    atrasadas = list(
        qs.filter(estado=EstadoActividad.PASADA)
        .order_by(F("fin").asc(nulls_last=True), "inicio")[:12]
    )
    nombres_mes = [
        "",
        "Enero",
        "Febrero",
        "Marzo",
        "Abril",
        "Mayo",
        "Junio",
        "Julio",
        "Agosto",
        "Septiembre",
        "Octubre",
        "Noviembre",
        "Diciembre",
    ]
    return render(
        request,
        "maestro/actividad_calendario.html",
        {
            "anio": anio,
            "mes": mes,
            "mes_nombre": nombres_mes[mes],
            "semanas": semanas,
            "proximas": proximas,
            "atrasadas": atrasadas,
            "prev_anio": prev_y,
            "prev_mes": prev_m,
            "next_anio": next_y,
            "next_mes": next_m,
            "solo_lectura": usuario_es_cliente(request.user),
        },
    )


@gestor_o_admin_required
@require_http_methods(["GET", "POST"])
def actividad_crear(request: HttpRequest) -> HttpResponse:
    empresas = empresas_visibles(request.user).filter(activo=True)
    empresa_id = (request.POST.get("empresa") or request.GET.get("empresa") or "").strip()
    empresa = None
    if empresa_id.isdigit():
        empresa = empresas.filter(pk=int(empresa_id)).first()

    docs_qs = EmpresaDocumento.objects.filter(
        pk__in=[d.pk for d in _documentos_para_actividad(empresa)]
    ).order_by("codigo")
    areas_qs = (
        empresa.areas.filter(activo=True).order_by("nombre")
        if empresa
        else Area.objects.none()
    )
    area_id = (request.POST.get("area") or request.GET.get("area") or "").strip()
    area_sel = None
    if area_id.isdigit():
        area_sel = areas_qs.filter(pk=int(area_id)).first()
    responsables_qs = (
        area_sel.responsables.filter(activo=True).order_by("nombre")
        if area_sel
        else Responsable.objects.none()
    )

    form = ActividadForm(
        request.POST or None,
        user=request.user,
        empresas_qs=empresas,
        documentos_qs=docs_qs,
        areas_qs=areas_qs,
        responsables_qs=responsables_qs,
        asignables_qs=usuarios_asignables(request.user),
    )
    if request.method == "GET":
        if empresa:
            form.initial["empresa"] = empresa.pk
        if area_sel:
            form.initial["area"] = area_sel.pk
        alcance_q = (request.GET.get("alcance") or "").strip()
        if alcance_q in {AlcanceActividad.PERSONAL, AlcanceActividad.CON_CLIENTE}:
            form.initial["alcance"] = alcance_q
        # Prefill desde el calendario: ?fecha=YYYY-MM-DD o ?inicio=YYYY-MM-DDTHH:MM
        inicio_q = (request.GET.get("inicio") or request.GET.get("fecha") or "").strip()
        if inicio_q:
            from datetime import datetime as dt_parse
            from datetime import timedelta as td_parse

            parsed = None
            for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    parsed = dt_parse.strptime(inicio_q, fmt)
                    break
                except ValueError:
                    continue
            if parsed is not None:
                if parsed.hour == 0 and parsed.minute == 0 and "T" not in inicio_q and " " not in inicio_q:
                    parsed = parsed.replace(hour=9, minute=0)
                form.initial["inicio"] = parsed.strftime("%Y-%m-%dT%H:%M")
                form.initial["fin"] = (parsed + td_parse(hours=1)).strftime("%Y-%m-%dT%H:%M")

    if request.method == "POST" and form.is_valid():
        actividad = form.save(commit=False)
        actividad.creado_por = request.user
        if not actividad.asignado_a_id:
            actividad.asignado_a = request.user
        actividad.tipo = TipoActividad.GENERAL
        actividad.estado = EstadoActividad.PROGRAMADA
        # Correo automático solo si es con cliente (sin checkbox).
        actividad.enviar_correo = actividad.alcance == AlcanceActividad.CON_CLIENTE
        actividad.save()
        form.save_m2m()

        if actividad.enviar_correo:
            try:
                ok, msg = enviar_correo_actividad(
                    actividad, enviado_por=request.user
                )
                if ok:
                    messages.success(request, f"Actividad creada. {msg}")
                else:
                    messages.warning(request, f"Actividad creada. Correo no enviado: {msg}")
            except Exception as exc:  # noqa: BLE001
                messages.warning(
                    request,
                    f"Actividad creada, pero falló el correo: {exc}",
                )
        else:
            messages.success(request, "Actividad creada.")
        return redirect("maestro:actividad_detalle", pk=actividad.pk)

    docs_por_revisar = []
    docs_pendientes = []
    if empresa:
        ids_ok = set(docs_qs.values_list("pk", flat=True))
        por_revisar_ids: set[int] = set()
        pendientes_ids: set[int] = set()
        for revision in revisiones_activas_empresa(empresa.pk):
            prog = calcular_progreso_revision(revision)
            por_revisar_ids.update(x.id for x in prog.documentos_por_revisar)
            pendientes_ids.update(x.id for x in prog.documentos_pendientes_entrega)
        docs_por_revisar = [d for d in docs_qs if d.pk in por_revisar_ids and d.pk in ids_ok]
        docs_pendientes = [d for d in docs_qs if d.pk in pendientes_ids and d.pk in ids_ok]

    selected_doc_ids: set[int] = set()
    if form.is_bound:
        selected_doc_ids = {int(x) for x in request.POST.getlist("documentos") if str(x).isdigit()}

    return render(
        request,
        "maestro/actividad_form.html",
        {
            "form": form,
            "empresa_sel": empresa,
            "docs_por_revisar": docs_por_revisar,
            "docs_pendientes": docs_pendientes,
            "selected_doc_ids": selected_doc_ids,
            "modo": "crear",
        },
    )


@gestor_admin_o_cliente_required
@require_GET
def actividad_detalle(request: HttpRequest, pk: int) -> HttpResponse:
    sincronizar_actividades_pasadas()
    actividad = get_object_or_404(
        Actividad.objects.select_related(
            "empresa",
            "creado_por",
            "asignado_a",
            "actividad_padre",
            "area",
            "responsable",
        ).prefetch_related("documentos", "acuerdos__documentos"),
        pk=pk,
    )
    if not puede_ver_actividad(request.user, actividad):
        messages.error(request, "No tienes acceso a esta actividad.")
        return redirect("maestro:actividad_calendario")
    if actividad.empresa_id and not puede_ver_empresa(request.user, actividad.empresa):
        messages.error(request, "No tienes acceso a esta actividad.")
        return redirect("maestro:actividad_calendario")

    solo_lectura = usuario_es_cliente(request.user)
    docs_qs = actividad.documentos.all().order_by("codigo")
    now_local = timezone.localtime(timezone.now())
    default_dt = (now_local + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    acuerdo_form = AcuerdoForm(
        documentos_qs=docs_qs,
        initial={
            "fecha_compromiso": default_dt,
            "titulo": actividad.titulo,
        },
    )
    acuerdos = list(
        actividad.acuerdos.exclude(estado=EstadoAcuerdo.CANCELADO)
        .prefetch_related("documentos")
        .order_by("-creado_en")
    )
    responsables_qs = (
        responsables_con_correo(actividad.empresa_id)
        if actividad.empresa_id and not solo_lectura
        else Responsable.objects.none()
    )
    # Agrupar por área para el modal de recordatorio
    responsables_por_area: dict[str, list] = {}
    for r in responsables_qs:
        responsables_por_area.setdefault(r.area.nombre, []).append(r)
    return render(
        request,
        "maestro/actividad_detalle.html",
        {
            "actividad": actividad,
            "acuerdo_form": acuerdo_form,
            "acuerdos": acuerdos,
            "recordatorio_form": AcuerdoRecordatorioForm(responsables_qs=responsables_qs),
            "responsables_por_area": responsables_por_area,
            "puede_derivar": (
                not solo_lectura and actividad.estado != EstadoActividad.CANCELADA
            ),
            "solo_lectura": solo_lectura,
        },
    )


def _actividad_padre_ok(request: HttpRequest, pk: int) -> Actividad | None:
    actividad = get_object_or_404(Actividad, pk=pk)
    if not puede_ver_actividad(request.user, actividad):
        messages.error(request, "No tienes acceso a esta actividad.")
        return None
    if actividad.empresa_id and not puede_ver_empresa(request.user, actividad.empresa):
        messages.error(request, "No tienes acceso a esta actividad.")
        return None
    if actividad.estado == EstadoActividad.CANCELADA:
        messages.error(request, "No se puede derivar desde una actividad cancelada.")
        return None
    return actividad


@gestor_o_admin_required
@require_http_methods(["POST"])
def actividad_crear_acuerdo(request: HttpRequest, pk: int) -> HttpResponse:
    """Crea un Acuerdo (modelo propio), no una actividad del calendario."""
    padre = _actividad_padre_ok(request, pk)
    if padre is None:
        return redirect("maestro:actividad_calendario")

    form = AcuerdoForm(request.POST, documentos_qs=padre.documentos.all())
    if not form.is_valid():
        messages.error(request, "Indica un detalle o selecciona documentos.")
        return redirect("maestro:actividad_detalle", pk=padre.pk)

    docs = form.cleaned_data.get("documentos") or []
    empresa = padre.empresa
    acuerdo = Acuerdo.objects.create(
        actividad=padre,
        empresa=empresa,
        titulo=form.cleaned_data["titulo"],
        descripcion=form.cleaned_data.get("descripcion") or "",
        fecha_compromiso=form.cleaned_data.get("fecha_compromiso"),
        estado=EstadoAcuerdo.ABIERTO,
        creado_por=request.user,
    )
    if docs:
        acuerdo.documentos.set(docs)

    # Solo consultor: correo al consultor, sin mensajes en el hilo del cliente.
    # Con cliente: correo a clientes + mensajes en documentos.
    try:
        ok, mail_msg = enviar_correo_acuerdo(
            acuerdo, es_recordatorio=False, enviado_por=request.user
        )
        if padre.alcance != AlcanceActividad.PERSONAL:
            publicar_mensajes_acuerdo(acuerdo, request.user)
        if ok:
            messages.success(request, "Acuerdo guardado y notificado.")
        else:
            messages.warning(request, f"Acuerdo guardado. {mail_msg}")
    except Exception as exc:  # noqa: BLE001
        messages.warning(request, f"Acuerdo guardado, pero no se pudo notificar: {exc}")

    return redirect("maestro:actividad_detalle", pk=padre.pk)


def _acuerdo_accesible(request: HttpRequest, pk: int) -> Acuerdo | None:
    acuerdo = get_object_or_404(
        Acuerdo.objects.select_related("actividad", "empresa", "creado_por"),
        pk=pk,
    )
    if not acuerdo.empresa_id and acuerdo.actividad.empresa_id:
        acuerdo.empresa = acuerdo.actividad.empresa
        acuerdo.save(update_fields=["empresa", "actualizado_en"])
    empresa = acuerdo.empresa or acuerdo.actividad.empresa
    if empresa and not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a este acuerdo.")
        return None
    if not empresa and acuerdo.creado_por_id != request.user.pk:
        messages.error(request, "No tienes acceso a este acuerdo.")
        return None
    return acuerdo


def _redirect_acuerdo(request: HttpRequest, acuerdo: Acuerdo) -> HttpResponse:
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect("maestro:actividad_detalle", pk=acuerdo.actividad_id)


@gestor_admin_o_cliente_required
@require_GET
def acuerdo_bandeja(request: HttpRequest) -> HttpResponse:
    """Bandeja global de acuerdos de las empresas visibles."""
    sincronizar_acuerdos_vencidos()
    empresas = empresas_visibles(request.user)
    estado = (request.GET.get("estado") or "abiertos").strip().lower()
    filtro = (request.GET.get("filtro") or "todos").strip().lower()
    empresa_q = (request.GET.get("empresa") or "").strip()
    solo_lectura = usuario_es_cliente(request.user)

    base_q = Q(empresa__in=empresas) | Q(
        empresa__isnull=True, actividad__empresa__in=empresas
    )
    if solo_lectura:
        # Cliente: solo acuerdos de actividades compartidas con la empresa.
        base_q &= Q(actividad__alcance=AlcanceActividad.CON_CLIENTE)
    qs = (
        Acuerdo.objects.filter(base_q)
        .exclude(estado=EstadoAcuerdo.CANCELADO)
        .select_related("empresa", "actividad", "actividad__empresa", "creado_por")
        .prefetch_related("documentos")
        .order_by("fecha_compromiso", "-creado_en")
    )
    if estado == "abiertos":
        # Pendientes vigentes (aún no vencidos).
        qs = qs.filter(estado=EstadoAcuerdo.ABIERTO)
    elif estado == "vencidos":
        qs = qs.filter(estado=EstadoAcuerdo.VENCIDO)
    elif estado == "cumplidos":
        qs = qs.filter(estado=EstadoAcuerdo.CUMPLIDO)
    elif estado != "todos":
        estado = "abiertos"
        qs = qs.filter(estado=EstadoAcuerdo.ABIERTO)

    if empresa_q.isdigit():
        qs = qs.filter(
            Q(empresa_id=int(empresa_q)) | Q(actividad__empresa_id=int(empresa_q))
        )

    now = timezone.now()
    hoy = timezone.localdate()
    if filtro == "vencidos":
        qs = qs.filter(estado=EstadoAcuerdo.VENCIDO)
    elif filtro == "hoy":
        qs = qs.filter(
            estado=EstadoAcuerdo.ABIERTO,
            fecha_compromiso__date=hoy,
        )
    elif filtro == "proximos":
        qs = qs.filter(
            estado=EstadoAcuerdo.ABIERTO,
            fecha_compromiso__gte=now,
            fecha_compromiso__date__lte=hoy + timedelta(days=7),
        )
    elif filtro != "todos":
        filtro = "todos"

    acuerdos = list(qs[:300])
    contadores = {
        "abiertos": Acuerdo.objects.filter(
            base_q, estado=EstadoAcuerdo.ABIERTO
        ).count(),
        "vencidos": Acuerdo.objects.filter(
            base_q, estado=EstadoAcuerdo.VENCIDO
        ).count(),
    }
    # Responsables por empresa para el modal de recordatorio (JSON en plantilla).
    empresa_ids = {
        (a.empresa_id or a.actividad.empresa_id)
        for a in acuerdos
        if (a.empresa_id or a.actividad.empresa_id)
    }
    responsables_por_empresa: dict[int, list[dict]] = {eid: [] for eid in empresa_ids}
    if empresa_ids:
        for r in (
            Responsable.objects.filter(
                area__empresa_id__in=empresa_ids,
                area__activo=True,
                activo=True,
            )
            .exclude(correo="")
            .select_related("area")
            .order_by("area__nombre", "nombre")
        ):
            responsables_por_empresa.setdefault(r.area.empresa_id, []).append(
                {
                    "id": r.pk,
                    "nombre": r.nombre,
                    "correo": r.correo,
                    "area": r.area.nombre,
                }
            )
    return render(
        request,
        "maestro/acuerdo_bandeja.html",
        {
            "acuerdos": acuerdos,
            "estado": estado,
            "filtro": filtro,
            "empresa_q": empresa_q,
            "empresas": empresas.filter(activo=True),
            "contadores": contadores,
            "now": now,
            "responsables_por_empresa": responsables_por_empresa,
            "solo_lectura": solo_lectura,
        },
    )


@gestor_o_admin_required
@require_GET
def aviso_list(request: HttpRequest) -> HttpResponse:
    solo = (request.GET.get("solo") or "nuevos").strip().lower()
    qs = (
        AvisoConsultor.objects.filter(destinatario=request.user)
        .select_related("empresa", "empresa_documento")
        .order_by("-creado_en")
    )
    if solo == "nuevos":
        qs = qs.filter(leido=False)
    elif solo != "todos":
        solo = "nuevos"
        qs = qs.filter(leido=False)
    avisos = list(qs[:200])
    return render(
        request,
        "maestro/aviso_list.html",
        {"avisos": avisos, "solo": solo},
    )


@gestor_o_admin_required
@require_http_methods(["POST"])
def aviso_marcar_leido(request: HttpRequest, pk: int) -> HttpResponse:
    aviso = get_object_or_404(AvisoConsultor, pk=pk, destinatario=request.user)
    if not aviso.leido:
        aviso.leido = True
        aviso.leido_en = timezone.now()
        aviso.save(update_fields=["leido", "leido_en"])
    next_url = (request.POST.get("next") or "").strip()
    if aviso.url and aviso.url.startswith("/") and not aviso.url.startswith("//"):
        return redirect(aviso.url)
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect("maestro:aviso_list")


@gestor_o_admin_required
@require_http_methods(["POST"])
def aviso_marcar_todos(request: HttpRequest) -> HttpResponse:
    AvisoConsultor.objects.filter(destinatario=request.user, leido=False).update(
        leido=True,
        leido_en=timezone.now(),
    )
    messages.success(request, "Todos los avisos se marcaron como leídos.")
    return redirect("maestro:aviso_list")


@gestor_o_admin_required
@require_http_methods(["POST"])
def acuerdo_enviar_recordatorio(request: HttpRequest, pk: int) -> HttpResponse:
    from .actividad_mail import acuerdo_es_solo_consultor

    acuerdo = _acuerdo_accesible(request, pk)
    if acuerdo is None:
        return redirect("maestro:acuerdo_bandeja")

    if acuerdo.estado not in (EstadoAcuerdo.ABIERTO, EstadoAcuerdo.VENCIDO):
        messages.error(request, "Solo se puede recordar acuerdos abiertos o vencidos.")
        return _redirect_acuerdo(request, acuerdo)

    solo_consultor = acuerdo_es_solo_consultor(acuerdo)
    if not solo_consultor and not acuerdo.empresa_id:
        messages.error(request, "Este acuerdo no tiene empresa para notificar.")
        return _redirect_acuerdo(request, acuerdo)

    nota = (request.POST.get("mensaje") or "").strip()
    emails = None
    if not solo_consultor:
        responsables_qs = responsables_con_correo(acuerdo.empresa_id)
        form = AcuerdoRecordatorioForm(request.POST, responsables_qs=responsables_qs)
        if not form.is_valid():
            messages.error(
                request,
                "Selecciona al menos un responsable con correo para enviar el recordatorio.",
            )
            return _redirect_acuerdo(request, acuerdo)
        nota = (form.cleaned_data.get("mensaje") or "").strip()
        emails = emails_desde_responsables(form.cleaned_data["responsables"])

    try:
        ok, mail_msg = enviar_correo_acuerdo(
            acuerdo,
            es_recordatorio=True,
            mensaje_extra=nota,
            destinatarios=emails,
            enviado_por=request.user,
        )
        if not solo_consultor:
            publicar_mensajes_acuerdo(acuerdo, request.user, solo_pendientes=True)
        if ok:
            messages.success(request, mail_msg)
        else:
            messages.info(request, mail_msg)
    except Exception as exc:  # noqa: BLE001
        messages.warning(request, f"No se pudo enviar el recordatorio: {exc}")

    return _redirect_acuerdo(request, acuerdo)


@gestor_o_admin_required
@require_http_methods(["POST"])
def acuerdo_marcar_cumplido(request: HttpRequest, pk: int) -> HttpResponse:
    acuerdo = _acuerdo_accesible(request, pk)
    if acuerdo is None:
        return redirect("maestro:acuerdo_bandeja")
    if acuerdo.estado not in (EstadoAcuerdo.ABIERTO, EstadoAcuerdo.VENCIDO):
        messages.info(request, "Este acuerdo ya está cerrado.")
        return _redirect_acuerdo(request, acuerdo)
    acuerdo.estado = EstadoAcuerdo.CUMPLIDO
    acuerdo.save(update_fields=["estado", "actualizado_en"])
    messages.success(request, "Acuerdo marcado como cumplido.")
    return _redirect_acuerdo(request, acuerdo)


@gestor_o_admin_required
@require_http_methods(["POST"])
def actividad_completar(request: HttpRequest, pk: int) -> HttpResponse:
    actividad = get_object_or_404(Actividad, pk=pk)
    if not puede_ver_actividad(request.user, actividad):
        messages.error(request, "No tienes acceso a esta actividad.")
        return redirect("maestro:actividad_calendario")
    if actividad.estado not in (
        EstadoActividad.PROGRAMADA,
        EstadoActividad.PASADA,
    ):
        messages.info(request, "Esta actividad ya está cerrada.")
        return redirect("maestro:actividad_detalle", pk=actividad.pk)
    actividad.estado = EstadoActividad.COMPLETADA
    actividad.save(update_fields=["estado", "actualizado_en"])
    messages.success(request, "Actividad marcada como completada (se realizó).")
    return redirect("maestro:actividad_detalle", pk=actividad.pk)


@gestor_o_admin_required
@require_http_methods(["POST"])
def actividad_cancelar(request: HttpRequest, pk: int) -> HttpResponse:
    """Cierra la actividad como no realizada (anulación o vencida sin ejecutarse)."""
    actividad = get_object_or_404(Actividad, pk=pk)
    if not puede_ver_actividad(request.user, actividad):
        messages.error(request, "No tienes acceso a esta actividad.")
        return redirect("maestro:actividad_calendario")
    if actividad.estado not in (
        EstadoActividad.PROGRAMADA,
        EstadoActividad.PASADA,
    ):
        messages.info(request, "Esta actividad ya está cerrada.")
        return redirect("maestro:actividad_detalle", pk=actividad.pk)
    actividad.estado = EstadoActividad.CANCELADA
    actividad.save(update_fields=["estado", "actualizado_en"])
    messages.success(
        request,
        "Actividad marcada como «No se llevó a cabo».",
    )
    return redirect("maestro:actividad_detalle", pk=actividad.pk)


@gestor_o_admin_required
@require_GET
def recordatorio_list(request: HttpRequest) -> HttpResponse:
    """Historial de correos enviados (To cliente / CC). Por defecto: solo hoy."""
    empresas = empresas_visibles(request.user)
    empresa_ids = list(empresas.values_list("pk", flat=True))
    qs = CorreoEnvio.objects.select_related(
        "empresa", "acuerdo", "actividad", "enviado_por"
    ).order_by("-enviado_en")
    if not usuario_es_administrador(request.user):
        qs = qs.filter(empresa_id__in=empresa_ids)

    hoy = timezone.localdate()
    fecha_raw = (request.GET.get("fecha") or "").strip()
    if fecha_raw:
        try:
            fecha = datetime.strptime(fecha_raw, "%Y-%m-%d").date()
        except ValueError:
            fecha = hoy
    else:
        fecha = hoy
    inicio = timezone.make_aware(datetime.combine(fecha, datetime.min.time()))
    fin = inicio + timedelta(days=1)
    qs = qs.filter(enviado_en__gte=inicio, enviado_en__lt=fin)

    tipo = (request.GET.get("tipo") or "").strip()
    if tipo in {c.value for c in TipoCorreoEnvio}:
        qs = qs.filter(tipo=tipo)
    empresa_q = (request.GET.get("empresa") or "").strip()
    if empresa_q.isdigit() and int(empresa_q) in empresa_ids:
        qs = qs.filter(empresa_id=int(empresa_q))
    envios = list(qs[:400])
    return render(
        request,
        "maestro/recordatorio_list.html",
        {
            "envios": envios,
            "empresas": empresas.filter(activo=True),
            "tipo": tipo,
            "empresa_q": empresa_q,
            "fecha": fecha,
            "es_hoy": fecha == hoy,
            "tipos": TipoCorreoEnvio.choices,
            "total": len(envios),
        },
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def plantilla_correo_list(request: HttpRequest) -> HttpResponse:
    from .email_templates import asegurar_plantilla_acuerdo

    asegurar_plantilla_acuerdo()
    plantillas = PlantillaCorreo.objects.order_by("nombre")
    return render(
        request,
        "maestro/plantilla_correo_list.html",
        {"plantillas": plantillas},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def plantilla_correo_edit(request: HttpRequest, pk: int) -> HttpResponse:
    from django.contrib.staticfiles.storage import staticfiles_storage

    from .email_templates import (
        asegurar_plantilla_acuerdo,
        contexto_correo_acuerdo,
        render_plantilla_html,
        validar_html_plantilla_correo,
    )

    asegurar_plantilla_acuerdo()
    plantilla = get_object_or_404(PlantillaCorreo, pk=pk)
    tab = (request.POST.get("tab") or request.GET.get("tab") or "html").strip().lower()
    if tab not in {"html", "preview"}:
        tab = "html"

    # HTML mostrado en el editor (puede diferir de BD si el POST falló validación).
    html_editor = plantilla.html

    if request.method == "POST":
        nombre = (request.POST.get("nombre") or "").strip() or plantilla.nombre
        html = request.POST.get("html") or ""
        descripcion = request.POST.get("descripcion") or ""
        activo = request.POST.get("activo") == "on"
        html_editor = html
        plantilla.nombre = nombre[:120]
        plantilla.descripcion = descripcion
        plantilla.activo = activo
        if not html.strip():
            messages.error(request, "El HTML de la plantilla no puede estar vacío.")
            tab = "html"
        else:
            error_html = validar_html_plantilla_correo(html)
            if error_html:
                messages.error(request, error_html)
                tab = "html"
            else:
                plantilla.html = html
                plantilla.actualizado_por = request.user
                plantilla.save()
                messages.success(request, "Plantilla de correo guardada.")
                return redirect(
                    f"{reverse('maestro:plantilla_correo_edit', args=[plantilla.pk])}?tab={tab}"
                )

    # Vista previa solo con HTML persistido en BD (nunca request.POST → evita SSTI).
    acuerdo = (
        Acuerdo.objects.select_related("empresa")
        .filter(empresa__isnull=False)
        .order_by("-creado_en")
        .first()
    )
    preview_html = ""
    logo_url = request.build_absolute_uri(
        staticfiles_storage.url("maestro/img/logotipo.png")
    )
    if acuerdo:
        ctx = contexto_correo_acuerdo(
            acuerdo,
            es_recordatorio=True,
            tipo_regla=TipoNotificacionAcuerdo.ANTES,
            dias_regla=1,
            logo_src=logo_url,
            es_preview=True,
            cta_url=request.build_absolute_uri("/"),
        )
        ctx["asunto"] = f"[Vista previa] {acuerdo.titulo}"
        try:
            preview_html = render_plantilla_html(plantilla.html, ctx)
        except Exception as exc:  # noqa: BLE001
            preview_html = f"<p style='color:#b42318'>Error en la plantilla: {exc}</p>"
            tab = "html"
    else:
        preview_html = (
            "<p class='muted'>Crea un acuerdo de prueba para ver la vista previa con datos reales.</p>"
        )

    return render(
        request,
        "maestro/plantilla_correo_form.html",
        {
            "plantilla": plantilla,
            "html_editor": html_editor,
            "preview_html": preview_html,
            "tab": tab,
        },
    )


