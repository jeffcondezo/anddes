from __future__ import annotations

from decimal import Decimal
from pathlib import Path

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


class Requisito(models.Model):
    codigo = models.CharField(max_length=32, unique=True)
    descripcion = models.TextField()
    principio = models.ForeignKey(
        Principio,
        on_delete=models.PROTECT,
        related_name="requisitos",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["codigo"]
        verbose_name = "Requisito"
        verbose_name_plural = "Requisitos"

    def __str__(self) -> str:
        return f"{self.codigo}"

    def save(self, *args, **kwargs) -> None:
        old_principio_id = None
        if self.pk:
            old = Requisito.objects.filter(pk=self.pk).values("principio_id").first()
            if old:
                old_principio_id = old["principio_id"]
        super().save(*args, **kwargs)
        if old_principio_id and old_principio_id != self.principio_id:
            tema_id = self.principio.tema_id
            self.criterios.update(principio_id=self.principio_id, tema_id=tema_id)
            Principio.objects.get(pk=old_principio_id).recalcular_suma_ponderacion()
            self.principio.recalcular_suma_ponderacion()

    def recalcular_ponderaciones_criterios(self) -> None:
        """Asigna a cada criterio 1/N según cuántos criterios tiene este requisito."""
        qs = self.criterios.all()
        n = qs.count()
        if n == 0:
            self.principio.recalcular_suma_ponderacion()
            return
        peso = (Decimal("1") / Decimal(n)).quantize(Decimal("0.0001"))
        qs.update(ponderacion=peso)
        self.principio.recalcular_suma_ponderacion()


class Criterio(models.Model):
    codigo = models.CharField(max_length=32, unique=True)
    descripcion = models.TextField()
    requisito = models.ForeignKey(
        Requisito,
        on_delete=models.PROTECT,
        related_name="criterios",
    )
    principio = models.ForeignKey(
        Principio,
        on_delete=models.PROTECT,
        related_name="criterios",
        help_text="Denormalizado desde el requisito para facilitar consultas.",
    )
    tema = models.ForeignKey(
        Tema,
        on_delete=models.PROTECT,
        related_name="criterios",
        help_text="Denormalizado desde el principio para facilitar consultas.",
    )
    ponderacion = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal("1"),
        help_text="Calculada automáticamente como 1 / N criterios del mismo requisito.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["codigo"]
        verbose_name = "Criterio"
        verbose_name_plural = "Criterios"

    def __str__(self) -> str:
        return f"{self.codigo}"

    def clean(self) -> None:
        if self.requisito_id:
            principio = self.requisito.principio
            if self.principio_id and self.principio_id != principio.id:
                raise ValidationError(
                    {
                        "principio": (
                            "El principio del criterio debe coincidir con el del requisito."
                        )
                    }
                )
            if self.tema_id and self.tema_id != principio.tema_id:
                raise ValidationError(
                    {"tema": "El tema del criterio debe coincidir con el tema del principio."}
                )
            self.principio_id = principio.id
            self.tema_id = principio.tema_id

    def save(self, *args, **kwargs) -> None:
        old_requisito_id = None
        if self.pk:
            old = Criterio.objects.filter(pk=self.pk).values("requisito_id").first()
            if old:
                old_requisito_id = old["requisito_id"]
        if self.requisito_id:
            self.principio_id = self.requisito.principio_id
            self.tema_id = self.requisito.principio.tema_id
        if self.ponderacion is None:
            self.ponderacion = Decimal("1")
        super().save(*args, **kwargs)
        self.requisito.recalcular_ponderaciones_criterios()
        if old_requisito_id and old_requisito_id != self.requisito_id:
            Requisito.objects.get(pk=old_requisito_id).recalcular_ponderaciones_criterios()

    def delete(self, *args, **kwargs):
        requisito = self.requisito
        result = super().delete(*args, **kwargs)
        requisito.recalcular_ponderaciones_criterios()
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
    revision = models.ForeignKey(
        Revision,
        on_delete=models.CASCADE,
        related_name="vinculos_criterio",
        help_text="Denormalizado desde el documento para garantizar un criterio por revisión.",
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
            models.UniqueConstraint(
                fields=["revision", "criterio"],
                name="maestro_empresadoccrit_revision_criterio_uniq",
            ),
        ]
        verbose_name = "Vínculo documento-criterio"
        verbose_name_plural = "Vínculos documento-criterio"

    def __str__(self) -> str:
        return f"{self.empresa_documento.codigo} → {self.criterio.codigo}"

    def clean(self) -> None:
        if self.empresa_documento_id:
            self.revision_id = self.empresa_documento.revision_id
        if self.empresa_documento_id and self.area_id:
            if self.area.empresa_id != self.empresa_documento.revision.empresa_id:
                raise ValidationError(
                    {"area": "El área debe pertenecer a la misma empresa del documento."}
                )
        if self.revision_id and self.criterio_id:
            qs = EmpresaDocumentoCriterio.objects.filter(
                revision_id=self.revision_id,
                criterio_id=self.criterio_id,
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                otro = qs.select_related("empresa_documento").first()
                raise ValidationError(
                    {
                        "criterio": (
                            "Este criterio ya está asociado al documento "
                            f"{otro.empresa_documento.codigo} en esta revisión."
                        )
                    }
                )

    def save(self, *args, **kwargs) -> None:
        if self.empresa_documento_id:
            self.revision_id = self.empresa_documento.revision_id
        super().save(*args, **kwargs)


class RevisionCriterioDesactivado(models.Model):
    """Criterio marcado como no aplicable en una revisión concreta de empresa."""

    revision = models.ForeignKey(
        Revision,
        on_delete=models.CASCADE,
        related_name="criterios_desactivados",
    )
    criterio = models.ForeignKey(
        Criterio,
        on_delete=models.PROTECT,
        related_name="desactivaciones_revision",
    )
    motivo = models.TextField(
        help_text="Motivo por el que el criterio no aplica a esta revisión/empresa.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["criterio__codigo"]
        constraints = [
            models.UniqueConstraint(
                fields=["revision", "criterio"],
                name="maestro_rev_criterio_desactivado_uniq",
            ),
        ]
        verbose_name = "Criterio desactivado en revisión"
        verbose_name_plural = "Criterios desactivados en revisión"

    def __str__(self) -> str:
        return f"{self.revision} · {self.criterio.codigo} (no aplica)"


def documento_carga_upload_to(instance: "DocumentoCarga", filename: str) -> str:
    revision_id = instance.empresa_documento.revision_id
    return f"revisiones/{revision_id}/documentos/{instance.empresa_documento_id}/{filename}"


class EstadoRevisionCarga(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente de revisión"
    APROBADO = "APROBADO", "Aprobado"
    RECHAZADO = "RECHAZADO", "Rechazado"


class DocumentoCarga(models.Model):
    """Evidencia/archivo subido por el cliente para un documento de la revisión."""

    empresa_documento = models.ForeignKey(
        EmpresaDocumento,
        on_delete=models.CASCADE,
        related_name="cargas",
    )
    archivo = models.FileField(upload_to=documento_carga_upload_to)
    nombre_original = models.CharField(max_length=255, blank=True)
    observaciones = models.TextField(blank=True)
    subido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documentos_cargados",
    )
    activo = models.BooleanField(
        default=True,
        help_text="La carga vigente es la más reciente con activo=True.",
    )
    estado_revision = models.CharField(
        max_length=16,
        choices=EstadoRevisionCarga.choices,
        default=EstadoRevisionCarga.PENDIENTE,
        help_text="Solo las cargas APROBADO cuentan en el avance del cliente.",
    )
    motivo_rechazo = models.TextField(
        blank=True,
        help_text="Mensaje visible para la minera cuando se rechaza la evidencia.",
    )
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documentos_revisados",
    )
    revisado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Carga de documento"
        verbose_name_plural = "Cargas de documento"

    def __str__(self) -> str:
        return f"{self.empresa_documento.codigo} · {self.nombre_original or self.archivo.name}"

    @property
    def esta_pendiente(self) -> bool:
        return self.estado_revision == EstadoRevisionCarga.PENDIENTE

    @property
    def esta_aprobado(self) -> bool:
        return self.estado_revision == EstadoRevisionCarga.APROBADO

    @property
    def esta_rechazado(self) -> bool:
        return self.estado_revision == EstadoRevisionCarga.RECHAZADO

    def save(self, *args, **kwargs) -> None:
        if self.archivo and not self.nombre_original:
            self.nombre_original = Path(self.archivo.name).name
        super().save(*args, **kwargs)
        if self.activo and self.pk:
            DocumentoCarga.objects.filter(
                empresa_documento_id=self.empresa_documento_id,
                activo=True,
            ).exclude(pk=self.pk).update(activo=False)


class TipoDocumentoMensaje(models.TextChoices):
    RECHAZO = "RECHAZO", "Rechazo"
    OBSERVACION = "OBSERVACION", "Observación"
    RESPUESTA = "RESPUESTA", "Respuesta / nueva carga"
    ACUERDO = "ACUERDO", "Acuerdo"
    SISTEMA = "SISTEMA", "Sistema"


class DocumentoMensaje(models.Model):
    """Hilo de comunicación consultor ↔ minera sobre un documento de la revisión."""

    empresa_documento = models.ForeignKey(
        EmpresaDocumento,
        on_delete=models.CASCADE,
        related_name="mensajes",
    )
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mensajes_documento",
    )
    es_consultor = models.BooleanField(
        default=False,
        help_text="True si el mensaje lo escribe admin/gestor; False si lo escribe el cliente.",
    )
    tipo = models.CharField(
        max_length=16,
        choices=TipoDocumentoMensaje.choices,
        default=TipoDocumentoMensaje.OBSERVACION,
    )
    texto = models.TextField()
    carga = models.ForeignKey(
        DocumentoCarga,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mensajes",
        help_text="Carga asociada, si el mensaje nace de un rechazo o una nueva evidencia.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["creado_en"]
        verbose_name = "Mensaje de documento"
        verbose_name_plural = "Mensajes de documento"

    def __str__(self) -> str:
        lado = "Consultor" if self.es_consultor else "Cliente"
        return f"{self.empresa_documento.codigo} · {lado} · {self.creado_en:%d/%m/%Y}"


def ponderaciones_revision(revision: Revision) -> dict[int, Decimal]:
    """
    Ponderación efectiva por criterio en la revisión: 1/N entre criterios
    activos (no desactivados) del mismo requisito.
    """
    desactivados = set(
        RevisionCriterioDesactivado.objects.filter(revision=revision).values_list(
            "criterio_id", flat=True
        )
    )
    activos = (
        Criterio.objects.exclude(pk__in=desactivados)
        .values_list("id", "requisito_id")
        .order_by("requisito_id", "id")
    )
    por_requisito: dict[int, list[int]] = {}
    for criterio_id, requisito_id in activos:
        por_requisito.setdefault(requisito_id, []).append(criterio_id)

    resultado: dict[int, Decimal] = {}
    for ids in por_requisito.values():
        n = len(ids)
        if n == 0:
            continue
        peso = (Decimal("1") / Decimal(n)).quantize(Decimal("0.0001"))
        for criterio_id in ids:
            resultado[criterio_id] = peso
    return resultado

