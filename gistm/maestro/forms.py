from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q

from .models import (
    Area,
    Criterio,
    Documento,
    DocumentoCarga,
    DocumentoCriterio,
    Empresa,
    EmpresaDocumento,
    EmpresaDocumentoCriterio,
    OrigenEmpresaDocumento,
    PerfilUsuario,
    Principio,
    Requisito,
    Revision,
    RevisionCriterioDesactivado,
    Tema,
    TipoUsuario,
)

EXTENSIONES_CARGA_PERMITIDAS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
}

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


class RequisitoForm(forms.ModelForm):
    class Meta:
        model = Requisito
        fields = ["codigo", "principio", "descripcion"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "principio": forms.Select(attrs={"class": "input", "id": "id_principio"}),
            "descripcion": forms.Textarea(attrs={"class": "input", "rows": 4}),
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
        elif self.instance.pk:
            self.tema_nombre = str(self.instance.principio.tema)


class CriterioForm(forms.ModelForm):
    class Meta:
        model = Criterio
        fields = ["codigo", "requisito", "descripcion"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "requisito": forms.Select(attrs={"class": "input", "id": "id_requisito"}),
            "descripcion": forms.Textarea(attrs={"class": "input", "rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["requisito"].queryset = (
            Requisito.objects.select_related("principio", "principio__tema")
            .annotate(total_criterios=Count("criterios"))
            .all()
        )
        self.principio_nombre = ""
        self.tema_nombre = ""
        self.ponderacion_info = ""
        self.instance_requisito_id = (
            self.instance.requisito_id if self.instance.pk else None
        )
        requisito = None
        if self.is_bound:
            requisito_id = self.data.get("requisito")
            if requisito_id:
                requisito = (
                    Requisito.objects.select_related("principio", "principio__tema")
                    .annotate(total_criterios=Count("criterios"))
                    .filter(pk=requisito_id)
                    .first()
                )
        elif self.instance.pk and self.instance.requisito_id:
            requisito = (
                Requisito.objects.select_related("principio", "principio__tema")
                .annotate(total_criterios=Count("criterios"))
                .filter(pk=self.instance.requisito_id)
                .first()
            )
            self.ponderacion_info = str(self.instance.ponderacion)
        if requisito:
            self.principio_nombre = str(requisito.principio)
            self.tema_nombre = str(requisito.principio.tema)
            if not self.ponderacion_info:
                n = requisito.total_criterios
                if not self.instance.pk or self.instance.requisito_id != requisito.pk:
                    n += 1
                if n > 0:
                    self.ponderacion_info = str(
                        (Decimal("1") / Decimal(n)).quantize(Decimal("0.0001"))
                    )

    def save(self, commit=True):
        criterio = super().save(commit=False)
        criterio.principio = criterio.requisito.principio
        criterio.tema = criterio.requisito.principio.tema
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
            "requisito", "principio", "tema"
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
        revision = empresa_documento.revision
        ya_en_revision = EmpresaDocumentoCriterio.objects.filter(
            revision=revision
        ).values_list("criterio_id", flat=True)
        desactivados = RevisionCriterioDesactivado.objects.filter(
            revision=revision
        ).values_list("criterio_id", flat=True)
        self.fields["criterios"].queryset = (
            Criterio.objects.select_related("requisito", "principio", "tema")
            .exclude(pk__in=ya_en_revision)
            .exclude(pk__in=desactivados)
            .order_by("codigo")
        )
        self.fields["area"].queryset = Area.objects.filter(empresa=empresa, activo=True)
        self.fields["criterios"].help_text = (
            "Solo se listan criterios aún no asociados a ningún documento de esta revisión."
        )

    def clean_criterios(self):
        criterios = self.cleaned_data["criterios"]
        revision = self.empresa_documento.revision
        ya = set(
            EmpresaDocumentoCriterio.objects.filter(
                revision=revision,
                criterio_id__in=[c.pk for c in criterios],
            ).values_list("criterio_id", flat=True)
        )
        if ya:
            codigos = list(
                Criterio.objects.filter(pk__in=ya).values_list("codigo", flat=True)
            )
            raise ValidationError(
                "Estos criterios ya están asociados a otro documento de la revisión: "
                + ", ".join(codigos)
            )
        return criterios

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
                revision=self.empresa_documento.revision,
                criterio=criterio,
                defaults={
                    "empresa_documento": self.empresa_documento,
                    "area": area,
                },
            )
            if not created:
                # Ya existía en otro documento de la revisión: no se mueve aquí.
                continue
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


def _validar_extension_archivo(archivo) -> None:
    if not archivo:
        return
    ext = Path(archivo.name).suffix.lower()
    if ext not in EXTENSIONES_CARGA_PERMITIDAS:
        raise ValidationError(
            "Formato no permitido. Usa PDF, DOC/DOCX, XLS/XLSX o imagen (PNG/JPG/GIF/WEBP)."
        )


class DocumentoCargaForm(forms.ModelForm):
    class Meta:
        model = DocumentoCarga
        fields = ["archivo", "observaciones"]
        widgets = {
            "archivo": forms.ClearableFileInput(attrs={"class": "input"}),
            "observaciones": forms.Textarea(
                attrs={"class": "input", "rows": 3, "placeholder": "Opcional"}
            ),
        }

    def clean_archivo(self):
        archivo = self.cleaned_data.get("archivo")
        _validar_extension_archivo(archivo)
        return archivo

    def save(self, commit=True):
        from .models import EstadoRevisionCarga

        obj = super().save(commit=False)
        obj.estado_revision = EstadoRevisionCarga.PENDIENTE
        obj.motivo_rechazo = ""
        obj.revisado_por = None
        obj.revisado_en = None
        obj.activo = True
        if commit:
            obj.save()
        return obj


class RechazoDocumentoCargaForm(forms.Form):
    motivo_rechazo = forms.CharField(
        label="Motivo del rechazo",
        widget=forms.Textarea(
            attrs={
                "class": "input",
                "rows": 4,
                "placeholder": "Explica a la minera por qué se rechaza esta evidencia.",
            }
        ),
        min_length=10,
        help_text="Este mensaje será visible para el cliente de la empresa minera.",
    )


class DocumentoMensajeForm(forms.Form):
    texto = forms.CharField(
        label="Mensaje",
        widget=forms.Textarea(
            attrs={
                "class": "input",
                "rows": 3,
                "placeholder": "Escribe una observación, acuerdo de fecha u otra nota…",
            }
        ),
        min_length=2,
    )
    tipo = forms.ChoiceField(
        label="Tipo",
        choices=[
            ("OBSERVACION", "Observación"),
            ("ACUERDO", "Acuerdo / fecha"),
            ("RESPUESTA", "Respuesta"),
        ],
        initial="OBSERVACION",
        widget=forms.Select(attrs={"class": "input"}),
        required=False,
    )


class ClienteDocumentoPropioCargaForm(forms.ModelForm):
    """Crear documento propio de la revisión + primera evidencia."""

    archivo = forms.FileField(
        label="Archivo",
        widget=forms.ClearableFileInput(attrs={"class": "input"}),
    )
    observaciones = forms.CharField(
        label="Observaciones",
        required=False,
        widget=forms.Textarea(
            attrs={"class": "input", "rows": 3, "placeholder": "Opcional"}
        ),
    )

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
        if qs.exists():
            raise ValidationError("Ya existe un documento con este código en la revisión.")
        return codigo

    def clean_archivo(self):
        archivo = self.cleaned_data.get("archivo")
        _validar_extension_archivo(archivo)
        return archivo

    def save(self, user, commit=True):
        obj = super().save(commit=False)
        obj.revision = self.revision
        obj.documento = None
        obj.origen = OrigenEmpresaDocumento.PROPIO
        obj.activo = True
        if commit:
            obj.save()
            from .models import EstadoRevisionCarga

            DocumentoCarga.objects.create(
                empresa_documento=obj,
                archivo=self.cleaned_data["archivo"],
                observaciones=(self.cleaned_data.get("observaciones") or "").strip(),
                subido_por=user,
                activo=True,
                estado_revision=EstadoRevisionCarga.PENDIENTE,
            )
        return obj
