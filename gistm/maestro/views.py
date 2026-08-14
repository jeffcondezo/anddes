from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .entra import build_auth_url, claims_from_id_token, exchange_code_for_token, new_oauth_state
from .forms import EmpresaForm, UsuarioForm
from .models import (
    Empresa,
    IntentoAcceso,
    MotivoIntentoAcceso,
    PerfilUsuario,
    RegistroLoginExterno,
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
    empresas = Empresa.objects.all()
    return render(request, "maestro/empresa_list.html", {"empresas": empresas})


@administrador_required
@require_http_methods(["GET", "POST"])
def empresa_create(request: HttpRequest) -> HttpResponse:
    form = EmpresaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Empresa creada correctamente.")
        return redirect("maestro:empresa_list")
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
        form.save()
        messages.success(request, "Empresa actualizada correctamente.")
        return redirect("maestro:empresa_list")
    return render(
        request,
        "maestro/empresa_form.html",
        {"form": form, "titulo": "Editar empresa", "empresa": empresa},
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
