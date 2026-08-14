from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .models import Empresa, PerfilUsuario, TipoUsuario

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
