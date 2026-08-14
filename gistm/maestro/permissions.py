from __future__ import annotations

from functools import wraps
from typing import Callable

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from .models import PerfilUsuario, TipoUsuario


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


def administrador_required(view_func: Callable) -> Callable:
    @login_required
    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not usuario_es_administrador(request.user):
            messages.error(request, "No tienes permisos de administrador para esta acción.")
            return redirect("maestro:home")
        return view_func(request, *args, **kwargs)

    return _wrapped


def tipo_usuario_label(tipo: str) -> str:
    return dict(TipoUsuario.choices).get(tipo, tipo)
