from __future__ import annotations

from datetime import timedelta
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
    EmpresaDocumentoPregunta,
    EmpresaNotificacionRegla,
    EstadoInstalacionRevision,
    FaseInstalacionRevision,
    EstadoRevisionCriterio,
    EstadoRevisionPregunta,
    GlosarioTermino,
    OrigenEmpresaDocumento,
    PerfilUsuario,
    Pregunta,
    Principio,
    Requisito,
    Responsable,
    Revision,
    RevisionCriterioDesactivado,
    RolDocumentoCriterio,
    Tema,
    TIPOS_CLIENTE,
    TipoNotificacionAcuerdo,
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


class RevisionSelectMultiple(forms.SelectMultiple):
    """Select múltiple con data-empresa para filtrar por empresa en el formulario de usuario."""

    def __init__(self, *args, empresa_by_rev: dict | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa_by_rev = empresa_by_rev or {}

    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        raw = getattr(value, "value", value)
        if raw not in (None, ""):
            emp = self.empresa_by_rev.get(str(raw))
            if emp is not None:
                option["attrs"]["data-empresa"] = str(emp)
        return option

User = get_user_model()


class EmpresaForm(forms.ModelForm):
    class Meta:
        model = Empresa
        fields = ["nombre", "ruc", "tamano_max_mb", "notificaciones_activas", "activo"]
        widgets = {
            "nombre": forms.TextInput(attrs={"class": "input"}),
            "ruc": forms.TextInput(
                attrs={
                    "class": "input",
                    "inputmode": "numeric",
                    "pattern": r"[0-9]{11}",
                    "maxlength": "11",
                    "minlength": "11",
                    "autocomplete": "off",
                    "placeholder": "11 dígitos",
                }
            ),
            "tamano_max_mb": forms.NumberInput(
                attrs={
                    "class": "input",
                    "min": "1",
                    "step": "1",
                    "placeholder": "Ej. 25",
                }
            ),
            "notificaciones_activas": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ruc"].required = True
        self.fields["ruc"].help_text = "Solo números, exactamente 11 dígitos."
        self.fields["tamano_max_mb"].label = "Tamaño máximo de archivo (MB)"
        self.fields["tamano_max_mb"].help_text = (
            "Límite de peso para evidencias que cargue esta empresa (mínimo 1 MB)."
        )
        self.fields["notificaciones_activas"].label = "Envío de notificaciones por correo"
        self.fields["notificaciones_activas"].help_text = (
            "Desactívalo si la empresa ya no trabaja con Anddes: no se enviarán "
            "recordatorios ni avisos por correo."
        )

    def clean_ruc(self):
        ruc = (self.cleaned_data.get("ruc") or "").strip()
        if not ruc.isdigit() or len(ruc) != 11:
            raise forms.ValidationError(
                "El RUC debe ser numérico y tener exactamente 11 dígitos."
            )
        return ruc

    def clean_tamano_max_mb(self):
        valor = self.cleaned_data.get("tamano_max_mb")
        if valor is None or valor < 1:
            raise forms.ValidationError("Indica un tamaño máximo de al menos 1 MB.")
        return valor


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


class AreaCreateForm(forms.ModelForm):
    class Meta:
        model = Area
        fields = ["nombre", "activo"]
        widgets = {
            "nombre": forms.TextInput(
                attrs={"class": "input", "placeholder": "Ej. Sustentabilidad, Operaciones"}
            ),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.fields["activo"].initial = True

    def clean_nombre(self):
        nombre = (self.cleaned_data.get("nombre") or "").strip()
        if not nombre:
            raise forms.ValidationError("Indica el nombre del área.")
        qs = Area.objects.filter(empresa=self.empresa, nombre__iexact=nombre)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if self.empresa and qs.exists():
            raise forms.ValidationError("Ya existe un área con ese nombre en la empresa.")
        return nombre


class EmpresaNotificacionReglaForm(forms.Form):
    """
    Alta de uno o varios días (separados por coma) para un tipo de notificación.
    El día 0 lo reserva el sistema (aviso el día del vencimiento).
    """

    tipo = forms.ChoiceField(
        choices=TipoNotificacionAcuerdo.choices,
        widget=forms.Select(attrs={"class": "input"}),
    )
    dias = forms.CharField(
        label="Días",
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "placeholder": "Ej. 2, 4, 5",
                "autocomplete": "off",
            }
        ),
        help_text=(
            "Números separados por coma. Antes = días previos al vencimiento; "
            "post = días posteriores. El día 0 ya lo cubre el sistema."
        ),
    )
    activo = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "checkbox"}),
    )

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.dias_parseados: list[int] = []

    def clean_dias(self) -> list[int]:
        from .models import NOTIFICACION_ACUERDO_DIA_SISTEMA

        raw = (self.cleaned_data.get("dias") or "").strip()
        if not raw:
            raise ValidationError("Indica al menos un día (ej. 2, 5, 7).")
        partes = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
        if not partes:
            raise ValidationError("Indica al menos un día (ej. 2, 5, 7).")
        valores: list[int] = []
        vistos: set[int] = set()
        errores: list[str] = []
        for parte in partes:
            if not parte.isdigit():
                errores.append(f'"{parte}" no es un número válido.')
                continue
            n = int(parte)
            if n == NOTIFICACION_ACUERDO_DIA_SISTEMA:
                errores.append(
                    "El día 0 (mismo día del vencimiento) ya lo cubre la "
                    "configuración base del sistema; no se puede agregar."
                )
                continue
            if n < 1:
                errores.append(f"El día {n} no es válido; usa enteros mayores a 0.")
                continue
            if n in vistos:
                continue
            vistos.add(n)
            valores.append(n)
        if errores:
            raise ValidationError(errores)
        if not valores:
            raise ValidationError("No quedó ningún día válido para agregar.")
        return sorted(valores)

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get("tipo")
        dias_lista = cleaned.get("dias") or []
        if not self.empresa or not tipo or not dias_lista:
            return cleaned
        existentes = set(
            EmpresaNotificacionRegla.objects.filter(
                empresa=self.empresa, tipo=tipo, dias__in=dias_lista
            ).values_list("dias", flat=True)
        )
        if existentes:
            lista = ", ".join(str(d) for d in sorted(existentes))
            self.add_error(
                "dias",
                f"Ya existen reglas de este tipo para el/los día(s): {lista}.",
            )
        self.dias_parseados = list(dias_lista)
        return cleaned

    def save(self) -> list[EmpresaNotificacionRegla]:
        """Crea una regla por cada día indicado. Devuelve las creadas."""
        if not self.is_valid():
            raise ValueError("El formulario no es válido.")
        if self.empresa is None:
            raise ValueError("Se requiere empresa.")
        creadas: list[EmpresaNotificacionRegla] = []
        activo = bool(self.cleaned_data.get("activo"))
        tipo = self.cleaned_data["tipo"]
        for dias in self.cleaned_data["dias"]:
            obj = EmpresaNotificacionRegla.objects.create(
                empresa=self.empresa,
                tipo=tipo,
                dias=dias,
                activo=activo,
            )
            creadas.append(obj)
        return creadas


class ResponsableForm(forms.ModelForm):
    class Meta:
        from .models import Responsable

        model = Responsable
        fields = ["nombre", "dni", "telefono", "correo", "activo"]
        widgets = {
            "nombre": forms.TextInput(attrs={"class": "input", "placeholder": "Nombre completo"}),
            "dni": forms.TextInput(
                attrs={
                    "class": "input",
                    "placeholder": "8 dígitos",
                    "inputmode": "numeric",
                    "pattern": "[0-9]{8}",
                    "maxlength": "8",
                    "minlength": "8",
                }
            ),
            "telefono": forms.TextInput(attrs={"class": "input", "placeholder": "Número de contacto"}),
            "correo": forms.EmailInput(attrs={"class": "input", "placeholder": "correo@empresa.com"}),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, area=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.area = area
        self.fields["activo"].initial = True
        self.fields["dni"].required = True

    def clean_nombre(self):
        nombre = (self.cleaned_data.get("nombre") or "").strip()
        if not nombre:
            raise forms.ValidationError("Indica el nombre del responsable.")
        return nombre

    def clean_dni(self):
        dni = (self.cleaned_data.get("dni") or "").strip()
        if not dni.isdigit() or len(dni) != 8:
            raise forms.ValidationError("El DNI debe tener exactamente 8 dígitos numéricos.")
        return dni

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.area is not None:
            obj.area = self.area
        if commit:
            obj.save()
        return obj


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
    revisiones = forms.ModelMultipleChoiceField(
        label="Revisiones permitidas",
        queryset=Revision.objects.none(),
        required=False,
        widget=RevisionSelectMultiple(
            attrs={"class": "input", "id": "id_revisiones", "size": "8"}
        ),
        help_text=(
            "El cliente solo verá estas revisiones de la empresa. "
            "Selecciona al menos una."
        ),
    )
    empresas = forms.ModelMultipleChoiceField(
        label="Empresas (gestor)",
        queryset=Empresa.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "input", "id": "id_empresas", "size": "6"}),
    )
    reporta_a = forms.ModelChoiceField(
        label="Reporta a (jefe)",
        queryset=User.objects.none(),
        required=False,
        empty_label="— Sin jefe —",
        widget=forms.Select(attrs={"class": "input", "id": "id_reporta_a"}),
        help_text="Solo para gestores: coordinador o administrador al que reporta.",
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

        # Todas las revisiones (el JS filtra por empresa); validación en clean.
        revs_qs = Revision.objects.select_related("empresa").order_by(
            "empresa__nombre", "-anio", "-creado_en"
        )
        self.fields["revisiones"].queryset = revs_qs
        self.fields["revisiones"].label_from_instance = (
            lambda r: f"{r.empresa.nombre} · {r.etiqueta}"
            + ("" if r.activo else " (inactiva)")
        )
        self.fields["revisiones"].widget.empresa_by_rev = {
            str(r.pk): r.empresa_id for r in revs_qs
        }

        jefes_qs = User.objects.filter(
            is_active=True,
            perfil__activo=True,
            perfil__tipo__in=[TipoUsuario.GESTOR, TipoUsuario.ADMINISTRADOR],
        ).select_related("perfil").order_by("first_name", "last_name", "email")
        if instance:
            jefes_qs = jefes_qs.exclude(pk=instance.user_id)
        self.fields["reporta_a"].queryset = jefes_qs
        self.fields["reporta_a"].label_from_instance = (
            lambda u: f"{(u.get_full_name() or '').strip() or u.email} ({u.perfil.get_tipo_display()})"
        )

        if instance:
            user = instance.user
            self.fields["email"].initial = user.email or user.username
            self.fields["first_name"].initial = user.first_name
            self.fields["last_name"].initial = user.last_name
            self.fields["tipo"].initial = instance.tipo
            self.fields["activo"].initial = instance.activo
            self.fields["empresa"].initial = instance.empresa_id
            self.fields["empresas"].initial = list(instance.empresas.values_list("pk", flat=True))
            self.fields["revisiones"].initial = list(
                instance.revisiones.values_list("pk", flat=True)
            )
            self.fields["reporta_a"].initial = instance.reporta_a_id
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
        revisiones = cleaned.get("revisiones")
        reporta_a = cleaned.get("reporta_a")
        es_cliente = tipo in TIPOS_CLIENTE

        if es_cliente and not empresa:
            self.add_error("empresa", "El usuario cliente debe estar asociado a una empresa minera.")
        if es_cliente and empresa:
            if not revisiones:
                self.add_error(
                    "revisiones",
                    "Selecciona al menos una revisión a la que el cliente pueda acceder.",
                )
            else:
                ajenas = [r for r in revisiones if r.empresa_id != empresa.pk]
                if ajenas:
                    self.add_error(
                        "revisiones",
                        "Todas las revisiones deben pertenecer a la empresa del cliente.",
                    )
        if tipo == TipoUsuario.GESTOR and (not empresas or len(empresas) < 1):
            self.add_error("empresas", "El gestor debe supervisar al menos una empresa.")
        if tipo == TipoUsuario.ADMINISTRADOR:
            cleaned["empresa"] = None
            cleaned["empresas"] = []
            cleaned["revisiones"] = []
            cleaned["reporta_a"] = None
        if es_cliente:
            cleaned["empresas"] = []
            cleaned["reporta_a"] = None
        if tipo == TipoUsuario.GESTOR:
            cleaned["empresa"] = None
            cleaned["revisiones"] = []
            if reporta_a and self.instance and reporta_a.pk == self.instance.user_id:
                self.add_error("reporta_a", "Un usuario no puede reportarse a sí mismo.")
            elif reporta_a:
                # Ciclo: el jefe (o su cadena) no debe reportar a este usuario.
                if self.instance and self.instance.user_id:
                    visto = {self.instance.user_id}
                    actual = reporta_a
                    while actual is not None:
                        if actual.pk in visto:
                            self.add_error(
                                "reporta_a",
                                "Esa jerarquía formaría un ciclo.",
                            )
                            break
                        visto.add(actual.pk)
                        try:
                            actual = actual.perfil.reporta_a
                        except PerfilUsuario.DoesNotExist:
                            break
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
        revisiones = self.cleaned_data.get("revisiones") or []
        es_cliente = tipo in TIPOS_CLIENTE
        reporta_a = self.cleaned_data.get("reporta_a") if tipo == TipoUsuario.GESTOR else None

        if self.instance:
            user = self.instance.user
            user.first_name = first_name
            user.last_name = last_name
            user.is_active = activo
            user.save(update_fields=["first_name", "last_name", "is_active"])
            perfil = self.instance
            perfil.tipo = tipo
            perfil.activo = activo
            perfil.empresa = empresa if es_cliente else None
            perfil.reporta_a = reporta_a
            perfil.save()
        else:
            user = User(username=email, email=email, first_name=first_name, last_name=last_name, is_active=activo)
            user.set_unusable_password()
            user.save()
            perfil = PerfilUsuario.objects.create(
                user=user,
                tipo=tipo,
                activo=activo,
                empresa=empresa if es_cliente else None,
                reporta_a=reporta_a,
            )

        if tipo == TipoUsuario.GESTOR:
            perfil.empresas.set(empresas)
            perfil.revisiones.clear()
        elif es_cliente:
            perfil.empresas.clear()
            perfil.revisiones.set(revisiones)
            if perfil.reporta_a_id:
                perfil.reporta_a = None
                perfil.save(update_fields=["reporta_a", "actualizado_en"])
        else:
            perfil.empresas.clear()
            perfil.revisiones.clear()
            if perfil.reporta_a_id:
                perfil.reporta_a = None
                perfil.save(update_fields=["reporta_a", "actualizado_en"])

        return perfil


class TemaForm(forms.ModelForm):
    class Meta:
        model = Tema
        fields = ["codigo", "nombre", "activo"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "nombre": forms.TextInput(attrs={"class": "input"}),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }


class GlosarioTerminoForm(forms.ModelForm):
    class Meta:
        model = GlosarioTermino
        fields = ["concepto", "descripcion", "activo"]
        widgets = {
            "concepto": forms.TextInput(attrs={"class": "input"}),
            "descripcion": forms.Textarea(attrs={"class": "input", "rows": 5}),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }


class PrincipioForm(forms.ModelForm):
    class Meta:
        model = Principio
        fields = ["codigo", "tema", "descripcion", "activo"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "tema": forms.Select(attrs={"class": "input"}),
            "descripcion": forms.Textarea(attrs={"class": "input", "rows": 4}),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }


class RequisitoForm(forms.ModelForm):
    class Meta:
        model = Requisito
        fields = ["codigo", "principio", "descripcion", "activo"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "principio": forms.Select(attrs={"class": "input", "id": "id_principio"}),
            "descripcion": forms.Textarea(attrs={"class": "input", "rows": 4}),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
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
        fields = ["codigo", "requisito", "descripcion", "activo"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "requisito": forms.Select(attrs={"class": "input", "id": "id_requisito"}),
            "descripcion": forms.Textarea(attrs={"class": "input", "rows": 4}),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
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


class PreguntaForm(forms.ModelForm):
    class Meta:
        model = Pregunta
        fields = ["codigo", "criterio", "texto", "orden", "activo"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "criterio": forms.Select(attrs={"class": "input", "id": "id_criterio"}),
            "texto": forms.Textarea(attrs={"class": "input", "rows": 4}),
            "orden": forms.NumberInput(attrs={"class": "input", "min": "0"}),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["criterio"].queryset = Criterio.objects.select_related(
            "requisito", "principio", "tema"
        ).order_by("codigo")
        self.fields["orden"].help_text = "Menor número aparece primero."


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
        fields = ["criterio", "rol", "documento", "documento_secundario"]
        widgets = {
            "criterio": forms.Select(attrs={"class": "input"}),
            "rol": forms.Select(attrs={"class": "input"}),
            "documento": forms.Select(attrs={"class": "input"}),
            "documento_secundario": forms.Select(attrs={"class": "input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        docs = Documento.objects.order_by("codigo")
        self.fields["documento"].queryset = docs
        self.fields["documento"].label = "Documento principal"
        self.fields["documento_secundario"].queryset = docs
        self.fields["documento_secundario"].required = False
        self.fields["documento_secundario"].empty_label = "— Sin secundario —"
        self.fields["criterio"].queryset = Criterio.objects.select_related(
            "requisito", "principio", "tema"
        ).order_by("codigo")

    def clean(self):
        cleaned = super().clean()
        documento = cleaned.get("documento")
        documento_secundario = cleaned.get("documento_secundario")
        criterio = cleaned.get("criterio")
        if documento and documento_secundario and documento.pk == documento_secundario.pk:
            self.add_error(
                "documento_secundario",
                "El documento secundario debe ser distinto del principal.",
            )
        if criterio:
            qs = DocumentoCriterio.objects.filter(criterio=criterio)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(
                    "Este criterio ya tiene una relación estándar. "
                    "Edítala para cambiar documentos o rol."
                )
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
        fields = [
            "anio",
            "nombre",
            "instalacion_deposito",
            "estado",
            "fase",
            "capacidad",
            "vida_util",
            "fecha_inicio",
            "fecha_fin",
            "activo",
        ]
        widgets = {
            "anio": forms.NumberInput(attrs={"class": "input", "min": "2000", "max": "2100"}),
            "nombre": forms.TextInput(attrs={"class": "input", "placeholder": "Opcional"}),
            "instalacion_deposito": forms.TextInput(attrs={"class": "input"}),
            "estado": forms.Select(attrs={"class": "input"}),
            "fase": forms.Select(attrs={"class": "input"}),
            "capacidad": forms.TextInput(attrs={"class": "input"}),
            "vida_util": forms.TextInput(attrs={"class": "input"}),
            "fecha_inicio": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": "input", "type": "date"},
            ),
            "fecha_fin": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"class": "input", "type": "date"},
            ),
            "activo": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, empresa: Empresa, **kwargs):
        self.empresa = empresa
        super().__init__(*args, **kwargs)
        self.fields["instalacion_deposito"].label = "Instalación o depósito"
        self.fields["estado"].label = "Estado"
        self.fields["fase"].label = "Fase"
        self.fields["capacidad"].label = "Capacidad"
        self.fields["vida_util"].label = "Vida útil"
        self.fields["instalacion_deposito"].required = False
        self.fields["estado"].required = False
        self.fields["fase"].required = False
        self.fields["capacidad"].required = False
        self.fields["vida_util"].required = False
        self.fields["estado"].choices = [("", "Seleccione…")] + list(
            EstadoInstalacionRevision.choices
        )
        self.fields["fase"].choices = [("", "Seleccione…")] + list(
            FaseInstalacionRevision.choices
        )
        self.fields["fecha_inicio"].label = "Fecha inicio"
        self.fields["fecha_fin"].label = "Fecha fin"
        self.fields["fecha_inicio"].input_formats = ["%Y-%m-%d"]
        self.fields["fecha_fin"].input_formats = ["%Y-%m-%d"]
        self.fields["fecha_inicio"].help_text = (
            "Inicio del periodo global (curva Plan / Forecast). "
            "Las fechas por documento se definen en Crear plan."
        )
        self.fields["fecha_fin"].help_text = (
            "Fin del periodo global (meta 100%). El detalle por documento está en Crear plan."
        )

    def clean(self):
        cleaned = super().clean()
        inicio = cleaned.get("fecha_inicio")
        fin = cleaned.get("fecha_fin")
        if inicio and fin and fin < inicio:
            self.add_error(
                "fecha_fin",
                "La fecha fin no puede ser anterior a la fecha inicio.",
            )
        if bool(inicio) ^ bool(fin):
            self.add_error(
                "fecha_fin" if inicio else "fecha_inicio",
                "Debes indicar fecha inicio y fecha fin, o ninguna.",
            )
        return cleaned

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
    prioritario = forms.BooleanField(
        label="Marcar como prioritarios",
        required=False,
        initial=False,
        help_text="Los documentos activados aparecerán en Documentos prioritarios del dashboard.",
        widget=forms.CheckboxInput(attrs={"class": "checkbox"}),
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
        fields = ["codigo", "nombre", "tipo_documento", "prioritario"]
        widgets = {
            "codigo": forms.TextInput(attrs={"class": "input"}),
            "nombre": forms.TextInput(attrs={"class": "input"}),
            "tipo_documento": forms.TextInput(attrs={"class": "input"}),
            "prioritario": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, revision: Revision, **kwargs):
        self.revision = revision
        super().__init__(*args, **kwargs)
        self.fields["prioritario"].label = "Prioritario"
        self.fields["prioritario"].help_text = (
            "Aparece en Documentos prioritarios del dashboard de Indicadores."
        )

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
    rol = forms.ChoiceField(
        label="Rol",
        choices=RolDocumentoCriterio.choices,
        initial=RolDocumentoCriterio.PRINCIPAL,
        widget=forms.Select(attrs={"class": "input"}),
        required=True,
    )
    documento_secundario = forms.ModelChoiceField(
        label="Documento secundario (opcional)",
        queryset=EmpresaDocumento.objects.none(),
        required=False,
        empty_label="— Sin secundario —",
        widget=forms.Select(attrs={"class": "input"}),
        help_text="Otra fuente de evidencia del mismo criterio en esta revisión.",
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
        from .models import (
            RevisionCatalogoCriterio,
            asegurar_snapshot_catalogo_revision,
        )

        empresa = empresa_documento.revision.empresa
        revision = empresa_documento.revision
        asegurar_snapshot_catalogo_revision(revision)
        snap_ids = RevisionCatalogoCriterio.objects.filter(
            revision=revision
        ).values_list("criterio_id", flat=True)
        ya_en_revision = EmpresaDocumentoCriterio.objects.filter(
            revision=revision
        ).values_list("criterio_id", flat=True)
        desactivados = RevisionCriterioDesactivado.objects.filter(
            revision=revision
        ).values_list("criterio_id", flat=True)
        self.fields["criterios"].queryset = (
            Criterio.objects.filter(pk__in=snap_ids)
            .select_related("requisito", "principio", "tema")
            .exclude(pk__in=ya_en_revision)
            .exclude(pk__in=desactivados)
            .order_by("codigo")
        )
        self.fields["documento_secundario"].queryset = (
            EmpresaDocumento.objects.filter(revision=revision, activo=True)
            .exclude(pk=empresa_documento.pk)
            .order_by("codigo")
        )
        self.fields["area"].queryset = Area.objects.filter(empresa=empresa, activo=True)
        self.fields["criterios"].help_text = (
            "Solo se listan criterios aún no asociados a ningún documento de esta revisión. "
            "Este documento queda como principal; puedes indicar uno secundario."
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

    def clean_documento_secundario(self):
        sec = self.cleaned_data.get("documento_secundario")
        if sec and sec.pk == self.empresa_documento.pk:
            raise ValidationError("El secundario debe ser distinto del documento actual.")
        if sec and sec.revision_id != self.empresa_documento.revision_id:
            raise ValidationError("El secundario debe ser de la misma revisión.")
        return sec

    def save(self):
        area = self.cleaned_data["area"]
        rol = self.cleaned_data["rol"]
        sec = self.cleaned_data.get("documento_secundario")
        creados = []
        for criterio in self.cleaned_data["criterios"]:
            vinculo, created = EmpresaDocumentoCriterio.objects.get_or_create(
                revision=self.empresa_documento.revision,
                criterio=criterio,
                defaults={
                    "empresa_documento": self.empresa_documento,
                    "empresa_documento_secundario": sec,
                    "rol": rol,
                    "area": area,
                },
            )
            if not created:
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


def _validar_tamano_archivo(archivo, tamano_max_mb: int | None) -> None:
    if not archivo or not tamano_max_mb:
        return
    max_bytes = int(tamano_max_mb) * 1024 * 1024
    peso = getattr(archivo, "size", None)
    if peso is None:
        return
    if peso > max_bytes:
        peso_mb = peso / (1024 * 1024)
        raise ValidationError(
            f"El archivo pesa {peso_mb:.1f} MB y supera el máximo permitido "
            f"para esta empresa ({tamano_max_mb} MB)."
        )


class DocumentoCargaForm(forms.ModelForm):
    """Carga de evidencia: archivo (Blob) o URL externa."""

    TIPO_ARCHIVO = "archivo"
    TIPO_URL = "url"

    tipo_evidencia = forms.ChoiceField(
        label="Tipo de evidencia",
        choices=(
            (TIPO_ARCHIVO, "Archivo"),
            (TIPO_URL, "URL / enlace"),
        ),
        initial=TIPO_ARCHIVO,
        widget=forms.RadioSelect,
    )

    class Meta:
        model = DocumentoCarga
        fields = ["archivo", "url_evidencia", "observaciones"]
        widgets = {
            "archivo": forms.ClearableFileInput(attrs={"class": "input"}),
            "url_evidencia": forms.URLInput(
                attrs={
                    "class": "input",
                    "placeholder": "https://…",
                    "inputmode": "url",
                }
            ),
            "observaciones": forms.Textarea(
                attrs={"class": "input", "rows": 3, "placeholder": "Opcional"}
            ),
        }

    def __init__(self, *args, empresa: Empresa | None = None, **kwargs):
        self.empresa = empresa
        super().__init__(*args, **kwargs)
        self.fields["archivo"].required = False
        self.fields["url_evidencia"].required = False
        self.fields["url_evidencia"].label = "URL de la evidencia"
        if empresa and getattr(empresa, "tamano_max_mb", None):
            self.fields["archivo"].help_text = (
                f"Máximo {empresa.tamano_max_mb} MB. "
                "Formatos: PDF, DOC/DOCX, XLS/XLSX o imagen."
            )
        self.fields["url_evidencia"].help_text = (
            "Enlace público o compartido (https) al documento o repositorio."
        )

    def clean_archivo(self):
        archivo = self.cleaned_data.get("archivo")
        if archivo:
            _validar_extension_archivo(archivo)
            lim = self.empresa.tamano_max_mb if self.empresa else None
            _validar_tamano_archivo(archivo, lim)
        return archivo

    def clean(self):
        cleaned = super().clean()
        tipo = (cleaned.get("tipo_evidencia") or self.TIPO_ARCHIVO).strip()
        archivo = cleaned.get("archivo")
        url = (cleaned.get("url_evidencia") or "").strip()
        if tipo == self.TIPO_URL:
            if not url:
                self.add_error("url_evidencia", "Indica la URL de la evidencia.")
            cleaned["archivo"] = None
            cleaned["url_evidencia"] = url
        else:
            if not archivo:
                self.add_error("archivo", "Selecciona un archivo para subir.")
            cleaned["url_evidencia"] = ""
        return cleaned

    def save(self, commit=True):
        from .models import (
            EstadoRevisionCarga,
            resetear_criterios_no_aprobados,
            sincronizar_estado_carga_documento,
        )

        obj = super().save(commit=False)
        tipo = (self.cleaned_data.get("tipo_evidencia") or self.TIPO_ARCHIVO).strip()
        if tipo == self.TIPO_URL:
            obj.archivo = None
            obj.url_evidencia = (self.cleaned_data.get("url_evidencia") or "").strip()
            if not obj.nombre_original:
                obj.nombre_original = obj.url_evidencia[:255]
        else:
            obj.url_evidencia = ""
        obj.estado_revision = EstadoRevisionCarga.PENDIENTE
        obj.motivo_rechazo = ""
        obj.revisado_por = None
        obj.revisado_en = None
        obj.activo = True
        if commit:
            obj.save()
            # Nueva evidencia: conservar criterios ya APROBADO; reiniciar el resto.
            resetear_criterios_no_aprobados(obj.empresa_documento)
            sincronizar_estado_carga_documento(obj.empresa_documento)
            obj.refresh_from_db()
        return obj


class RevisionParcialCargaForm(forms.Form):
    """
    Revisión por pregunta sobre una carga.
    POST: accion=guardar|aprobar_todos,
    accion_p_<pregunta_respuesta_id>=CUMPLE|NO_CUMPLE,
    obs_p_<pregunta_respuesta_id>=texto (obligatorio si NO_CUMPLE).
    """

    def __init__(self, *args, carga: DocumentoCarga, **kwargs):
        self.carga = carga
        self.empresa_documento = carga.empresa_documento
        self.vinculos = list(
            EmpresaDocumentoCriterio.objects.filter(
                empresa_documento=self.empresa_documento
            )
            .select_related("criterio", "area")
            .prefetch_related("preguntas__pregunta")
            .order_by("criterio__codigo")
        )
        self.preguntas = list(
            EmpresaDocumentoPregunta.objects.filter(
                vinculo_criterio__empresa_documento=self.empresa_documento
            )
            .select_related("pregunta", "vinculo_criterio", "vinculo_criterio__criterio")
            .order_by(
                "vinculo_criterio__criterio__codigo",
                "pregunta__orden",
                "pregunta__codigo",
            )
        )
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        data = self.data
        accion = (data.get("accion") or "guardar").strip()
        decisions: list[tuple[EmpresaDocumentoPregunta, str, str]] = []

        if accion == "aprobar_todos":
            for p in self.preguntas:
                if p.estado_revision != EstadoRevisionPregunta.CUMPLE:
                    decisions.append((p, EstadoRevisionPregunta.CUMPLE, ""))
            cleaned["decisions"] = decisions
            return cleaned

        for p in self.preguntas:
            if p.estado_revision == EstadoRevisionPregunta.CUMPLE:
                continue
            key = f"accion_p_{p.pk}"
            choice = (data.get(key) or "").strip().upper()
            if choice not in {
                EstadoRevisionPregunta.CUMPLE,
                EstadoRevisionPregunta.NO_CUMPLE,
                "",
            }:
                raise ValidationError(f"Acción inválida para {p.pregunta.codigo}.")
            if not choice:
                continue
            obs = (data.get(f"obs_p_{p.pk}") or "").strip()
            if choice == EstadoRevisionPregunta.NO_CUMPLE and len(obs) < 10:
                self.add_error(
                    None,
                    f"La observación de {p.pregunta.codigo} debe tener al menos 10 caracteres.",
                )
            decisions.append((p, choice, obs))

        if not decisions and not self.errors:
            raise ValidationError(
                "Indica al menos una pregunta (cumple / no cumple), "
                "o usa «Aprobar todos»."
            )
        cleaned["decisions"] = decisions
        return cleaned


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


class DocumentoRevisionConsultorForm(forms.Form):
    """Etiqueta y % de revisión definidos por el consultor (no afectan el avance)."""

    revision_texto = forms.CharField(
        label="Revisión",
        max_length=64,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "placeholder": "Ej. Iniciado, Rev0, RevA, RevB…",
                "autocomplete": "off",
            }
        ),
        help_text="Texto libre. Vacío = sin revisión definida.",
    )
    revision_porcentaje = forms.IntegerField(
        label="Porcentaje",
        required=False,
        min_value=0,
        max_value=100,
        widget=forms.NumberInput(
            attrs={
                "class": "input",
                "min": "0",
                "max": "100",
                "step": "1",
                "placeholder": "0–100",
            }
        ),
        help_text="Gestión interna del documento; no modifica el avance calculado.",
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
    """Crear documento propio de la revisión + primera evidencia (archivo o URL)."""

    TIPO_ARCHIVO = "archivo"
    TIPO_URL = "url"

    tipo_evidencia = forms.ChoiceField(
        label="Tipo de evidencia",
        choices=(
            (TIPO_ARCHIVO, "Archivo"),
            (TIPO_URL, "URL / enlace"),
        ),
        initial=TIPO_ARCHIVO,
        widget=forms.RadioSelect,
    )
    archivo = forms.FileField(
        label="Archivo",
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "input"}),
    )
    url_evidencia = forms.URLField(
        label="URL de la evidencia",
        required=False,
        widget=forms.URLInput(
            attrs={"class": "input", "placeholder": "https://…", "inputmode": "url"}
        ),
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
        self.empresa = getattr(revision, "empresa", None)
        super().__init__(*args, **kwargs)
        if self.empresa and getattr(self.empresa, "tamano_max_mb", None):
            self.fields["archivo"].help_text = (
                f"Máximo {self.empresa.tamano_max_mb} MB. "
                "Formatos: PDF, DOC/DOCX, XLS/XLSX o imagen."
            )
        self.fields["url_evidencia"].help_text = (
            "Enlace público o compartido (https) al documento o repositorio."
        )

    def clean_codigo(self):
        codigo = (self.cleaned_data.get("codigo") or "").strip()
        qs = EmpresaDocumento.objects.filter(revision=self.revision, codigo__iexact=codigo)
        if qs.exists():
            raise ValidationError("Ya existe un documento con este código en la revisión.")
        return codigo

    def clean_archivo(self):
        archivo = self.cleaned_data.get("archivo")
        if archivo:
            _validar_extension_archivo(archivo)
            lim = self.empresa.tamano_max_mb if self.empresa else None
            _validar_tamano_archivo(archivo, lim)
        return archivo

    def clean(self):
        cleaned = super().clean()
        tipo = (cleaned.get("tipo_evidencia") or self.TIPO_ARCHIVO).strip()
        archivo = cleaned.get("archivo")
        url = (cleaned.get("url_evidencia") or "").strip()
        if tipo == self.TIPO_URL:
            if not url:
                self.add_error("url_evidencia", "Indica la URL de la evidencia.")
            cleaned["archivo"] = None
            cleaned["url_evidencia"] = url
        else:
            if not archivo:
                self.add_error("archivo", "Selecciona un archivo para subir.")
            cleaned["url_evidencia"] = ""
        return cleaned

    def save(self, user, commit=True):
        obj = super().save(commit=False)
        obj.revision = self.revision
        obj.documento = None
        obj.origen = OrigenEmpresaDocumento.PROPIO
        obj.activo = True
        self._carga_creada = None
        if commit:
            obj.save()
            from .models import EstadoRevisionCarga

            tipo = (self.cleaned_data.get("tipo_evidencia") or self.TIPO_ARCHIVO).strip()
            url = (self.cleaned_data.get("url_evidencia") or "").strip()
            archivo = self.cleaned_data.get("archivo")
            kwargs = {
                "empresa_documento": obj,
                "observaciones": (self.cleaned_data.get("observaciones") or "").strip(),
                "subido_por": user,
                "activo": True,
                "estado_revision": EstadoRevisionCarga.PENDIENTE,
            }
            if tipo == self.TIPO_URL:
                kwargs["url_evidencia"] = url
                kwargs["nombre_original"] = url[:255]
            else:
                kwargs["archivo"] = archivo
            self._carga_creada = DocumentoCarga.objects.create(**kwargs)
        return obj


class ActividadForm(forms.ModelForm):
    documentos = forms.ModelMultipleChoiceField(
        queryset=EmpresaDocumento.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Documentos relacionados",
    )

    class Meta:
        from .models import Actividad

        model = Actividad
        fields = (
            "titulo",
            "descripcion",
            "alcance",
            "empresa",
            "asignado_a",
            "area",
            "responsable",
            "inicio",
            "fin",
            "enlace_reunion",
            "documentos",
        )
        widgets = {
            "titulo": forms.TextInput(attrs={"class": "input"}),
            "descripcion": forms.Textarea(
                attrs={
                    "class": "input",
                    "rows": 4,
                    "placeholder": "Tenor de la reunión, agenda o notas…",
                }
            ),
            "alcance": forms.Select(attrs={"class": "input", "id": "id_alcance"}),
            "empresa": forms.Select(attrs={"class": "input", "id": "id_empresa"}),
            "asignado_a": forms.Select(attrs={"class": "input", "id": "id_asignado_a"}),
            "area": forms.Select(attrs={"class": "input", "id": "id_area"}),
            "responsable": forms.Select(attrs={"class": "input", "id": "id_responsable"}),
            "inicio": forms.DateTimeInput(
                attrs={"class": "input", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "fin": forms.DateTimeInput(
                attrs={"class": "input", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "enlace_reunion": forms.URLInput(
                attrs={
                    "class": "input",
                    "placeholder": "https://teams.microsoft.com/…",
                }
            ),
        }
        labels = {
            "asignado_a": "Asignar a",
            "area": "Área responsable",
            "responsable": "Responsable",
        }

    def __init__(
        self,
        *args,
        user=None,
        empresas_qs=None,
        documentos_qs=None,
        areas_qs=None,
        responsables_qs=None,
        asignables_qs=None,
        **kwargs,
    ):
        from .models import Area, AlcanceActividad, Responsable
        from .permissions import usuarios_asignables

        self._user = user
        super().__init__(*args, **kwargs)
        self.fields["inicio"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]
        self.fields["fin"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]
        self.fields["empresa"].queryset = empresas_qs or Empresa.objects.none()
        self.fields["empresa"].required = False
        self.fields["empresa"].empty_label = "— Sin empresa —"
        self.fields["area"].queryset = areas_qs if areas_qs is not None else Area.objects.none()
        self.fields["area"].required = False
        self.fields["area"].empty_label = "— Sin área —"
        self.fields["responsable"].queryset = (
            responsables_qs if responsables_qs is not None else Responsable.objects.none()
        )
        self.fields["responsable"].required = False
        self.fields["responsable"].empty_label = "— Sin responsable —"
        self.fields["responsable"].label_from_instance = (
            lambda r: f"{r.nombre}" + (f" · {r.correo}" if r.correo else "")
        )
        qs_asig = asignables_qs
        if qs_asig is None and user is not None:
            qs_asig = usuarios_asignables(user)
        self.fields["asignado_a"].queryset = qs_asig if qs_asig is not None else User.objects.none()
        self.fields["asignado_a"].required = True
        self.fields["asignado_a"].label_from_instance = (
            lambda u: f"{(u.get_full_name() or '').strip() or u.email or u.username}"
        )
        self.fields["documentos"].queryset = documentos_qs or EmpresaDocumento.objects.none()
        self.fields["documentos"].label_from_instance = (
            lambda d: f"{d.codigo} · {d.nombre}"
        )
        self.fields["alcance"].choices = AlcanceActividad.choices
        # Ocultar selector si solo puede asignarse a sí mismo.
        if qs_asig is not None and user is not None:
            count_asig = qs_asig.count() if hasattr(qs_asig, "count") else len(list(qs_asig))
            if count_asig <= 1:
                self.fields["asignado_a"].widget = forms.HiddenInput()
        if not self.is_bound and not self.instance.pk:
            from django.utils import timezone as tz

            now = tz.localtime(tz.now()).replace(minute=0, second=0, microsecond=0)
            self.initial.setdefault("inicio", (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"))
            if user is not None:
                self.initial.setdefault("asignado_a", user.pk)

    def clean(self):
        from .models import AlcanceActividad
        from .permissions import usuarios_asignables

        cleaned = super().clean()
        alcance = cleaned.get("alcance")
        empresa = cleaned.get("empresa")
        area = cleaned.get("area")
        responsable = cleaned.get("responsable")
        asignado = cleaned.get("asignado_a")
        if alcance == AlcanceActividad.CON_CLIENTE and not empresa:
            self.add_error("empresa", "Selecciona la empresa del cliente.")
        if area and not empresa:
            self.add_error("area", "Selecciona una empresa para asignar el área responsable.")
        elif area and empresa and area.empresa_id != empresa.pk:
            self.add_error("area", "El área debe pertenecer a la empresa seleccionada.")
        if responsable and not area:
            self.add_error("responsable", "Selecciona el área para filtrar responsables.")
        elif responsable and area and responsable.area_id != area.pk:
            self.add_error("responsable", "El responsable debe pertenecer al área seleccionada.")
        if asignado and self._user is not None:
            permitidos = set(usuarios_asignables(self._user).values_list("pk", flat=True))
            if asignado.pk not in permitidos:
                self.add_error("asignado_a", "No puedes asignar la actividad a ese usuario.")
        return cleaned


class AcuerdoForm(forms.Form):
    titulo = forms.CharField(
        label="Título del acuerdo",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "Ej. Entrega de D012, D013 y D014"}),
    )
    descripcion = forms.CharField(
        label="Detalle del acuerdo",
        widget=forms.Textarea(
            attrs={
                "class": "input",
                "rows": 3,
                "placeholder": "Qué se acordó…",
            }
        ),
        required=False,
    )
    fecha_compromiso = forms.DateTimeField(
        label="Fecha compromiso",
        required=False,
        widget=forms.DateTimeInput(
            attrs={"class": "input", "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
    )
    documentos = forms.ModelMultipleChoiceField(
        queryset=EmpresaDocumento.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Documentos",
    )

    def __init__(self, *args, documentos_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["documentos"].queryset = documentos_qs or EmpresaDocumento.objects.none()
        self.fields["documentos"].label_from_instance = (
            lambda d: f"{d.codigo} · {d.nombre}"
        )

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get("descripcion") or "").strip() and not cleaned.get("documentos"):
            raise ValidationError(
                "Indica un texto del acuerdo y/o selecciona al menos un documento."
            )
        return cleaned


class AcuerdoRecordatorioForm(forms.Form):
    mensaje = forms.CharField(
        label="Mensaje adicional (opcional)",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "input",
                "rows": 2,
                "placeholder": "Nota breve para el recordatorio…",
            }
        ),
    )
    responsables = forms.ModelMultipleChoiceField(
        label="Responsables destinatarios",
        queryset=Responsable.objects.none(),
        required=True,
        widget=forms.CheckboxSelectMultiple,
        help_text="Elige uno o varios responsables de la empresa (irán en To / CC).",
    )

    def __init__(self, *args, responsables_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["responsables"].queryset = (
            responsables_qs
            if responsables_qs is not None
            else Responsable.objects.none()
        )
        self.fields["responsables"].label_from_instance = (
            lambda r: f"{r.area.nombre} · {r.nombre}"
            + (f" ({r.correo})" if r.correo else "")
        )


class DocumentoVigenciaAprobacionForm(forms.Form):
    """Campos obligatorios al aprobar un documento (frecuencia + fechas)."""

    frecuencia_actualizacion = forms.ChoiceField(
        label="Frecuencia de actualización",
        choices=[
            ("MENSUAL", "Mensual"),
            ("TRIMESTRAL", "Trimestral"),
            ("SEMESTRAL", "Semestral"),
            ("ANUAL", "Anual"),
            ("BIANUAL", "Cada 2 años"),
            ("OTRO", "Otro / según necesidad"),
        ],
        widget=forms.Select(attrs={"class": "input", "id": "id_frecuencia_actualizacion"}),
    )
    frecuencia_otro = forms.CharField(
        label="¿Cada cuánto se actualizará?",
        required=False,
        max_length=120,
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "placeholder": "Ej. cada 18 meses",
                "id": "id_frecuencia_otro",
            }
        ),
    )
    fecha_ultima_revision = forms.DateField(
        label="Rev. 0",
        widget=forms.DateInput(attrs={"class": "input", "type": "date"}),
    )
    fecha_caducidad = forms.DateField(
        label="Fecha de caducidad",
        widget=forms.DateInput(attrs={"class": "input", "type": "date"}),
    )

    def clean(self):
        cleaned = super().clean()
        freq = cleaned.get("frecuencia_actualizacion")
        otro = (cleaned.get("frecuencia_otro") or "").strip()
        if freq == "OTRO" and len(otro) < 3:
            self.add_error(
                "frecuencia_otro",
                "Indica cada cuánto se actualizará el documento (mín. 3 caracteres).",
            )
        cleaned["frecuencia_otro"] = otro if freq == "OTRO" else ""
        ref = cleaned.get("fecha_ultima_revision")
        cad = cleaned.get("fecha_caducidad")
        if ref and cad and cad < ref:
            self.add_error(
                "fecha_caducidad",
                "La caducidad no puede ser anterior a la Rev. 0.",
            )
        return cleaned
