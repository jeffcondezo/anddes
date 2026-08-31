from django.contrib import admin

from .models import (
    Area,
    Criterio,
    Documento,
    DocumentoCriterio,
    Empresa,
    EmpresaDocumento,
    EmpresaDocumentoCriterio,
    IntentoAcceso,
    PerfilUsuario,
    Principio,
    RegistroLoginExterno,
    Revision,
    Tema,
)


class AreaInline(admin.TabularInline):
    model = Area
    extra = 1
    fields = ("nombre", "activo")


class EmpresaDocumentoInline(admin.TabularInline):
    model = EmpresaDocumento
    extra = 0
    fields = ("codigo", "nombre", "tipo_documento", "origen", "activo")
    raw_id_fields = ("documento",)


class EmpresaDocumentoCriterioInline(admin.TabularInline):
    model = EmpresaDocumentoCriterio
    extra = 0
    fields = ("criterio", "area")
    raw_id_fields = ("criterio", "area")


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "ruc", "activo", "creado_en")
    list_filter = ("activo",)
    search_fields = ("nombre", "ruc")
    inlines = [AreaInline]


@admin.register(Revision)
class RevisionAdmin(admin.ModelAdmin):
    list_display = ("empresa", "anio", "nombre", "activo", "creado_en")
    list_filter = ("activo", "anio", "empresa")
    search_fields = ("nombre", "empresa__nombre")
    raw_id_fields = ("empresa",)
    inlines = [EmpresaDocumentoInline]


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "empresa", "activo", "creado_en")
    list_filter = ("activo", "empresa")
    search_fields = ("nombre", "empresa__nombre")
    raw_id_fields = ("empresa",)


@admin.register(EmpresaDocumento)
class EmpresaDocumentoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "revision", "origen", "tipo_documento", "activo")
    list_filter = ("origen", "activo", "revision__empresa", "tipo_documento")
    search_fields = ("codigo", "nombre", "revision__empresa__nombre")
    raw_id_fields = ("revision", "documento")
    inlines = [EmpresaDocumentoCriterioInline]


@admin.register(EmpresaDocumentoCriterio)
class EmpresaDocumentoCriterioAdmin(admin.ModelAdmin):
    list_display = ("empresa_documento", "criterio", "area", "creado_en")
    list_filter = ("empresa_documento__revision__empresa", "area")
    search_fields = (
        "empresa_documento__codigo",
        "criterio__codigo",
        "area__nombre",
        "empresa_documento__revision__empresa__nombre",
    )
    raw_id_fields = ("empresa_documento", "criterio", "area")


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


@admin.register(Tema)
class TemaAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "creado_en")
    search_fields = ("codigo", "nombre")


@admin.register(Principio)
class PrincipioAdmin(admin.ModelAdmin):
    list_display = ("codigo", "tema", "suma_ponderacion")
    list_filter = ("tema",)
    search_fields = ("codigo", "descripcion")
    readonly_fields = ("suma_ponderacion",)


@admin.register(Criterio)
class CriterioAdmin(admin.ModelAdmin):
    list_display = ("codigo", "principio", "tema", "ponderacion")
    list_filter = ("tema", "principio")
    search_fields = ("codigo", "descripcion")
    readonly_fields = ("tema",)


@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "tipo_documento", "creado_en")
    list_filter = ("tipo_documento",)
    search_fields = ("codigo", "nombre", "tipo_documento")


@admin.register(DocumentoCriterio)
class DocumentoCriterioAdmin(admin.ModelAdmin):
    list_display = ("documento", "criterio", "creado_en")
    list_filter = ("documento", "criterio__tema", "criterio__principio")
    search_fields = ("documento__codigo", "documento__nombre", "criterio__codigo")
    raw_id_fields = ("documento", "criterio")
