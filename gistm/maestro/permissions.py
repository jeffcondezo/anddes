from __future__ import annotations

from functools import wraps
from typing import Callable

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from .models import (
    Actividad,
    AlcanceActividad,
    Empresa,
    PerfilUsuario,
    Revision,
    TIPOS_CLIENTE,
    TipoUsuario,
)

User = get_user_model()

SESSION_CLIENTE_REV = "cliente_revision_id"


def get_perfil(user) -> PerfilUsuario | None:
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        return user.perfil
    except PerfilUsuario.DoesNotExist:
        return None


def usuario_es_administrador(user) -> bool:
    perfil = get_perfil(user)
    return bool(perfil and perfil.es_administrador)


def usuario_es_gestor(user) -> bool:
    perfil = get_perfil(user)
    return bool(perfil and perfil.tipo == TipoUsuario.GESTOR and perfil.activo)


def usuario_es_cliente(user) -> bool:
    perfil = get_perfil(user)
    return bool(perfil and perfil.tipo in TIPOS_CLIENTE and perfil.activo)


def usuario_cliente_puede_cargar(user) -> bool:
    """Cliente con permiso de carga de evidencias (no solo lectura)."""
    perfil = get_perfil(user)
    return bool(perfil and perfil.es_cliente_carga)


def empresas_visibles(user) -> QuerySet[Empresa]:
    """Empresas que el usuario puede ver según su rol."""
    qs = Empresa.objects.all().order_by("nombre")
    if usuario_es_administrador(user):
        return qs
    perfil = get_perfil(user)
    if not perfil:
        return qs.none()
    if usuario_es_gestor(user):
        return perfil.empresas.all().order_by("nombre")
    if usuario_es_cliente(user) and perfil.empresa_id:
        return qs.filter(pk=perfil.empresa_id)
    return qs.none()


def puede_ver_empresa(user, empresa: Empresa) -> bool:
    if usuario_es_administrador(user):
        return True
    perfil = get_perfil(user)
    if not perfil:
        return False
    if usuario_es_gestor(user):
        return perfil.empresas.filter(pk=empresa.pk).exists()
    if usuario_es_cliente(user):
        return perfil.empresa_id == empresa.pk
    return False


def revisiones_visibles_cliente(user) -> QuerySet[Revision]:
    """Revisiones explícitamente asignadas al cliente (de su empresa)."""
    perfil = get_perfil(user)
    if not perfil or not usuario_es_cliente(user) or not perfil.empresa_id:
        return Revision.objects.none()
    return (
        perfil.revisiones.filter(empresa_id=perfil.empresa_id)
        .select_related("empresa")
        .order_by("-anio", "-creado_en")
    )


def puede_ver_revision(user, revision: Revision) -> bool:
    if usuario_es_administrador(user):
        return True
    if usuario_es_gestor(user):
        return puede_ver_empresa(user, revision.empresa)
    if usuario_es_cliente(user):
        perfil = get_perfil(user)
        if not perfil or perfil.empresa_id != revision.empresa_id:
            return False
        return perfil.revisiones.filter(pk=revision.pk).exists()
    return False


def revision_cliente_actual(
    request: HttpRequest, *, rev_id: int | None = None
) -> Revision | None:
    """
    Revisión de trabajo del cliente: parámetro ?rev=, sesión, o la más reciente
    entre las permitidas (prioriza activas).
    """
    qs = revisiones_visibles_cliente(request.user)
    if not qs.exists():
        return None
    if rev_id is None:
        raw = (request.GET.get("rev") or request.POST.get("rev") or "").strip()
        if raw.isdigit():
            rev_id = int(raw)
        else:
            session_rev = request.session.get(SESSION_CLIENTE_REV)
            if session_rev:
                rev_id = int(session_rev)
    if rev_id is not None:
        elegida = qs.filter(pk=rev_id).first()
        if elegida is not None:
            request.session[SESSION_CLIENTE_REV] = elegida.pk
            return elegida
    activa = qs.filter(activo=True).first()
    if activa is not None:
        request.session[SESSION_CLIENTE_REV] = activa.pk
        return activa
    primera = qs.first()
    if primera is not None:
        request.session[SESSION_CLIENTE_REV] = primera.pk
    return primera


def administrador_required(view_func: Callable) -> Callable:
    @login_required
    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not usuario_es_administrador(request.user):
            messages.error(request, "No tienes permisos de administrador para esta acción.")
            return redirect("maestro:home")
        return view_func(request, *args, **kwargs)

    return _wrapped


def cliente_required(view_func: Callable) -> Callable:
    @login_required
    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not usuario_es_cliente(request.user):
            messages.error(request, "Esta sección es solo para usuarios cliente.")
            return redirect("maestro:home")
        perfil = get_perfil(request.user)
        if not perfil or not perfil.empresa_id:
            messages.error(request, "Tu perfil de cliente no tiene empresa asignada.")
            return redirect("maestro:home")
        return view_func(request, *args, **kwargs)

    return _wrapped


def cliente_carga_required(view_func: Callable) -> Callable:
    """Portal cliente con permiso de carga (archivo/URL y documento propio)."""

    @cliente_required
    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not usuario_cliente_puede_cargar(request.user):
            messages.error(
                request,
                "Tu perfil es de solo lectura; no puedes cargar evidencias.",
            )
            return redirect("maestro:mi_empresa_documentos")
        return view_func(request, *args, **kwargs)

    return _wrapped


def gestor_o_admin_required(view_func: Callable) -> Callable:
    @login_required
    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not (
            usuario_es_administrador(request.user) or usuario_es_gestor(request.user)
        ):
            messages.error(request, "No tienes permisos para ver el seguimiento.")
            return redirect("maestro:home")
        return view_func(request, *args, **kwargs)

    return _wrapped


def gestor_admin_o_cliente_required(view_func: Callable) -> Callable:
    """Lectura de indicadores / actividades / acuerdos (cliente solo su empresa)."""

    @login_required
    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not (
            usuario_es_administrador(request.user)
            or usuario_es_gestor(request.user)
            or usuario_es_cliente(request.user)
        ):
            messages.error(request, "No tienes permisos para esta sección.")
            return redirect("maestro:home")
        if usuario_es_cliente(request.user):
            perfil = get_perfil(request.user)
            if not perfil or not perfil.empresa_id:
                messages.error(
                    request, "Tu perfil de cliente no tiene empresa asignada."
                )
                return redirect("maestro:home")
        return view_func(request, *args, **kwargs)

    return _wrapped


def tipo_usuario_label(tipo: str) -> str:
    return dict(TipoUsuario.choices).get(tipo, tipo)


def ids_equipo(user, *, max_depth: int = 20) -> set[int]:
    """
    IDs de usuario del consultor y todos sus subordinados en cascada
    (quien tiene reporta_a apuntando a él, recursivo).
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        return set()
    equipo = {user.pk}
    frontera = {user.pk}
    profundidad = 0
    while frontera and profundidad < max_depth:
        hijos = set(
            PerfilUsuario.objects.filter(
                reporta_a_id__in=frontera,
                activo=True,
                user__is_active=True,
            ).values_list("user_id", flat=True)
        )
        nuevos = hijos - equipo
        if not nuevos:
            break
        equipo |= nuevos
        frontera = nuevos
        profundidad += 1
    return equipo


def usuarios_asignables(user) -> QuerySet:
    """Gestores a los que el usuario puede asignar una actividad."""
    qs = User.objects.filter(
        is_active=True,
        perfil__activo=True,
        perfil__tipo=TipoUsuario.GESTOR,
    ).select_related("perfil").order_by("first_name", "last_name", "email")
    if usuario_es_administrador(user):
        return qs
    if not usuario_es_gestor(user):
        return qs.none()
    return qs.filter(pk__in=ids_equipo(user))


def actividades_visibles(user) -> QuerySet[Actividad]:
    """Actividades que el usuario puede ver (propias + equipo / todas si admin)."""
    qs = Actividad.objects.all()
    if usuario_es_administrador(user):
        return qs
    if usuario_es_cliente(user):
        perfil = get_perfil(user)
        if not perfil or not perfil.empresa_id:
            return qs.none()
        return qs.filter(
            alcance=AlcanceActividad.CON_CLIENTE,
            empresa_id=perfil.empresa_id,
        )
    if not usuario_es_gestor(user):
        return qs.none()
    equipo = ids_equipo(user)
    if not equipo:
        return qs.none()
    return qs.filter(Q(asignado_a_id__in=equipo) | Q(creado_por_id__in=equipo))


def puede_ver_actividad(user, actividad: Actividad) -> bool:
    if usuario_es_administrador(user):
        return True
    if not actividad:
        return False
    if usuario_es_cliente(user):
        perfil = get_perfil(user)
        return bool(
            perfil
            and actividad.alcance == AlcanceActividad.CON_CLIENTE
            and actividad.empresa_id == perfil.empresa_id
        )
    equipo = ids_equipo(user)
    return (
        actividad.asignado_a_id in equipo or actividad.creado_por_id in equipo
    )
