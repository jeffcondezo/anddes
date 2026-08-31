from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import DecimalField, Sum


class Empresa(models.Model):
    nombre = models.CharField(max_length=255)
    ruc = models.CharField(max_length=20, blank=True)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"

    def __str__(self) -> str:
        return self.nombre


class Area(models.Model):
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="areas",
    )
    nombre = models.CharField(max_length=255)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "nombre"],
                name="maestro_area_empresa_nombre_uniq",
            ),
        ]
        verbose_name = "Área"
        verbose_name_plural = "Áreas"

    def __str__(self) -> str:
        return f"{self.nombre} ({self.empresa.nombre})"


class TipoUsuario(models.TextChoices):
    ADMINISTRADOR = "ADMINISTRADOR", "Administrador"
    GESTOR = "GESTOR", "Gestor"
    CLIENTE = "CLIENTE", "Cliente"


class PerfilUsuario(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil",
    )
    tipo = models.CharField(max_length=20, choices=TipoUsuario.choices)
    activo = models.BooleanField(default=True)
    empresas = models.ManyToManyField(
        Empresa,
        blank=True,
        related_name="gestores",
        help_text="Empresas supervisadas por un gestor.",
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="clientes",
        help_text="Empresa minera asociada a un usuario cliente.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__email"]
        verbose_name = "Perfil de usuario"
        verbose_name_plural = "Perfiles de usuario"

    def __str__(self) -> str:
        return f"{self.user.email or self.user.username} ({self.get_tipo_display()})"

    @property
    def es_administrador(self) -> bool:
        return self.tipo == TipoUsuario.ADMINISTRADOR and self.activo

    @property
    def es_gestor(self) -> bool:
        return self.tipo == TipoUsuario.GESTOR and self.activo

    @property
    def es_cliente(self) -> bool:
        return self.tipo == TipoUsuario.CLIENTE and self.activo


class MotivoIntentoAcceso(models.TextChoices):
    USUARIO_INEXISTENTE = "USUARIO_INEXISTENTE", "Usuario no registrado"
    SIN_PERFIL = "SIN_PERFIL", "Usuario sin perfil"
    INACTIVO = "INACTIVO", "Usuario o perfil inactivo"
    SIN_EMAIL = "SIN_EMAIL", "Entra ID no devolvió correo"
    OTRO = "OTRO", "Otro"


class IntentoAcceso(models.Model):
    email = models.EmailField(blank=True)
    oid = models.CharField(max_length=64, blank=True)
    motivo = models.CharField(max_length=32, choices=MotivoIntentoAcceso.choices)
    detalle = models.TextField(blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Intento de acceso"
        verbose_name_plural = "Intentos de acceso"

    def __str__(self) -> str:
        return f"{self.email or self.oid or 'desconocido'} · {self.get_motivo_display()}"


class RegistroLoginExterno(models.Model):
    """Registro de inicios de sesión exitosos de usuarios cliente (empresas externas)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logins_externos",
    )
    email = models.EmailField()
    nombre = models.CharField(max_length=255, blank=True)
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logins_externos",
    )
    empresa_nombre = models.CharField(
        max_length=255,
        blank=True,
        help_text="Nombre de la empresa al momento del login (histórico).",
    )
    oid = models.CharField(max_length=64, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Registro de login externo"
        verbose_name_plural = "Registros de login externos"

    def __str__(self) -> str:
        return f"{self.email} · {self.creado_en}"


class Tema(models.Model):
    codigo = models.CharField(max_length=32, unique=True)
    nombre = models.CharField(max_length=255)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["codigo"]
        verbose_name = "Tema"
        verbose_name_plural = "Temas"

    def __str__(self) -> str:
        return f"{self.codigo} · {self.nombre}"


class Principio(models.Model):
    codigo = models.CharField(max_length=32, unique=True)
    descripcion = models.TextField()
    tema = models.ForeignKey(
        Tema,
        on_delete=models.PROTECT,
        related_name="principios",
    )
    suma_ponderacion = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal("0"),
        help_text="Suma de las ponderaciones de los criterios asociados.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["codigo"]
        verbose_name = "Principio"
        verbose_name_plural = "Principios"

    def __str__(self) -> str:
        return f"{self.codigo}"

    def save(self, *args, **kwargs) -> None:
        old_tema_id = None
        if self.pk:
            old = Principio.objects.filter(pk=self.pk).values("tema_id").first()
            if old:
                old_tema_id = old["tema_id"]
        super().save(*args, **kwargs)
        if old_tema_id and old_tema_id != self.tema_id:
            self.criterios.update(tema_id=self.tema_id)

    def recalcular_suma_ponderacion(self) -> None:
        total = self.criterios.aggregate(
            total=Sum("ponderacion", output_field=DecimalField(max_digits=8, decimal_places=4))
        )["total"] or Decimal("0")
        if self.suma_ponderacion != total:
            self.suma_ponderacion = total
            self.save(update_fields=["suma_ponderacion", "actualizado_en"])


class Criterio(models.Model):
    codigo = models.CharField(max_length=32, unique=True)
    descripcion = models.TextField()
    principio = models.ForeignKey(
        Principio,
        on_delete=models.PROTECT,
        related_name="criterios",
    )
    tema = models.ForeignKey(
        Tema,
        on_delete=models.PROTECT,
        related_name="criterios",
        help_text="Denormalizado desde el principio para facilitar consultas.",
    )
    ponderacion = models.DecimalField(max_digits=8, decimal_places=4)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["codigo"]
        verbose_name = "Criterio"
        verbose_name_plural = "Criterios"

    def __str__(self) -> str:
        return f"{self.codigo}"

    def clean(self) -> None:
        if self.principio_id:
            tema_principio = self.principio.tema_id
            if self.tema_id and self.tema_id != tema_principio:
                raise ValidationError(
                    {"tema": "El tema del criterio debe coincidir con el tema del principio."}
                )
            self.tema_id = tema_principio

    def save(self, *args, **kwargs) -> None:
        old_principio_id = None
        if self.pk:
            old = Criterio.objects.filter(pk=self.pk).values("principio_id").first()
            if old:
                old_principio_id = old["principio_id"]
        if self.principio_id:
            self.tema_id = self.principio.tema_id
        super().save(*args, **kwargs)
        self.principio.recalcular_suma_ponderacion()
        if old_principio_id and old_principio_id != self.principio_id:
            Principio.objects.get(pk=old_principio_id).recalcular_suma_ponderacion()

    def delete(self, *args, **kwargs):
        principio = self.principio
        result = super().delete(*args, **kwargs)
        principio.recalcular_suma_ponderacion()
        return result


class Documento(models.Model):
    codigo = models.CharField(max_length=32, unique=True)
    nombre = models.CharField(max_length=255)
    tipo_documento = models.CharField(max_length=64)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["codigo"]
        verbose_name = "Documento"
        verbose_name_plural = "Documentos"

    def __str__(self) -> str:
        return f"{self.codigo} · {self.nombre}"


class DocumentoCriterio(models.Model):
    """Relación estándar (plantilla) entre documento del catálogo y criterio."""

    documento = models.ForeignKey(
        Documento,
        on_delete=models.CASCADE,
        related_name="criterios_estandar",
    )
    criterio = models.ForeignKey(
        Criterio,
        on_delete=models.CASCADE,
        related_name="documentos_estandar",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["documento__codigo", "criterio__codigo"]
        constraints = [
            models.UniqueConstraint(
                fields=["documento", "criterio"],
                name="maestro_documentocriterio_doc_crit_uniq",
            ),
        ]
        verbose_name = "Relación estándar documento-criterio"
        verbose_name_plural = "Relaciones estándar documento-criterio"

    def __str__(self) -> str:
        return f"{self.documento.codigo} → {self.criterio.codigo}"


class OrigenEmpresaDocumento(models.TextChoices):
    CATALOGO = "CATALOGO", "Catálogo"
    PROPIO = "PROPIO", "Propio"


class Revision(models.Model):
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="revisiones",
    )
    anio = models.PositiveSmallIntegerField()
    nombre = models.CharField(max_length=255, blank=True)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-anio", "-creado_en"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "anio"],
                name="maestro_revision_empresa_anio_uniq",
            ),
        ]
        verbose_name = "Revisión"
        verbose_name_plural = "Revisiones"

    def __str__(self) -> str:
        if self.nombre:
            return f"{self.empresa.nombre} · {self.anio} ({self.nombre})"
        return f"{self.empresa.nombre} · {self.anio}"

    @property
    def etiqueta(self) -> str:
        return self.nombre or str(self.anio)


class EmpresaDocumento(models.Model):
    revision = models.ForeignKey(
        Revision,
        on_delete=models.CASCADE,
        related_name="documentos",
    )
    documento = models.ForeignKey(
        Documento,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="empresas_documento",
        help_text="Referencia al catálogo estándar, si aplica.",
    )
    codigo = models.CharField(max_length=32)
    nombre = models.CharField(max_length=255)
    tipo_documento = models.CharField(max_length=64)
    origen = models.CharField(
        max_length=16,
        choices=OrigenEmpresaDocumento.choices,
        default=OrigenEmpresaDocumento.PROPIO,
    )
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["codigo"]
        constraints = [
            models.UniqueConstraint(
                fields=["revision", "codigo"],
                name="maestro_empresadoc_revision_codigo_uniq",
            ),
            models.UniqueConstraint(
                fields=["revision", "documento"],
                condition=models.Q(documento__isnull=False),
                name="maestro_empresadoc_revision_documento_uniq",
            ),
        ]
        verbose_name = "Documento de empresa"
        verbose_name_plural = "Documentos de empresa"

    def __str__(self) -> str:
        return f"{self.revision} · {self.codigo}"

    @property
    def empresa(self) -> Empresa:
        return self.revision.empresa

    def clean(self) -> None:
        if self.origen == OrigenEmpresaDocumento.CATALOGO and not self.documento_id:
            raise ValidationError(
                {"documento": "Un documento de catálogo debe referenciar el catálogo estándar."}
            )
        if self.origen == OrigenEmpresaDocumento.PROPIO and self.documento_id:
            raise ValidationError(
                {"documento": "Un documento propio no debe referenciar el catálogo estándar."}
            )


class EmpresaDocumentoCriterio(models.Model):
    empresa_documento = models.ForeignKey(
        EmpresaDocumento,
        on_delete=models.CASCADE,
        related_name="vinculos_criterio",
    )
    criterio = models.ForeignKey(
        Criterio,
        on_delete=models.PROTECT,
        related_name="vinculos_empresa_documento",
    )
    area = models.ForeignKey(
        Area,
        on_delete=models.PROTECT,
        related_name="vinculos_empresa_documento",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["criterio__codigo"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa_documento", "criterio"],
                name="maestro_empresadoccrit_doc_criterio_uniq",
            ),
        ]
        verbose_name = "Vínculo documento-criterio"
        verbose_name_plural = "Vínculos documento-criterio"

    def __str__(self) -> str:
        return f"{self.empresa_documento.codigo} → {self.criterio.codigo}"

    def clean(self) -> None:
        if self.empresa_documento_id and self.area_id:
            if self.area.empresa_id != self.empresa_documento.revision.empresa_id:
                raise ValidationError(
                    {"area": "El área debe pertenecer a la misma empresa del documento."}
                )
