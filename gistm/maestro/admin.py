from django.contrib import admin

from .models import Empresa, IntentoAcceso, PerfilUsuario, RegistroLoginExterno


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "ruc", "activo", "creado_en")
    list_filter = ("activo",)
    search_fields = ("nombre", "ruc")


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = ("user", "tipo", "activo", "empresa", "creado_en")
    list_filter = ("tipo", "activo")
    search_fields = ("user__email", "user__username", "user__first_name", "user__last_name")
    filter_horizontal = ("empresas",)
    raw_id_fields = ("user", "empresa")


@admin.register(IntentoAcceso)
class IntentoAccesoAdmin(admin.ModelAdmin):
    list_display = ("creado_en", "email", "motivo", "ip", "oid")
    list_filter = ("motivo",)
    search_fields = ("email", "oid", "ip", "detalle")
    readonly_fields = ("email", "oid", "motivo", "detalle", "ip", "user_agent", "creado_en")


@admin.register(RegistroLoginExterno)
class RegistroLoginExternoAdmin(admin.ModelAdmin):
    list_display = ("creado_en", "email", "nombre", "empresa_nombre", "ip")
    list_filter = ("empresa",)
    search_fields = ("email", "nombre", "empresa_nombre", "oid", "ip")
    readonly_fields = (
        "user",
        "email",
        "nombre",
        "empresa",
        "empresa_nombre",
        "oid",
        "ip",
        "user_agent",
        "creado_en",
    )
