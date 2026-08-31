from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .models import (
    Area,
    Criterio,
    Documento,
    DocumentoCriterio,
    Empresa,
    EmpresaDocumento,
    EmpresaDocumentoCriterio,
    OrigenEmpresaDocumento,
    PerfilUsuario,
    Principio,
    Revision,
    Tema,
    TipoUsuario,
)

User = get_user_model()


class EmpresaForm(forms.ModelForm):
    class Meta:
        model = Empresa
        fields = ["nombre", "ruc", "activo"]
        widgets = {
            "nombre": forms.TextInput(attrs={"class": "input"}),
            "ruc": forms.TextInput(attrs={"class": "input"}),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }


class AreaForm(forms.ModelForm):
    class Meta:
        model = Area
        fields = ["nombre", "activo"]
        widgets = {
            "nombre": forms.TextInput(attrs={"class": "input", "placeholder": "Nombre del área"}),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["nombre"].required = False

    def clean(self):
        cleaned = super().clean()
        nombre = (cleaned.get("nombre") or "").strip()
        cleaned["nombre"] = nombre
        if cleaned.get("DELETE"):
            return cleaned
        if not nombre and not self.instance.pk:
            return cleaned
        if not nombre:
            self.add_error("nombre", "Indica el nombre del área.")
        return cleaned

    def has_changed(self) -> bool:
        nombre_key = self.add_prefix("nombre")
        nombre = ""
        if self.data is not None:
            nombre = (self.data.get(nombre_key) or "").strip()
        if not self.instance.pk and not nombre:
            return False
        return super().has_changed()


AreaFormSet = forms.inlineformset_factory(
    Empresa,
    Area,
    form=AreaForm,
    extra=1,
    can_delete=True,
    min_num=0,
    validate_min=False,
    max_num=50,
    validate_max=True,
)


class UsuarioForm(forms.Form):
    email = forms.EmailField(
        label="Correo",
        widget=forms.EmailInput(attrs={"class": "input", "autocomplete": "email"}),
    )
    first_name = forms.CharField(
        label="Nombres",
        required=False,
        max_length=150,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    last_name = forms.CharField(
        label="Apellidos",
        required=False,
        max_length=150,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    tipo = forms.ChoiceField(
        label="Tipo de usuario",
        choices=TipoUsuario.choices,
        widget=forms.Select(attrs={"class": "input", "id": "id_tipo"}),
    )
    activo = forms.BooleanField(
        label="Activo",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "checkbox"}),
    )
    empresa = forms.ModelChoiceField(
        label="Empresa (cliente)",
        queryset=Empresa.objects.none(),
        required=False,
        empty_label="Seleccione una empresa",
        widget=forms.Select(attrs={"class": "input", "id": "id_empresa"}),
    )
    empresas = forms.ModelMultipleChoiceField(
        label="Empresas (gestor)",
        queryset=Empresa.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "input", "id": "id_empresas", "size": "6"}),
    )

    def __init__(self, *args, instance: PerfilUsuario | None = None, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)

        empresa_filter = Q(activo=True)
        if instance and instance.empresa_id:
            empresa_filter |= Q(pk=instance.empresa_id)
        self.fields["empresa"].queryset = Empresa.objects.filter(empresa_filter).distinct()

        empresas_qs = Empresa.objects.filter(activo=True)
        if instance:
            empresas_qs = (empresas_qs | instance.empresas.all()).distinct()
        self.fields["empresas"].queryset = empresas_qs

        if instance:
            user = instance.user
            self.fields["email"].initial = user.email or user.username
            self.fields["first_name"].initial = user.first_name
            self.fields["last_name"].initial = user.last_name
            self.fields["tipo"].initial = instance.tipo
            self.fields["activo"].initial = instance.activo
            self.fields["empresa"].initial = instance.empresa_id
            self.fields["empresas"].initial = list(instance.empresas.values_list("pk", flat=True))
            self.fields["email"].widget.attrs["readonly"] = True
            self.fields["email"].help_text = "El correo no se puede modificar (identidad Entra ID)."

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if self.instance:
            current = (self.instance.user.email or self.instance.user.username).strip().lower()
            if email != current:
                raise ValidationError("El correo no se puede modificar.")
            return current
        exists = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists()
        if exists:
            raise ValidationError("Ya existe un usuario registrado con este correo.")
        return email

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get("tipo")
        empresa = cleaned.get("empresa")
        empresas = cleaned.get("empresas")

        if tipo == TipoUsuario.CLIENTE and not empresa:
            self.add_error("empresa", "El usuario cliente debe estar asociado a una empresa minera.")
        if tipo == TipoUsuario.GESTOR and (not empresas or len(empresas) < 1):
            self.add_error("empresas", "El gestor debe supervisar al menos una empresa.")
        if tipo == TipoUsuario.ADMINISTRADOR:
            cleaned["empresa"] = None
            cleaned["empresas"] = []
        if tipo == TipoUsuario.CLIENTE:
            cleaned["empresas"] = []
        if tipo == TipoUsuario.GESTOR:
            cleaned["empresa"] = None
        return cleaned

    @transaction.atomic
    def save(self) -> PerfilUsuario:
        email = self.cleaned_data["email"].strip().lower()
        first_name = self.cleaned_data.get("first_name", "").strip()
        last_name = self.cleaned_data.get("last_name", "").strip()
        tipo = self.cleaned_data["tipo"]
        activo = bool(self.cleaned_data.get("activo"))
        empresa = self.cleaned_data.get("empresa")
        empresas = self.cleaned_data.get("empresas") or []

        if self.instance:
            user = self.instance.user
            user.first_name = first_name
            user.last_name = last_name
            user.is_active = activo
            user.save(update_fields=["first_name", "last_name", "is_active"])
            perfil = self.instance
            perfil.tipo = tipo
            perfil.activo = activo
            perfil.empresa = empresa if tipo == TipoUsuario.CLIENTE else None
            perfil.save()
        else:
            user = User(username=email, email=email, first_name=first_name, last_name=last_name, is_active=activo)
            user.set_unusable_password()
            user.save()
            perfil = PerfilUsuario.objects.create(
                user=user,
                tipo=tipo,
                activo=activo,
                empresa=empresa if tipo == TipoUsuario.CLIENTE else None,
            )

        if tipo == TipoUsuario.GESTOR:
            perfil.empresas.set(empresas)
        else:
            perfil.empresas.clear()

        return perfil


class TemaForm(forms.ModelForm):
    class Meta:
        model = Tema
        fields = ["codigo", "nombre"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "nombre": forms.TextInput(attrs={"class": "input"}),
        }


class PrincipioForm(forms.ModelForm):
    class Meta:
        model = Principio
        fields = ["codigo", "tema", "descripcion"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "tema": forms.Select(attrs={"class": "input"}),
            "descripcion": forms.Textarea(attrs={"class": "input", "rows": 4}),
        }


class CriterioForm(forms.ModelForm):
    class Meta:
        model = Criterio
        fields = ["codigo", "principio", "descripcion", "ponderacion"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "principio": forms.Select(attrs={"class": "input", "id": "id_principio"}),
            "descripcion": forms.Textarea(attrs={"class": "input", "rows": 4}),
            "ponderacion": forms.NumberInput(attrs={"class": "input", "step": "0.0001"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["principio"].queryset = Principio.objects.select_related("tema").all()
        self.tema_nombre = ""
        if self.is_bound:
            principio_id = self.data.get("principio")
            if principio_id:
                principio = Principio.objects.select_related("tema").filter(pk=principio_id).first()
                if principio:
                    self.tema_nombre = str(principio.tema)
        elif self.instance.pk and self.instance.tema_id:
            self.tema_nombre = str(self.instance.tema)

    def save(self, commit=True):
        criterio = super().save(commit=False)
        criterio.tema = criterio.principio.tema
        if commit:
            criterio.save()
        return criterio


class DocumentoForm(forms.ModelForm):
    class Meta:
        model = Documento
        fields = ["codigo", "nombre", "tipo_documento"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "nombre": forms.TextInput(attrs={"class": "input"}),
            "tipo_documento": forms.TextInput(attrs={"class": "input"}),
        }


class DocumentoCriterioForm(forms.ModelForm):
    class Meta:
        model = DocumentoCriterio
        fields = ["documento", "criterio"]
        widgets = {
            "documento": forms.Select(attrs={"class": "input"}),
            "criterio": forms.Select(attrs={"class": "input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["documento"].queryset = Documento.objects.order_by("codigo")
        self.fields["criterio"].queryset = Criterio.objects.select_related(
            "principio", "tema"
        ).order_by("codigo")

    def clean(self):
        cleaned = super().clean()
        documento = cleaned.get("documento")
        criterio = cleaned.get("criterio")
        if documento and criterio:
            qs = DocumentoCriterio.objects.filter(documento=documento, criterio=criterio)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError("Esta relación documento–criterio ya existe en el estándar.")
        return cleaned


class AplicarEstandarRevisionForm(forms.Form):
    area = forms.ModelChoiceField(
        label="Área responsable por defecto",
        queryset=Area.objects.none(),
        empty_label="Seleccione un área",
        widget=forms.Select(attrs={"class": "input"}),
        required=True,
        help_text="Se asignará a todos los vínculos nuevos creados desde el estándar.",
    )

    def __init__(self, *args, revision: Revision, **kwargs):
        self.revision = revision
        super().__init__(*args, **kwargs)
        self.fields["area"].queryset = Area.objects.filter(
            empresa=revision.empresa, activo=True
        )

    def clean_area(self):
        area = self.cleaned_data["area"]
        if area.empresa_id != self.revision.empresa_id:
            raise ValidationError("El área debe pertenecer a la empresa de esta revisión.")
        return area


class RevisionForm(forms.ModelForm):
    class Meta:
        model = Revision
        fields = ["anio", "nombre", "activo"]
        widgets = {
            "anio": forms.NumberInput(attrs={"class": "input", "min": "2000", "max": "2100"}),
            "nombre": forms.TextInput(attrs={"class": "input", "placeholder": "Opcional"}),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, empresa: Empresa, **kwargs):
        self.empresa = empresa
        super().__init__(*args, **kwargs)

    def clean_anio(self):
        anio = self.cleaned_data["anio"]
        qs = Revision.objects.filter(empresa=self.empresa, anio=anio)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("Ya existe una revisión para este año en la empresa.")
        return anio

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.empresa = self.empresa
        if commit:
            obj.save()
        return obj


class ActivarDocumentosCatalogoForm(forms.Form):
    documentos = forms.ModelMultipleChoiceField(
        label="Documentos del catálogo",
        queryset=Documento.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
    )

    def __init__(self, *args, revision: Revision, **kwargs):
        super().__init__(*args, **kwargs)
        ya_asociados = revision.documentos.filter(documento__isnull=False).values_list(
            "documento_id", flat=True
        )
        self.fields["documentos"].queryset = Documento.objects.exclude(pk__in=ya_asociados)


class EmpresaDocumentoPropioForm(forms.ModelForm):
    class Meta:
        model = EmpresaDocumento
        fields = ["codigo", "nombre", "tipo_documento"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "nombre": forms.TextInput(attrs={"class": "input"}),
            "tipo_documento": forms.TextInput(attrs={"class": "input"}),
        }

    def __init__(self, *args, revision: Revision, **kwargs):
        self.revision = revision
        super().__init__(*args, **kwargs)

    def clean_codigo(self):
        codigo = (self.cleaned_data.get("codigo") or "").strip()
        qs = EmpresaDocumento.objects.filter(revision=self.revision, codigo__iexact=codigo)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("Ya existe un documento con este código en la revisión.")
        return codigo

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.revision = self.revision
        obj.documento = None
        obj.origen = OrigenEmpresaDocumento.PROPIO
        obj.activo = True
        if commit:
            obj.save()
        return obj


class EmpresaDocumentoCriterioForm(forms.Form):
    criterios = forms.ModelMultipleChoiceField(
        label="Criterios",
        queryset=Criterio.objects.none(),
        widget=forms.SelectMultiple(attrs={"class": "input", "size": "12"}),
        required=True,
    )
    area = forms.ModelChoiceField(
        label="Área responsable",
        queryset=Area.objects.none(),
        empty_label="Seleccione un área",
        widget=forms.Select(attrs={"class": "input"}),
        required=True,
    )

    def __init__(self, *args, empresa_documento: EmpresaDocumento, **kwargs):
        self.empresa_documento = empresa_documento
        super().__init__(*args, **kwargs)
        empresa = empresa_documento.revision.empresa
        ya_vinculados = empresa_documento.vinculos_criterio.values_list("criterio_id", flat=True)
        self.fields["criterios"].queryset = (
            Criterio.objects.select_related("principio", "tema")
            .exclude(pk__in=ya_vinculados)
            .order_by("codigo")
        )
        self.fields["area"].queryset = Area.objects.filter(empresa=empresa, activo=True)

    def clean_area(self):
        area = self.cleaned_data["area"]
        if area.empresa_id != self.empresa_documento.revision.empresa_id:
            raise ValidationError("El área debe pertenecer a la misma empresa del documento.")
        return area

    def save(self):
        area = self.cleaned_data["area"]
        creados = []
        for criterio in self.cleaned_data["criterios"]:
            vinculo, created = EmpresaDocumentoCriterio.objects.get_or_create(
                empresa_documento=self.empresa_documento,
                criterio=criterio,
                defaults={"area": area},
            )
            if not created and vinculo.area_id != area.pk:
                vinculo.area = area
                vinculo.save(update_fields=["area", "actualizado_en"])
            creados.append(vinculo)
        return creados


class EmpresaDocumentoCriterioAreaForm(forms.ModelForm):
    class Meta:
        model = EmpresaDocumentoCriterio
        fields = ["area"]
        widgets = {
            "area": forms.Select(attrs={"class": "input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        empresa_id = self.instance.empresa_documento.revision.empresa_id
        self.fields["area"].queryset = Area.objects.filter(empresa_id=empresa_id, activo=True)

    def clean_area(self):
        area = self.cleaned_data["area"]
        if area.empresa_id != self.instance.empresa_documento.revision.empresa_id:
            raise ValidationError("El área debe pertenecer a la misma empresa del documento.")
        return area
