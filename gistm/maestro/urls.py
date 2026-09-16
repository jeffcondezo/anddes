from django.urls import path

from . import views

app_name = "maestro"

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("auth/entra/", views.entra_login, name="entra_login"),
    path("auth/callback/", views.entra_callback, name="entra_callback"),
    path("usuarios/", views.usuario_list, name="usuario_list"),
    path("usuarios/nuevo/", views.usuario_create, name="usuario_create"),
    path("usuarios/<int:pk>/editar/", views.usuario_edit, name="usuario_edit"),
    path("empresas/", views.empresa_list, name="empresa_list"),
    path("empresas/nueva/", views.empresa_create, name="empresa_create"),
    path("empresas/<int:pk>/editar/", views.empresa_edit, name="empresa_edit"),
    path("empresas/<int:pk>/revisiones/", views.empresa_revision_list, name="empresa_revision_list"),
    path("empresas/<int:pk>/revisiones/nueva/", views.empresa_revision_create, name="empresa_revision_create"),
    path(
        "empresas/<int:pk>/revisiones/<int:rev_id>/editar/",
        views.empresa_revision_edit,
        name="empresa_revision_edit",
    ),
    path(
        "empresas/<int:pk>/revisiones/<int:rev_id>/documentos/",
        views.empresa_documento_list,
        name="empresa_documento_list",
    ),
    path(
        "empresas/<int:pk>/revisiones/<int:rev_id>/documentos/criterios/desactivar/",
        views.empresa_criterios_desactivar,
        name="empresa_criterios_desactivar",
    ),
    path(
        "empresas/<int:pk>/revisiones/<int:rev_id>/documentos/criterios/reactivar/",
        views.empresa_criterios_reactivar,
        name="empresa_criterios_reactivar",
    ),
    path(
        "empresas/<int:pk>/revisiones/<int:rev_id>/documentos/activar-catalogo/",
        views.empresa_documento_activar_catalogo,
        name="empresa_documento_activar_catalogo",
    ),
    path(
        "empresas/<int:pk>/revisiones/<int:rev_id>/documentos/aplicar-estandar/",
        views.empresa_documento_aplicar_estandar,
        name="empresa_documento_aplicar_estandar",
    ),
    path(
        "empresas/<int:pk>/revisiones/<int:rev_id>/documentos/nuevo/",
        views.empresa_documento_crear_propio,
        name="empresa_documento_crear_propio",
    ),
    path(
        "empresas/<int:pk>/revisiones/<int:rev_id>/documentos/<int:doc_id>/toggle/",
        views.empresa_documento_toggle_activo,
        name="empresa_documento_toggle_activo",
    ),
    path(
        "empresas/<int:pk>/revisiones/<int:rev_id>/documentos/<int:doc_id>/criterios/",
        views.empresa_documento_criterios,
        name="empresa_documento_criterios",
    ),
    path(
        "empresas/<int:pk>/revisiones/<int:rev_id>/documentos/<int:doc_id>/criterios/<int:vinculo_id>/eliminar/",
        views.empresa_documento_criterio_eliminar,
        name="empresa_documento_criterio_eliminar",
    ),
    path("accesos-denegados/", views.acceso_denegado_list, name="acceso_denegado_list"),
    path("logins-empresas/", views.login_externo_list, name="login_externo_list"),
    path("catalogo/temas/", views.tema_list, name="tema_list"),
    path("catalogo/temas/nuevo/", views.tema_create, name="tema_create"),
    path("catalogo/temas/<int:pk>/editar/", views.tema_edit, name="tema_edit"),
    path("catalogo/principios/", views.principio_list, name="principio_list"),
    path("catalogo/principios/nuevo/", views.principio_create, name="principio_create"),
    path("catalogo/principios/<int:pk>/editar/", views.principio_edit, name="principio_edit"),
    path("catalogo/requisitos/", views.requisito_list, name="requisito_list"),
    path("catalogo/requisitos/nuevo/", views.requisito_create, name="requisito_create"),
    path("catalogo/requisitos/<int:pk>/editar/", views.requisito_edit, name="requisito_edit"),
    path("catalogo/criterios/", views.criterio_list, name="criterio_list"),
    path("catalogo/criterios/nuevo/", views.criterio_create, name="criterio_create"),
    path("catalogo/criterios/<int:pk>/editar/", views.criterio_edit, name="criterio_edit"),
    path("catalogo/documentos/", views.documento_list, name="documento_list"),
    path("catalogo/documentos/nuevo/", views.documento_create, name="documento_create"),
    path("catalogo/documentos/<int:pk>/editar/", views.documento_edit, name="documento_edit"),
    path(
        "catalogo/relacion-estandar/",
        views.documento_criterio_list,
        name="documento_criterio_list",
    ),
    path(
        "catalogo/relacion-estandar/nueva/",
        views.documento_criterio_create,
        name="documento_criterio_create",
    ),
    path(
        "catalogo/relacion-estandar/<int:pk>/editar/",
        views.documento_criterio_edit,
        name="documento_criterio_edit",
    ),
    path(
        "catalogo/relacion-estandar/<int:pk>/eliminar/",
        views.documento_criterio_delete,
        name="documento_criterio_delete",
    ),
    path(
        "catalogo/relacion-estandar/importar/",
        views.documento_criterio_importar,
        name="documento_criterio_importar",
    ),
    # Portal cliente
    path("mi-empresa/", views.mi_empresa_dashboard, name="mi_empresa_dashboard"),
    path("mi-empresa/documentos/", views.mi_empresa_documentos, name="mi_empresa_documentos"),
    path(
        "mi-empresa/documentos/nuevo/",
        views.mi_empresa_documento_nuevo,
        name="mi_empresa_documento_nuevo",
    ),
    path(
        "mi-empresa/documentos/<int:doc_id>/cargar/",
        views.mi_empresa_documento_cargar,
        name="mi_empresa_documento_cargar",
    ),
    path(
        "documentos/<int:doc_id>/conversacion/",
        views.documento_conversacion,
        name="documento_conversacion",
    ),
    # Seguimiento admin / gestor
    path("seguimiento/", views.seguimiento_list, name="seguimiento_list"),
    path(
        "seguimiento/empresas/<int:pk>/",
        views.seguimiento_empresa_detail,
        name="seguimiento_empresa_detail",
    ),
    path(
        "seguimiento/empresas/<int:pk>/monitoreo/",
        views.seguimiento_empresa_monitoreo,
        name="seguimiento_empresa_monitoreo",
    ),
    path(
        "seguimiento/empresas/<int:pk>/por-revisar/",
        views.seguimiento_empresa_revision,
        name="seguimiento_empresa_revision",
    ),
    path(
        "seguimiento/empresas/<int:pk>/cargas/<int:carga_id>/aprobar/",
        views.seguimiento_carga_aprobar,
        name="seguimiento_carga_aprobar",
    ),
    path(
        "seguimiento/empresas/<int:pk>/cargas/<int:carga_id>/rechazar/",
        views.seguimiento_carga_rechazar,
        name="seguimiento_carga_rechazar",
    ),
    path(
        "seguimiento/empresas/<int:pk>/documentos/<int:doc_id>/asociar-criterios/",
        views.seguimiento_documento_asociar_criterios,
        name="seguimiento_documento_asociar_criterios",
    ),
]
