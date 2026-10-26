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
    to_json,
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


def _rev_id_param(request: HttpRequest) -> int | None:
    raw = (request.GET.get("rev") or request.POST.get("rev") or "").strip()
    return int(raw) if raw.isdigit() else None


def _revision_seguimiento(
    empresa: Empresa, request: HttpRequest | None = None, rev_id: int | None = None
) -> Revision | None:
    if rev_id is None and request is not None:
        rev_id = _rev_id_param(request)
    return revision_activa_por_id(empresa.pk, rev_id)


def _seguimiento_url(name: str, empresa_pk: int, revision: Revision | None = None) -> str:
    url = reverse(name, args=[empresa_pk])
    if revision is not None:
        return f"{url}?rev={revision.pk}"
    return url


def _client_ip(request: HttpRequest) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _registrar_intento(
    request: HttpRequest,
    *,
    email: str,
    oid: str,
    motivo: str,
    detalle: str = "",
) -> None:
    IntentoAcceso.objects.create(
        email=email or "",
        oid=oid or "",
        motivo=motivo,
        detalle=detalle,
        ip=_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:1000],
    )


def _split_name(name: str) -> tuple[str, str]:
    name = (name or "").strip()
    if not name:
        return "", ""
    if " " not in name:
        return name, ""
    first, last = name.split(" ", 1)
    return first, last


@require_GET
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("maestro:home")
    return render(request, "maestro/login.html")


@require_GET
def entra_login(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("maestro:home")

    state = new_oauth_state()
    request.session["entra_oauth_state"] = state
    next_url = request.GET.get("next")
    if next_url:
        request.session["entra_oauth_next"] = next_url

    return redirect(build_auth_url(state))


@require_GET
def entra_callback(request: HttpRequest) -> HttpResponse:
    error = request.GET.get("error")
    if error:
        messages.error(
            request,
            request.GET.get("error_description") or f"Error de autenticación: {error}",
        )
        return redirect("maestro:login")

    expected_state = request.session.pop("entra_oauth_state", None)
    received_state = request.GET.get("state")
    if not expected_state or expected_state != received_state:
        messages.error(request, "Estado OAuth inválido. Intenta iniciar sesión de nuevo.")
        return redirect("maestro:login")

    code = request.GET.get("code")
    if not code:
        messages.error(request, "No se recibió el código de autorización.")
        return redirect("maestro:login")

    try:
        token_result = exchange_code_for_token(code)
        claims = claims_from_id_token(token_result)
    except Exception as exc:  # noqa: BLE001 - surface Entra errors to the user
        messages.error(request, f"No se pudo completar el inicio de sesión: {exc}")
        return redirect("maestro:login")

    email = (claims.get("preferred_username") or claims.get("email") or "").strip().lower()
    oid = str(claims.get("oid") or claims.get("sub") or "")
    name = (claims.get("name") or "").strip()

    if not email:
        _registrar_intento(
            request,
            email="",
            oid=oid,
            motivo=MotivoIntentoAcceso.SIN_EMAIL,
            detalle="Entra ID no devolvió correo electrónico.",
        )
        messages.error(
            request,
            "No se pudo identificar tu correo en Entra ID. Contacta al administrador.",
        )
        return redirect("maestro:login")

    user = (
        User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email))
        .select_related("perfil")
        .first()
    )

    if user is None:
        _registrar_intento(
            request,
            email=email,
            oid=oid,
            motivo=MotivoIntentoAcceso.USUARIO_INEXISTENTE,
            detalle="Correo autenticado en Entra ID pero no pre-registrado en GISTM.",
        )
        messages.error(
            request,
            "Tu cuenta no está registrada en GISTM. Solicita el alta a un administrador.",
        )
        return redirect("maestro:login")

    perfil = get_perfil(user)
    if perfil is None:
        _registrar_intento(
            request,
            email=email,
            oid=oid,
            motivo=MotivoIntentoAcceso.SIN_PERFIL,
            detalle="Usuario Django sin PerfilUsuario.",
        )
        messages.error(
            request,
            "Tu cuenta no tiene un perfil asignado. Contacta al administrador.",
        )
        return redirect("maestro:login")

    if not user.is_active or not perfil.activo:
        _registrar_intento(
            request,
            email=email,
            oid=oid,
            motivo=MotivoIntentoAcceso.INACTIVO,
            detalle="Usuario o perfil inactivo.",
        )
        messages.error(request, "Tu cuenta está inactiva. Contacta al administrador.")
        return redirect("maestro:login")

    first_name, last_name = _split_name(name)
    updated_fields: list[str] = []
    if email and user.email != email:
        user.email = email
        updated_fields.append("email")
    if first_name and user.first_name != first_name:
        user.first_name = first_name
        updated_fields.append("first_name")
    if last_name and user.last_name != last_name:
        user.last_name = last_name
        updated_fields.append("last_name")
    if updated_fields:
        user.save(update_fields=updated_fields)

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["entra_oid"] = oid
    request.session["entra_name"] = name or user.get_full_name() or user.username
    request.session["perfil_tipo"] = perfil.tipo

    if perfil.tipo in TIPOS_CLIENTE:
        RegistroLoginExterno.objects.create(
            user=user,
            email=email or user.email or user.username,
            nombre=name or user.get_full_name() or "",
            empresa=perfil.empresa,
            empresa_nombre=(perfil.empresa.nombre if perfil.empresa else ""),
            oid=oid,
            ip=_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:1000],
        )

    from .auditoria import auditar_login

    auditar_login(
        request,
        usuario=user,
        empresa=perfil.empresa if perfil.tipo in TIPOS_CLIENTE else None,
    )

    next_url = request.session.pop("entra_oauth_next", None) or reverse("maestro:home")
    return redirect(next_url)


@require_GET
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    messages.success(request, "Sesión cerrada correctamente.")
    return redirect("maestro:login")


@login_required
@require_GET
def home(request: HttpRequest) -> HttpResponse:
    perfil = get_perfil(request.user)
    context = {
        "entra_name": request.session.get("entra_name")
        or request.user.get_full_name()
        or request.user.username,
        "perfil": perfil,
        "es_administrador": usuario_es_administrador(request.user),
        "es_gestor": usuario_es_gestor(request.user),
        "es_cliente": usuario_es_cliente(request.user),
    }
    if context["es_administrador"]:
        context.update(
            {
                "total_usuarios": PerfilUsuario.objects.count(),
                "total_empresas": Empresa.objects.filter(activo=True).count(),
                "total_accesos_denegados": IntentoAcceso.objects.count(),
            }
        )
    elif context["es_cliente"] and perfil and perfil.empresa_id:
        revision = revision_cliente_actual(request)
        context["empresa"] = perfil.empresa
        context["revision"] = revision
        context["cliente_puede_cargar"] = usuario_cliente_puede_cargar(request.user)
        if revision:
            context["progreso"] = calcular_progreso_revision(revision)
    elif context["es_gestor"]:
        context["total_empresas_asignadas"] = empresas_visibles(request.user).count()
    return render(request, "maestro/home.html", context)


@administrador_required
@require_GET
def usuario_list(request: HttpRequest) -> HttpResponse:
    perfiles = (
        PerfilUsuario.objects.select_related("user", "empresa")
        .prefetch_related("empresas")
        .all()
    )
    return render(request, "maestro/usuario_list.html", {"perfiles": perfiles})


@administrador_required
@require_http_methods(["GET", "POST"])
def usuario_create(request: HttpRequest) -> HttpResponse:
    form = UsuarioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Usuario creado correctamente.")
        return redirect("maestro:usuario_list")
    return render(
        request,
        "maestro/usuario_form.html",
        {"form": form, "titulo": "Nuevo usuario", "modo": "create"},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def usuario_edit(request: HttpRequest, pk: int) -> HttpResponse:
    perfil = get_object_or_404(
        PerfilUsuario.objects.select_related("user", "empresa").prefetch_related("empresas"),
        pk=pk,
    )
    form = UsuarioForm(request.POST or None, instance=perfil)
    if request.method == "POST" and form.is_valid():
        estaba_activo = perfil.activo and perfil.user.is_active
        form.save()
        perfil.refresh_from_db()
        from .auditoria import auditar_archivo, auditar_edicion

        email = perfil.user.email or perfil.user.username
        ahora_activo = perfil.activo and perfil.user.is_active
        if estaba_activo and not ahora_activo:
            auditar_archivo(
                request,
                detalle=f"Usuario «{email}» desactivado",
                empresa=perfil.empresa,
                objeto_tipo="perfilusuario",
                objeto_id=perfil.pk,
            )
        elif (not estaba_activo) and ahora_activo:
            auditar_archivo(
                request,
                detalle=f"Usuario «{email}» reactivado",
                empresa=perfil.empresa,
                objeto_tipo="perfilusuario",
                objeto_id=perfil.pk,
            )
        else:
            auditar_edicion(
                request,
                detalle=f"Edición de usuario «{email}» ({perfil.get_tipo_display()})",
                empresa=perfil.empresa,
                objeto_tipo="perfilusuario",
                objeto_id=perfil.pk,
            )
        messages.success(request, "Usuario actualizado correctamente.")
        return redirect("maestro:usuario_list")
    return render(
        request,
        "maestro/usuario_form.html",
        {"form": form, "titulo": "Editar usuario", "modo": "edit", "perfil": perfil},
    )


@administrador_required
@require_GET
def empresa_list(request: HttpRequest) -> HttpResponse:
    empresas = Empresa.objects.annotate(total_areas=Count("areas")).all()
    return render(request, "maestro/empresa_list.html", {"empresas": empresas})


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_create(request: HttpRequest) -> HttpResponse:
    form = EmpresaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        empresa = form.save()
        messages.success(
            request,
            "Empresa creada. Ahora puedes configurar áreas y responsables.",
        )
        return redirect("maestro:empresa_areas", pk=empresa.pk)
    return render(
        request,
        "maestro/empresa_form.html",
        {"form": form, "titulo": "Nueva empresa"},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_edit(request: HttpRequest, pk: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    form = EmpresaForm(request.POST or None, instance=empresa)
    if request.method == "POST" and form.is_valid():
        era_activa = empresa.activo
        form.save()
        empresa.refresh_from_db()
        from .auditoria import auditar_archivo, auditar_edicion

        if era_activa and not empresa.activo:
            auditar_archivo(
                request,
                detalle=f"Empresa «{empresa.nombre}» desactivada",
                empresa=empresa,
                objeto_tipo="empresa",
                objeto_id=empresa.pk,
            )
        elif (not era_activa) and empresa.activo:
            auditar_archivo(
                request,
                detalle=f"Empresa «{empresa.nombre}» reactivada",
                empresa=empresa,
                objeto_tipo="empresa",
                objeto_id=empresa.pk,
            )
        else:
            auditar_edicion(
                request,
                detalle=f"Edición de empresa «{empresa.nombre}»",
                empresa=empresa,
                objeto_tipo="empresa",
                objeto_id=empresa.pk,
            )
        messages.success(request, "Empresa actualizada correctamente.")
        return redirect("maestro:empresa_list")
    return render(
        request,
        "maestro/empresa_form.html",
        {
            "form": form,
            "titulo": "Editar empresa",
            "empresa": empresa,
        },
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_areas(request: HttpRequest, pk: int) -> HttpResponse:
    """Gestión separada de áreas y responsables de una empresa."""
    empresa = get_object_or_404(Empresa, pk=pk)
    area_form = AreaCreateForm(empresa=empresa)
    responsable_forms: dict[int, ResponsableForm] = {}

    if request.method == "POST":
        accion = (request.POST.get("accion") or "").strip()
        if accion == "crear_area":
            area_form = AreaCreateForm(request.POST, empresa=empresa)
            if area_form.is_valid():
                area_form.save()
                messages.success(request, "Área creada.")
                return redirect("maestro:empresa_areas", pk=empresa.pk)
        elif accion == "crear_responsable":
            area_id = (request.POST.get("area_id") or "").strip()
            area = empresa.areas.filter(pk=area_id).first() if area_id.isdigit() else None
            if not area:
                messages.error(request, "Área no válida.")
                return redirect("maestro:empresa_areas", pk=empresa.pk)
            prefix = f"resp-{area.pk}"
            form_resp = ResponsableForm(request.POST, area=area, prefix=prefix)
            if form_resp.is_valid():
                form_resp.save()
                messages.success(request, f"Responsable agregado a {area.nombre}.")
                return redirect("maestro:empresa_areas", pk=empresa.pk)
            responsable_forms[area.pk] = form_resp
            messages.error(request, "Revisa los datos del responsable.")
            # Se reabre el modal en la plantilla vía open_modal_area_id.
        elif accion == "toggle_area":
            area_id = (request.POST.get("area_id") or "").strip()
            area = empresa.areas.filter(pk=area_id).first() if area_id.isdigit() else None
            if area:
                area.activo = not area.activo
                area.save(update_fields=["activo", "actualizado_en"])
                messages.success(
                    request,
                    f"Área «{area.nombre}» marcada como {'activa' if area.activo else 'inactiva'}.",
                )
            return redirect("maestro:empresa_areas", pk=empresa.pk)
        elif accion == "toggle_responsable":
            resp_id = (request.POST.get("responsable_id") or "").strip()
            resp = (
                Responsable.objects.filter(pk=resp_id, area__empresa=empresa).first()
                if resp_id.isdigit()
                else None
            )
            if resp:
                resp.activo = not resp.activo
                resp.save(update_fields=["activo", "actualizado_en"])
                from .auditoria import auditar_archivo

                auditar_archivo(
                    request,
                    detalle=(
                        f"Responsable «{resp.nombre}» "
                        f"{'activado' if resp.activo else 'desactivado'}"
                    ),
                    empresa=empresa,
                    objeto_tipo="responsable",
                    objeto_id=resp.pk,
                )
                messages.success(
                    request,
                    f"Responsable «{resp.nombre}» marcado como {'activo' if resp.activo else 'inactivo'}.",
                )
            return redirect("maestro:empresa_areas", pk=empresa.pk)

    areas = list(
        empresa.areas.prefetch_related("responsables").order_by("nombre")
    )
    areas_ctx = []
    open_modal_area_id = None
    for area in areas:
        form_resp = responsable_forms.get(area.pk) or ResponsableForm(
            area=area, prefix=f"resp-{area.pk}"
        )
        if area.pk in responsable_forms:
            open_modal_area_id = area.pk
        areas_ctx.append({"area": area, "responsable_form": form_resp})

    return render(
        request,
        "maestro/empresa_areas.html",
        {
            "empresa": empresa,
            "areas_ctx": areas_ctx,
            "area_form": area_form,
            "open_modal_area_id": open_modal_area_id,
        },
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_notificaciones(request: HttpRequest, pk: int) -> HttpResponse:
    """Configuración por empresa de avisos pre/post vencimiento de acuerdos."""
    empresa = get_object_or_404(Empresa, pk=pk)
    regla_form = EmpresaNotificacionReglaForm(empresa=empresa)

    if request.method == "POST":
        accion = (request.POST.get("accion") or "").strip()
        if accion == "crear_regla":
            regla_form = EmpresaNotificacionReglaForm(request.POST, empresa=empresa)
            if regla_form.is_valid():
                creadas = regla_form.save()
                n = len(creadas)
                messages.success(
                    request,
                    f"{n} regla(s) de notificación agregada(s)."
                    if n != 1
                    else f"Regla «{creadas[0].etiqueta}» agregada.",
                )
                return redirect("maestro:empresa_notificaciones", pk=empresa.pk)
            messages.error(request, "Revisa los datos de la regla.")
        elif accion == "toggle_regla":
            regla_id = (request.POST.get("regla_id") or "").strip()
            regla = (
                empresa.notificacion_reglas.filter(pk=regla_id).first()
                if regla_id.isdigit()
                else None
            )
            if regla:
                regla.activo = not regla.activo
                regla.save(update_fields=["activo", "actualizado_en"])
                from .auditoria import auditar_archivo

                auditar_archivo(
                    request,
                    detalle=(
                        f"Regla de notificación «{regla.etiqueta}» "
                        f"{'activada' if regla.activo else 'desactivada'}"
                    ),
                    empresa=empresa,
                    objeto_tipo="empresanotificacionregla",
                    objeto_id=regla.pk,
                )
                messages.success(
                    request,
                    f"Regla «{regla.etiqueta}» marcada como "
                    f"{'activa' if regla.activo else 'inactiva'}.",
                )
            return redirect("maestro:empresa_notificaciones", pk=empresa.pk)

    reglas = list(empresa.notificacion_reglas.order_by("tipo", "dias"))
    reglas_antes = [r for r in reglas if r.tipo == TipoNotificacionAcuerdo.ANTES]
    reglas_post = [r for r in reglas if r.tipo == TipoNotificacionAcuerdo.POST]

    return render(
        request,
        "maestro/empresa_notificaciones.html",
        {
            "empresa": empresa,
            "regla_form": regla_form,
            "reglas_antes": reglas_antes,
            "reglas_post": reglas_post,
            "TipoNotificacionAcuerdo": TipoNotificacionAcuerdo,
        },
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
        .annotate(total_criterios=Count("vinculos_criterio"))
        .order_by("codigo")
    )
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
            "total_catalogo": total_catalogo,
            "avance_pct": avance_pct,
            "criterios": criterios,
            "filtro_q": q,
            "filtro_doc_q": doc_q,
            "filtro_doc_estado": doc_estado,
            "tab": tab,
        },
    )


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
        with transaction.atomic():
            marcar_prio = bool(form.cleaned_data.get("prioritario"))
            for doc in form.cleaned_data["documentos"]:
                _, created = EmpresaDocumento.objects.get_or_create(
                    revision=revision,
                    documento=doc,
                    defaults={
                        "codigo": doc.codigo,
                        "nombre": doc.nombre,
                        "tipo_documento": doc.tipo_documento,
                        "origen": OrigenEmpresaDocumento.CATALOGO,
                        "activo": True,
                        "prioritario": marcar_prio,
                    },
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
        form.save()
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
@require_GET
def acceso_denegado_list(request: HttpRequest) -> HttpResponse:
    intentos = IntentoAcceso.objects.all()[:200]
    return render(request, "maestro/acceso_denegado_list.html", {"intentos": intentos})


@administrador_required
@require_GET
def login_externo_list(request: HttpRequest) -> HttpResponse:
    hoy = timezone.localdate()
    fecha_desde_default = hoy - timedelta(days=7)
    fecha_hasta_default = hoy

    empresa_id = (request.GET.get("empresa") or "").strip()
    fecha_desde_raw = (request.GET.get("fecha_desde") or "").strip()
    fecha_hasta_raw = (request.GET.get("fecha_hasta") or "").strip()

    # Sin parámetros de fecha: última semana por defecto.
    if "fecha_desde" not in request.GET and "fecha_hasta" not in request.GET:
        fecha_desde = fecha_desde_default
        fecha_hasta = fecha_hasta_default
    else:
        try:
            fecha_desde = (
                datetime.strptime(fecha_desde_raw, "%Y-%m-%d").date()
                if fecha_desde_raw
                else None
            )
        except ValueError:
            fecha_desde = fecha_desde_default
            messages.warning(request, "Fecha desde inválida. Se usó el valor por defecto.")
        try:
            fecha_hasta = (
                datetime.strptime(fecha_hasta_raw, "%Y-%m-%d").date()
                if fecha_hasta_raw
                else None
            )
        except ValueError:
            fecha_hasta = fecha_hasta_default
            messages.warning(request, "Fecha hasta inválida. Se usó el valor por defecto.")

    registros = RegistroLoginExterno.objects.select_related("user", "empresa").all()

    if empresa_id.isdigit():
        registros = registros.filter(empresa_id=int(empresa_id))

    if fecha_desde:
        start = timezone.make_aware(datetime.combine(fecha_desde, datetime.min.time()))
        registros = registros.filter(creado_en__gte=start)
    if fecha_hasta:
        end = timezone.make_aware(
            datetime.combine(fecha_hasta, datetime.max.time().replace(microsecond=0))
        )
        registros = registros.filter(creado_en__lte=end)

    registros = registros[:500]

    return render(
        request,
        "maestro/login_externo_list.html",
        {
            "registros": registros,
            "empresas": Empresa.objects.filter(activo=True).order_by("nombre"),
            "filtro_empresa": empresa_id,
            "filtro_fecha_desde": (fecha_desde or fecha_desde_default).isoformat(),
            "filtro_fecha_hasta": (fecha_hasta or fecha_hasta_default).isoformat(),
        },
    )


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
    eliminadas, _ = DocumentoCriterio.objects.all().delete()
    if eliminadas:
        messages.success(
            request,
            f"Se eliminaron {eliminadas} relación(es) del estándar.",
        )
    else:
        messages.info(request, "No había relaciones estándar para eliminar.")
    return redirect("maestro:documento_criterio_list")


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_documento_aplicar_estandar(
    request: HttpRequest, pk: int, rev_id: int
) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    form = AplicarEstandarRevisionForm(request.POST or None, revision=revision)

    estandar = DocumentoCriterio.objects.select_related(
        "documento", "documento_secundario", "criterio"
    )
    doc_ids_estandar: set[int] = set()
    for rel in estandar:
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
        for rel in estandar
        if rel.criterio_id not in desactivados_ids
        and rel.criterio_id not in criterios_ya_asignados
    )

    if request.method == "POST" and form.is_valid():
        area = form.cleaned_data["area"]
        with transaction.atomic():
            docs_creados, vinculos_creados = aplicar_estandar_a_revision(revision, area)
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


SESSION_APROBACION_PENDIENTE = "aprobacion_pendiente_vigencia"


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
) -> None:
    request.session[SESSION_APROBACION_PENDIENTE] = {
        "empresa_id": empresa.pk,
        "doc_id": doc.pk,
        "carga_id": carga.pk,
        "decisions": [
            {"pregunta_id": p.pk, "estado": estado, "obs": obs}
            for p, estado, obs in decisions
        ],
    }
    request.session.modified = True


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
        messages.error(request, "Revisa la etiqueta y el porcentaje (0–100).")
        return redirect(next_url or fallback)

    texto = (form.cleaned_data.get("revision_texto") or "").strip()
    pct = form.cleaned_data.get("revision_porcentaje")
    doc.revision_texto = texto
    doc.revision_porcentaje = pct
    doc.save(update_fields=["revision_texto", "revision_porcentaje", "actualizado_en"])
    if texto or pct is not None:
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
    # Aprobación total solo se confirma al guardar vigencia.
    if _quedaria_aprobado(carga, decisions) and not _doc_tiene_vigencia(doc):
        _guardar_aprobacion_pendiente(
            request, empresa=empresa, doc=doc, carga=carga, decisions=decisions
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

    n_ok, n_no = _aplicar_revision_parcial_carga(
        carga=carga,
        decisions=decisions,
        user=request.user,
        request=request,
    )
    carga.refresh_from_db()
    if carga.estado_revision == EstadoRevisionCarga.APROBADO:
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
    if _quedaria_aprobado(carga, decisions) and not _doc_tiene_vigencia(doc):
        _guardar_aprobacion_pendiente(
            request, empresa=empresa, doc=doc, carga=carga, decisions=decisions
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
        "chart_general_json": "{}",
        "chart_temas_json": "{}",
        "chart_principios_json": "{}",
        "chart_tablero_progreso_json": "{}",
        "chart_tablero_estandar_json": "{}",
        "chart_curva_json": "{}",
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
        context["chart_general_json"] = to_json(chart_general(progreso))
        context["chart_temas_json"] = to_json(chart_temas(progreso))
        context["chart_principios_json"] = to_json(chart_principios(principios))
        if tab == "progreso":
            context["chart_tablero_progreso_json"] = to_json(
                tablero_progreso(progreso, empresa=empresa)
            )
        if tab == "general":
            context["chart_curva_json"] = to_json(curva_avance(revision, progreso))
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
        context["chart_tablero_estandar_json"] = to_json(tablero_estandar())

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
    )

    asegurar_plantilla_acuerdo()
    plantilla = get_object_or_404(PlantillaCorreo, pk=pk)
    tab = (request.POST.get("tab") or request.GET.get("tab") or "html").strip().lower()
    if tab not in {"html", "preview"}:
        tab = "html"

    if request.method == "POST":
        nombre = (request.POST.get("nombre") or "").strip() or plantilla.nombre
        html = request.POST.get("html") or ""
        descripcion = request.POST.get("descripcion") or ""
        activo = request.POST.get("activo") == "on"
        if not html.strip():
            messages.error(request, "El HTML de la plantilla no puede estar vacío.")
            tab = "html"
        else:
            plantilla.nombre = nombre[:120]
            plantilla.html = html
            plantilla.descripcion = descripcion
            plantilla.activo = activo
            plantilla.actualizado_por = request.user
            plantilla.save()
            messages.success(request, "Plantilla de correo guardada.")
            return redirect(
                f"{reverse('maestro:plantilla_correo_edit', args=[plantilla.pk])}?tab={tab}"
            )

    # Vista previa: logo por URL estática (CID solo funciona en el correo enviado).
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
        html_src = request.POST.get("html") if request.method == "POST" else plantilla.html
        try:
            from django.template import Context, Template

            preview_html = Template(html_src or plantilla.html).render(
                Context(ctx, autoescape=True)
            )
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
            "preview_html": preview_html,
            "tab": tab,
        },
    )


@login_required
@require_GET
def evidencia_abrir(request: HttpRequest, carga_id: int) -> HttpResponse:
    """Registra descarga/apertura y redirige a la evidencia (archivo o URL)."""
    carga = get_object_or_404(
        DocumentoCarga.objects.select_related(
            "empresa_documento",
            "empresa_documento__revision",
            "empresa_documento__revision__empresa",
        ),
        pk=carga_id,
    )
    empresa = carga.empresa_documento.revision.empresa
    if not puede_ver_empresa(request.user, empresa):
        messages.error(request, "No tienes acceso a esta evidencia.")
        return redirect("maestro:home")
    if usuario_es_cliente(request.user) and not puede_ver_revision(
        request.user, carga.empresa_documento.revision
    ):
        messages.error(request, "No tienes acceso a la revisión de esta evidencia.")
        return redirect("maestro:mi_empresa_documentos")
    url = carga.evidencia_url
    if not url:
        messages.error(request, "Esta evidencia no tiene archivo ni URL disponible.")
        return redirect("maestro:documento_conversacion", doc_id=carga.empresa_documento_id)
    from .auditoria import auditar_descarga

    auditar_descarga(request, carga=carga)
    return redirect(url)


@gestor_o_admin_required
@require_GET
def auditoria_list(request: HttpRequest) -> HttpResponse:
    """Consulta del log de auditoría (Anexo 01 §9.5)."""
    from datetime import datetime as dt

    from django.core.paginator import Paginator

    from .models import RegistroAuditoria, TipoAuditoria

    qs = RegistroAuditoria.objects.select_related("usuario", "empresa").order_by(
        "-creado_en"
    )
    if not usuario_es_administrador(request.user):
        qs = qs.filter(empresa_id__in=empresas_visibles(request.user))

    tipo = (request.GET.get("tipo") or "").strip()
    if tipo in {c.value for c in TipoAuditoria}:
        qs = qs.filter(tipo=tipo)

    empresa_id = (request.GET.get("empresa") or "").strip()
    if empresa_id.isdigit():
        qs = qs.filter(empresa_id=int(empresa_id))

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(detalle__icontains=q)
            | Q(usuario_email__icontains=q)
            | Q(usuario_nombre__icontains=q)
            | Q(empresa_nombre__icontains=q)
        )

    hoy = timezone.localdate()
    fecha_desde_raw = (request.GET.get("fecha_desde") or "").strip()
    fecha_hasta_raw = (request.GET.get("fecha_hasta") or "").strip()
    if "fecha_desde" not in request.GET and "fecha_hasta" not in request.GET:
        fecha_desde = hoy - timedelta(days=30)
        fecha_hasta = hoy
    else:
        try:
            fecha_desde = (
                dt.strptime(fecha_desde_raw, "%Y-%m-%d").date()
                if fecha_desde_raw
                else None
            )
        except ValueError:
            fecha_desde = hoy - timedelta(days=30)
        try:
            fecha_hasta = (
                dt.strptime(fecha_hasta_raw, "%Y-%m-%d").date()
                if fecha_hasta_raw
                else None
            )
        except ValueError:
            fecha_hasta = hoy

    if fecha_desde:
        qs = qs.filter(creado_en__date__gte=fecha_desde)
    if fecha_hasta:
        qs = qs.filter(creado_en__date__lte=fecha_hasta)

    page = Paginator(qs, 50).get_page(request.GET.get("page") or 1)
    return render(
        request,
        "maestro/auditoria_list.html",
        {
            "page": page,
            "registros": page.object_list,
            "tipos": TipoAuditoria.choices,
            "tipo": tipo,
            "q": q,
            "empresa_id": empresa_id,
            "empresas": empresas_visibles(request.user).filter(activo=True),
            "fecha_desde": fecha_desde.isoformat() if fecha_desde else "",
            "fecha_hasta": fecha_hasta.isoformat() if fecha_hasta else "",
        },
    )
