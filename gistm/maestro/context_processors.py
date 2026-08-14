from __future__ import annotations

from .permissions import get_perfil, usuario_es_administrador


def perfil_usuario(request):
    user = getattr(request, "user", None)
    perfil = get_perfil(user) if user is not None else None
    return {
        "perfil": perfil,
        "es_administrador": usuario_es_administrador(user) if user is not None else False,
    }
