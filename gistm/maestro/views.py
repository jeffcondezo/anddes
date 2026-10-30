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


from .view_helpers import (  # noqa: E402
    rev_id_param as _rev_id_param,
    revision_seguimiento as _revision_seguimiento,
    seguimiento_url as _seguimiento_url,
)


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
    try:
        # archivo.url → GistmAzureStorage: SAS por blob, solo lectura, corta duración.
        url = carga.evidencia_url
    except ValueError as exc:
        messages.error(
            request,
            "No se pudo generar un enlace seguro a la evidencia. "
            "Revise la configuración de Azure Storage (AccountKey / identidad). "
            f"Detalle: {exc}",
        )
        return redirect("maestro:documento_conversacion", doc_id=carga.empresa_documento_id)
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


# ---------------------------------------------------------------------------
# Reexports (4.2): urls sigue importando maestro.views.*
# ---------------------------------------------------------------------------
from .views_catalogo import (  # noqa: E402,F401
    tema_list,
    tema_create,
    tema_edit,
    tema_toggle_activo,
    glosario_termino_list,
    glosario_termino_create,
    glosario_termino_edit,
    principio_list,
    principio_create,
    principio_edit,
    principio_toggle_activo,
    requisito_list,
    requisito_create,
    requisito_edit,
    requisito_toggle_activo,
    criterio_list,
    criterio_create,
    criterio_edit,
    criterio_toggle_activo,
    pregunta_list,
    pregunta_create,
    pregunta_edit,
    pregunta_toggle_activo,
    documento_list,
    documento_create,
    documento_edit,
    documento_criterio_list,
    documento_criterio_create,
    documento_criterio_edit,
    documento_criterio_delete,
    documento_criterio_plantilla,
    documento_criterio_importar,
    documento_criterio_borrar_todas,
)
from .views_actividad import (  # noqa: E402,F401
    _documentos_para_actividad,
    actividad_calendario,
    actividad_crear,
    actividad_detalle,
    _actividad_padre_ok,
    actividad_crear_acuerdo,
    _acuerdo_accesible,
    _redirect_acuerdo,
    acuerdo_bandeja,
    aviso_list,
    aviso_marcar_leido,
    aviso_marcar_todos,
    acuerdo_enviar_recordatorio,
    acuerdo_marcar_cumplido,
    actividad_completar,
    actividad_cancelar,
    recordatorio_list,
    plantilla_correo_list,
    plantilla_correo_edit,
)
from .views_indicadores import (  # noqa: E402,F401
    indicadores_list,
    _parse_fecha_get,
    _documentos_indicadores_filtrados,
    indicadores_empresa,
    indicadores_documentos_export,
)

from .views_seguimiento import (  # noqa: E402,F401
    SESSION_APROBACION_PENDIENTE,
    _doc_tiene_vigencia,
    _quedaria_aprobado,
    _guardar_aprobacion_pendiente,
    _aplicar_ubicaciones_criterio,
    _ubicaciones_desde_pendiente,
    _leer_aprobacion_pendiente,
    _limpiar_aprobacion_pendiente,
    _decisions_desde_pendiente,
    _revertir_aprobacion_sin_vigencia,
    seguimiento_documento_cargar,
    seguimiento_documento_asociar_criterios,
    seguimiento_empresa_monitoreo,
    seguimiento_empresa_revision,
    seguimiento_empresa_pendientes,
    empresa_revision_plan,
    _aplicar_revision_parcial_carga,
    seguimiento_documento_revision_consultor,
    seguimiento_carga_revisar,
    seguimiento_carga_aprobar,
    seguimiento_documento_vigencia,
    seguimiento_carga_revertir,
    seguimiento_carga_rechazar,
    seguimiento_list,
    seguimiento_empresa_detail,
)
from .views_portal import (  # noqa: E402,F401
    _cliente_contexto_revision,
    mi_empresa_dashboard,
    mi_empresa_documentos,
    mi_empresa_documento_cargar,
    documento_conversacion,
    mi_empresa_documento_nuevo,
)
from .views_empresa_revision import (  # noqa: E402,F401
    empresa_revision_list,
    empresa_revision_create,
    empresa_revision_edit,
    empresa_documento_list,
    empresa_documento_asignar_consultor,
    empresa_criterios_desactivar,
    empresa_criterios_reactivar,
    empresa_documento_activar_catalogo,
    empresa_documento_crear_propio,
    empresa_documento_toggle_activo,
    empresa_documento_toggle_prioritario,
    empresa_documento_criterios,
    empresa_documento_criterio_eliminar,
    empresa_documento_aplicar_estandar,
)
