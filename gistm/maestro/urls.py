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
    path("accesos-denegados/", views.acceso_denegado_list, name="acceso_denegado_list"),
    path("logins-empresas/", views.login_externo_list, name="login_externo_list"),
]
