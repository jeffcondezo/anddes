from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .entra import build_auth_url, claims_from_id_token, exchange_code_for_token, new_oauth_state
from .forms import (
    ActivarDocumentosCatalogoForm,
    AplicarEstandarRevisionForm,
    AreaFormSet,
    CriterioForm,
    DocumentoCriterioForm,
    DocumentoForm,
    EmpresaDocumentoCriterioForm,
    EmpresaDocumentoPropioForm,
    EmpresaForm,
    PrincipioForm,
    RevisionForm,
    TemaForm,
    UsuarioForm,
)
from .models import (
    Criterio,
    Documento,
    DocumentoCriterio,
    Empresa,
    EmpresaDocumento,
    EmpresaDocumentoCriterio,
    IntentoAcceso,
    MotivoIntentoAcceso,
    OrigenEmpresaDocumento,
    PerfilUsuario,
    Principio,
    RegistroLoginExterno,
    Revision,
    Tema,
    TipoUsuario,
)
from .permissions import administrador_required, get_perfil, usuario_es_administrador

User = get_user_model()


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

    if perfil.tipo == TipoUsuario.CLIENTE:
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
    }
    if context["es_administrador"]:
        context.update(
            {
                "total_usuarios": PerfilUsuario.objects.count(),
                "total_empresas": Empresa.objects.filter(activo=True).count(),
                "total_accesos_denegados": IntentoAcceso.objects.count(),
            }
        )
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
        form.save()
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
    formset = AreaFormSet(request.POST or None)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            empresa = form.save()
            formset.instance = empresa
            formset.save()
        messages.success(request, "Empresa creada correctamente.")
        return redirect("maestro:empresa_list")
    return render(
        request,
        "maestro/empresa_form.html",
        {"form": form, "formset": formset, "titulo": "Nueva empresa"},
    )


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_edit(request: HttpRequest, pk: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    form = EmpresaForm(request.POST or None, instance=empresa)
    formset = AreaFormSet(request.POST or None, instance=empresa)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            form.save()
            formset.save()
        messages.success(request, "Empresa actualizada correctamente.")
        return redirect("maestro:empresa_list")
    return render(
        request,
        "maestro/empresa_form.html",
        {
            "form": form,
            "formset": formset,
            "titulo": "Editar empresa",
            "empresa": empresa,
        },
    )


@administrador_required
@require_GET
def empresa_revision_list(request: HttpRequest, pk: int) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    total_criterios = Criterio.objects.count()
    revisiones = []
    for revision in Revision.objects.filter(empresa=empresa).annotate(
        total_documentos=Count("documentos"),
        docs_activos=Count("documentos", filter=Q(documentos__activo=True)),
    ):
        criterios_cubiertos = (
            EmpresaDocumentoCriterio.objects.filter(
                empresa_documento__revision=revision,
                empresa_documento__activo=True,
            )
            .values("criterio_id")
            .distinct()
            .count()
        )
        avance_pct = (
            round((criterios_cubiertos / total_criterios) * 100, 1) if total_criterios else 0
        )
        revisiones.append(
            {
                "obj": revision,
                "criterios_cubiertos": criterios_cubiertos,
                "total_criterios": total_criterios,
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
        form.save()
        messages.success(request, "Revisión creada correctamente.")
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
    documentos = (
        EmpresaDocumento.objects.filter(revision=revision)
        .annotate(total_criterios=Count("vinculos_criterio"))
        .order_by("codigo")
    )
    total_criterios = Criterio.objects.count()
    criterios_cubiertos = (
        EmpresaDocumentoCriterio.objects.filter(
            empresa_documento__revision=revision,
            empresa_documento__activo=True,
        )
        .values("criterio_id")
        .distinct()
        .count()
    )
    docs_activos = documentos.filter(activo=True).count()
    avance_pct = round((criterios_cubiertos / total_criterios) * 100, 1) if total_criterios else 0
    return render(
        request,
        "maestro/empresa_documento_list.html",
        {
            "empresa": empresa,
            "revision": revision,
            "documentos": documentos,
            "docs_activos": docs_activos,
            "criterios_cubiertos": criterios_cubiertos,
            "total_criterios": total_criterios,
            "avance_pct": avance_pct,
        },
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
        .select_related("criterio", "criterio__tema", "criterio__principio", "area")
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
@require_GET
def criterio_list(request: HttpRequest) -> HttpResponse:
    criterios = Criterio.objects.select_related("principio", "tema").all()
    tema_id = (request.GET.get("tema") or "").strip()
    principio_id = (request.GET.get("principio") or "").strip()
    if tema_id.isdigit():
        criterios = criterios.filter(tema_id=int(tema_id))
    if principio_id.isdigit():
        criterios = criterios.filter(principio_id=int(principio_id))
    return render(
        request,
        "maestro/criterio_list.html",
        {
            "criterios": criterios,
            "temas": Tema.objects.all(),
            "principios": Principio.objects.select_related("tema").all(),
            "filtro_tema": tema_id,
            "filtro_principio": principio_id,
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
    criterio = get_object_or_404(Criterio.objects.select_related("principio", "tema"), pk=pk)
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
        "criterio",
        "criterio__tema",
        "criterio__principio",
    ).all()
    documento_id = (request.GET.get("documento") or "").strip()
    tema_id = (request.GET.get("tema") or "").strip()
    principio_id = (request.GET.get("principio") or "").strip()
    if documento_id.isdigit():
        relaciones = relaciones.filter(documento_id=int(documento_id))
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
@require_http_methods(["POST"])
def documento_criterio_importar(request: HttpRequest) -> HttpResponse:
    from django.core.management import call_command
    from io import StringIO

    out = StringIO()
    try:
        call_command("importar_documento_criterio", stdout=out)
        messages.success(request, "Importación desde Excel completada. " + out.getvalue().strip())
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"No se pudo importar: {exc}")
    return redirect("maestro:documento_criterio_list")


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_documento_aplicar_estandar(
    request: HttpRequest, pk: int, rev_id: int
) -> HttpResponse:
    empresa = get_object_or_404(Empresa, pk=pk)
    revision = get_object_or_404(Revision, pk=rev_id, empresa=empresa)
    form = AplicarEstandarRevisionForm(request.POST or None, revision=revision)

    estandar = DocumentoCriterio.objects.select_related("documento", "criterio")
    doc_ids_estandar = set(estandar.values_list("documento_id", flat=True).distinct())
    ya_docs = set(
        revision.documentos.filter(documento_id__isnull=False).values_list(
            "documento_id", flat=True
        )
    )
    docs_a_activar = len(doc_ids_estandar - ya_docs)

    existentes = set(
        EmpresaDocumentoCriterio.objects.filter(
            empresa_documento__revision=revision,
            empresa_documento__documento_id__isnull=False,
        ).values_list("empresa_documento__documento_id", "criterio_id")
    )
    pares_estandar = set(estandar.values_list("documento_id", "criterio_id"))
    vinculos_a_crear = len(pares_estandar - existentes)

    if request.method == "POST" and form.is_valid():
        area = form.cleaned_data["area"]
        docs_creados = 0
        vinculos_creados = 0
        with transaction.atomic():
            for documento in Documento.objects.filter(pk__in=doc_ids_estandar):
                emp_doc, created = EmpresaDocumento.objects.get_or_create(
                    revision=revision,
                    documento=documento,
                    defaults={
                        "codigo": documento.codigo,
                        "nombre": documento.nombre,
                        "tipo_documento": documento.tipo_documento,
                        "origen": OrigenEmpresaDocumento.CATALOGO,
                        "activo": True,
                    },
                )
                if created:
                    docs_creados += 1

            emp_docs = {
                ed.documento_id: ed
                for ed in EmpresaDocumento.objects.filter(
                    revision=revision, documento_id__in=doc_ids_estandar
                )
            }
            for rel in DocumentoCriterio.objects.filter(documento_id__in=doc_ids_estandar):
                emp_doc = emp_docs.get(rel.documento_id)
                if emp_doc is None:
                    continue
                _, created = EmpresaDocumentoCriterio.objects.get_or_create(
                    empresa_documento=emp_doc,
                    criterio=rel.criterio,
                    defaults={"area": area},
                )
                if created:
                    vinculos_creados += 1

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
