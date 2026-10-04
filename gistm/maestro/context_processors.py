from __future__ import annotations

from .avisos import avisos_recientes, contar_avisos_no_leidos
from .permissions import (
    get_perfil,
    usuario_es_administrador,
    usuario_es_cliente,
    usuario_es_gestor,
)


def perfil_usuario(request):
    user = getattr(request, "user", None)
    perfil = get_perfil(user) if user is not None else None
    es_admin = usuario_es_administrador(user) if user is not None else False
    es_gestor = usuario_es_gestor(user) if user is not None else False
    avisos_count = 0
    avisos_preview = []
    if user is not None and (es_admin or es_gestor):
        avisos_count = contar_avisos_no_leidos(user)
        avisos_preview = avisos_recientes(user, limite=6)
    return {
        "perfil": perfil,
        "es_administrador": es_admin,
        "es_gestor": es_gestor,
        "es_cliente": usuario_es_cliente(user) if user is not None else False,
        "avisos_no_leidos": avisos_count,
        "avisos_preview": avisos_preview,
    }
