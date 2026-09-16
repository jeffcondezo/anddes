from __future__ import annotations

from functools import wraps
from typing import Callable

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from .models import Empresa, PerfilUsuario, TipoUsuario


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
    return bool(perfil and perfil.tipo == TipoUsuario.CLIENTE and perfil.activo)


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


def tipo_usuario_label(tipo: str) -> str:
    return dict(TipoUsuario.choices).get(tipo, tipo)
