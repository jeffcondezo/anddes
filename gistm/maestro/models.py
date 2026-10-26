from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import DecimalField, Q, Sum
from django.utils import timezone


class Empresa(models.Model):
    nombre = models.CharField(max_length=255)
    ruc = models.CharField(max_length=20, blank=True)
    activo = models.BooleanField(default=True)
    notificaciones_activas = models.BooleanField(
        default=True,
        verbose_name="Envío de notificaciones por correo",
        help_text=(
            "Si está desactivado, el sistema no envía correos automáticos ni manuales "
            "de recordatorios (acuerdos, caducidad de documentos, etc.) a esta empresa."
        ),
    )
    tamano_max_mb = models.PositiveIntegerField(
        default=25,
        verbose_name="Tamaño máximo de archivo (MB)",
        help_text="Peso máximo permitido al cargar evidencias en esta empresa.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(tamano_max_mb__gte=1),
                name="maestro_empresa_tamano_max_mb_gte_1",
            ),
        ]

    def __str__(self) -> str:
        return self.nombre

    def delete(self, using=None, keep_parents=False):
        """No se borra: se desactiva para conservar historial (Anexo §6.10)."""
        self.activo = False
        self.save(update_fields=["activo", "actualizado_en"])

    @property
    def tamano_max_bytes(self) -> int:
        return int(self.tamano_max_mb) * 1024 * 1024


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


class Responsable(models.Model):
    """Persona de contacto vinculada a un área de la empresa."""

    area = models.ForeignKey(
        Area,
        on_delete=models.CASCADE,
        related_name="responsables",
    )
    nombre = models.CharField(max_length=255)
    dni = models.CharField(
        max_length=8,
        verbose_name="DNI",
        help_text="Exactamente 8 dígitos numéricos.",
    )
    telefono = models.CharField(max_length=40, blank=True, verbose_name="Número / teléfono")
    correo = models.EmailField(blank=True)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Responsable"
        verbose_name_plural = "Responsables"

    def __str__(self) -> str:
        return f"{self.nombre} · {self.area.nombre}"

    def clean(self) -> None:
        dni = (self.dni or "").strip()
        if not dni.isdigit() or len(dni) != 8:
            raise ValidationError({"dni": "El DNI debe tener exactamente 8 dígitos numéricos."})
        self.dni = dni


class TipoNotificacionAcuerdo(models.TextChoices):
    ANTES = "ANTES", "Antes del vencimiento"
    POST = "POST", "Post vencimiento"


# Día del vencimiento: recordatorio base del sistema (no configurable por empresa).
NOTIFICACION_ACUERDO_DIA_SISTEMA = 0


class EmpresaNotificacionRegla(models.Model):
    """
    Regla de correo automático de acuerdos, configurable por empresa.
    - ANTES: envía una vez cuando faltan `dias` para la fecha de compromiso.
    - POST: envía una vez cuando han pasado `dias` tras el vencimiento.
    El día 0 (mismo día del compromiso) lo cubre siempre el sistema.
    """

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="notificacion_reglas",
    )
    tipo = models.CharField(
        max_length=8,
        choices=TipoNotificacionAcuerdo.choices,
    )
    dias = models.PositiveIntegerField(
        help_text=(
            "ANTES: días antes del vencimiento. "
            "POST: días después del vencimiento. "
            "El día 0 lo reserva el sistema."
        ),
    )
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tipo", "dias"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "tipo", "dias"],
                name="maestro_emp_notif_regla_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(dias__gte=0),
                name="maestro_emp_notif_regla_dias_gte_0",
            ),
        ]
        verbose_name = "Regla de notificación"
        verbose_name_plural = "Reglas de notificación"

    def __str__(self) -> str:
        return f"{self.empresa.nombre} · {self.get_tipo_display()} · {self.etiqueta}"

    def clean(self) -> None:
        if self.dias == NOTIFICACION_ACUERDO_DIA_SISTEMA:
            raise ValidationError(
                {
                    "dias": (
                        "El día 0 (mismo día del vencimiento) ya lo cubre la "
                        "configuración base del sistema; no se puede agregar como regla."
                    )
                }
            )
        if self.dias < 1:
            raise ValidationError({"dias": "Indica un número de días mayor a 0."})

    @property
    def etiqueta(self) -> str:
        if self.tipo == TipoNotificacionAcuerdo.ANTES:
            if self.dias == 1:
                return "1 día antes"
            return f"{self.dias} días antes"
        if self.dias == 1:
            return "1 día después"
        return f"{self.dias} días después"


class AcuerdoNotificacionEnvio(models.Model):
    """Registro de envíos automáticos para no duplicar la misma regla el mismo día."""

    acuerdo = models.ForeignKey(
        "Acuerdo",
        on_delete=models.CASCADE,
        related_name="notificacion_envios",
    )
    regla = models.ForeignKey(
        EmpresaNotificacionRegla,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="envios",
    )
    tipo = models.CharField(max_length=8, choices=TipoNotificacionAcuerdo.choices)
    dias = models.PositiveIntegerField()
    enviado_en = models.DateTimeField(auto_now_add=True)
    dia_referencia = models.DateField(
        help_text="Día local en que se disparó la regla.",
    )

    class Meta:
        ordering = ["-enviado_en"]
        indexes = [
            models.Index(
                fields=["acuerdo", "tipo", "dias", "dia_referencia"],
                name="maestro_acuerdonotif_idx",
            ),
        ]
        verbose_name = "Envío de notificación de acuerdo"
        verbose_name_plural = "Envíos de notificación de acuerdos"

    def __str__(self) -> str:
        return f"Acuerdo {self.acuerdo_id} · {self.tipo}/{self.dias} · {self.dia_referencia}"


class TipoCorreoEnvio(models.TextChoices):
    ACUERDO_NUEVO = "ACUERDO_NUEVO", "Acuerdo (nuevo)"
    ACUERDO_RECORDATORIO = "ACUERDO_RECORDATORIO", "Acuerdo (recordatorio)"
    ACTIVIDAD = "ACTIVIDAD", "Actividad"
    CADUCIDAD = "CADUCIDAD", "Caducidad de documento"
    OTRO = "OTRO", "Otro"


class CorreoEnvio(models.Model):
    """Historial de correos enviados a clientes (To) y copias (CC)."""

    tipo = models.CharField(
        max_length=24,
        choices=TipoCorreoEnvio.choices,
        default=TipoCorreoEnvio.OTRO,
    )
    asunto = models.CharField(max_length=255)
    destinatarios_to = models.TextField(
        help_text="Destinatario principal (To), uno por línea o separados por coma.",
    )
    destinatarios_cc = models.TextField(
        blank=True,
        help_text="Copias (CC).",
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="correos_enviados",
    )
    acuerdo = models.ForeignKey(
        "Acuerdo",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="correos_enviados",
    )
    actividad = models.ForeignKey(
        "Actividad",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="correos_enviados",
    )
    automatico = models.BooleanField(
        default=False,
        help_text="True si lo disparó el cron o una regla automática.",
    )
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="correos_enviados",
    )
    exito = models.BooleanField(default=True)
    error = models.TextField(blank=True)
    enviado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-enviado_en"]
        verbose_name = "Correo enviado"
        verbose_name_plural = "Correos enviados"

    def __str__(self) -> str:
        return f"{self.asunto} · {self.enviado_en:%d/%m/%Y %H:%M}"

    @property
    def to_lista(self) -> list[str]:
        return [
            p.strip()
            for p in self.destinatarios_to.replace(";", ",").replace("\n", ",").split(",")
            if p.strip()
        ]

    @property
    def cc_lista(self) -> list[str]:
        if not self.destinatarios_cc.strip():
            return []
        return [
            p.strip()
            for p in self.destinatarios_cc.replace(";", ",").replace("\n", ",").split(",")
            if p.strip()
        ]


class PlantillaCorreo(models.Model):
    """
    Plantilla HTML editable (Administración → Correos).
    Usa sintaxis Django Template: {{ titulo_correo }}, {{ intro }}, {{ logo_src }}, etc.
    """

    CODIGO_ACUERDO = "ACUERDO_NOTIFICACION"

    codigo = models.CharField(
        max_length=64,
        unique=True,
        help_text="Clave interna (ej. ACUERDO_NOTIFICACION).",
    )
    nombre = models.CharField(max_length=120)
    descripcion = models.TextField(
        blank=True,
        help_text="Ayuda para el editor: variables disponibles, uso del logo, etc.",
    )
    html = models.TextField(
        help_text="HTML completo del correo. Placeholders con sintaxis Django Template.",
    )
    activo = models.BooleanField(default=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="plantillas_correo_editadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Plantilla de correo"
        verbose_name_plural = "Plantillas de correo"

    def __str__(self) -> str:
        return f"{self.nombre} ({self.codigo})"


class TipoUsuario(models.TextChoices):
    ADMINISTRADOR = "ADMINISTRADOR", "Administrador"
    GESTOR = "GESTOR", "Gestor"
    CLIENTE_LECTURA = "CLIENTE_LECTURA", "Cliente (solo lectura)"
    CLIENTE_CARGA = "CLIENTE_CARGA", "Cliente (con carga)"


TIPOS_CLIENTE = (TipoUsuario.CLIENTE_LECTURA, TipoUsuario.CLIENTE_CARGA)


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
    revisiones = models.ManyToManyField(
        "Revision",
        blank=True,
        related_name="clientes_autorizados",
        help_text=(
            "Revisiones a las que puede acceder el cliente. "
            "Si está vacío, no ve ninguna revisión de la empresa."
        ),
    )
    reporta_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subordinados_perfil",
        help_text="Jefe / coordinador al que reporta este gestor.",
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
        return self.tipo in TIPOS_CLIENTE and self.activo

    @property
    def es_cliente_carga(self) -> bool:
        return self.tipo == TipoUsuario.CLIENTE_CARGA and self.activo

    @property
    def es_cliente_lectura(self) -> bool:
        return self.tipo == TipoUsuario.CLIENTE_LECTURA and self.activo

    def clean(self) -> None:
        if self.tipo != TipoUsuario.GESTOR:
            self.reporta_a = None
            return
        if not self.reporta_a_id:
            return
        if self.user_id and self.reporta_a_id == self.user_id:
            raise ValidationError({"reporta_a": "Un usuario no puede reportarse a sí mismo."})
        jefe_perfil = getattr(self.reporta_a, "perfil", None)
        if jefe_perfil is None:
            try:
                jefe_perfil = self.reporta_a.perfil
            except PerfilUsuario.DoesNotExist:
                jefe_perfil = None
        if not jefe_perfil or jefe_perfil.tipo not in (
            TipoUsuario.GESTOR,
            TipoUsuario.ADMINISTRADOR,
        ):
            raise ValidationError(
                {"reporta_a": "Solo puede reportar a un gestor o administrador."}
            )
        # Detectar ciclos en la cadena reporta_a.
        visto = {self.user_id} if self.user_id else set()
        actual = self.reporta_a
        while actual is not None:
            if actual.pk in visto:
                raise ValidationError(
                    {"reporta_a": "Esa jerarquía formaría un ciclo (A reporta a B y B a A)."}
                )
            visto.add(actual.pk)
            try:
                actual = actual.perfil.reporta_a
            except PerfilUsuario.DoesNotExist:
                break


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


class TipoAuditoria(models.TextChoices):
    LOGIN = "LOGIN", "Inicio de sesión"
    CARGA = "CARGA", "Carga de evidencia"
    EDICION = "EDICION", "Edición"
    APROBACION = "APROBACION", "Aprobación"
    RECHAZO = "RECHAZO", "Rechazo"
    DESCARGA = "DESCARGA", "Descarga / apertura"
    EXPORTACION = "EXPORTACION", "Exportación"
    ARCHIVO = "ARCHIVO", "Archivo / desactivación"


class RegistroAuditoria(models.Model):
    """
    Log append-only de acciones sensibles (Anexo 01 §9.5).
    No se actualiza ni elimina desde la aplicación.
    """

    tipo = models.CharField(max_length=16, choices=TipoAuditoria.choices, db_index=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registros_auditoria",
    )
    usuario_email = models.CharField(
        max_length=254,
        blank=True,
        help_text="Correo al momento del evento (histórico si el usuario se desactiva).",
    )
    usuario_nombre = models.CharField(max_length=255, blank=True)
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registros_auditoria",
    )
    empresa_nombre = models.CharField(max_length=255, blank=True)
    objeto_tipo = models.CharField(max_length=64, blank=True)
    objeto_id = models.PositiveIntegerField(null=True, blank=True)
    detalle = models.TextField()
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Registro de auditoría"
        verbose_name_plural = "Registros de auditoría"
        indexes = [
            models.Index(fields=["-creado_en", "tipo"]),
            models.Index(fields=["empresa", "-creado_en"]),
        ]

    def __str__(self) -> str:
        quien = self.usuario_email or self.usuario_nombre or "sistema"
        return f"{self.get_tipo_display()} · {quien} · {self.creado_en}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError(
                "Los registros de auditoría no se pueden modificar."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Los registros de auditoría no se pueden eliminar desde la aplicación."
        )


class Tema(models.Model):
    codigo = models.CharField(max_length=32, unique=True)
    nombre = models.CharField(max_length=255)
    activo = models.BooleanField(
        default=True,
        help_text="Si está deshabilitado, sus criterios no se incluyen en revisiones nuevas.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["codigo"]
        verbose_name = "Tema"
        verbose_name_plural = "Temas"

    def __str__(self) -> str:
        return f"{self.codigo} · {self.nombre}"


class GlosarioTermino(models.Model):
    """Término del glosario mostrado en Indicadores → Glosario."""

    concepto = models.CharField(max_length=255, unique=True)
    descripcion = models.TextField()
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["concepto"]
        verbose_name = "Término de glosario"
        verbose_name_plural = "Términos de glosario"

    def __str__(self) -> str:
        return self.concepto


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
    activo = models.BooleanField(
        default=True,
        help_text="Si está deshabilitado, sus criterios no se incluyen en revisiones nuevas.",
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
    activo = models.BooleanField(
        default=True,
        help_text="Si está deshabilitado, sus criterios no se incluyen en revisiones nuevas.",
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
    activo = models.BooleanField(
        default=True,
        help_text="Si está deshabilitado, no se incluye en revisiones nuevas.",
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


class Pregunta(models.Model):
    """Último eslabón del catálogo: sí/no bajo un criterio."""

    codigo = models.CharField(max_length=32, unique=True)
    texto = models.TextField()
    criterio = models.ForeignKey(
        Criterio,
        on_delete=models.PROTECT,
        related_name="preguntas",
    )
    orden = models.PositiveIntegerField(
        default=0,
        help_text="Orden de presentación dentro del criterio.",
    )
    activo = models.BooleanField(
        default=True,
        help_text="Si está deshabilitada, no se siembra en revisiones nuevas.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["criterio__codigo", "orden", "codigo"]
        verbose_name = "Pregunta"
        verbose_name_plural = "Preguntas"

    def __str__(self) -> str:
        return f"{self.codigo}"


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


class RolDocumentoCriterio(models.TextChoices):
    PRINCIPAL = "PRINCIPAL", "Principal"
    COMPLEMENTARIO = "COMPLEMENTARIO", "Complementario"


class DocumentoCriterio(models.Model):
    """
    Relación estándar (plantilla) documento↔criterio.
    Un criterio tiene un documento principal de búsqueda y, opcionalmente,
    un documento secundario (típico en rol Complementario).
    """

    documento = models.ForeignKey(
        Documento,
        on_delete=models.CASCADE,
        related_name="criterios_estandar",
        help_text="Documento principal donde se busca primero la evidencia del criterio.",
    )
    documento_secundario = models.ForeignKey(
        Documento,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="criterios_estandar_secundarios",
        help_text="Documento complementario opcional (segunda fuente de evidencia).",
    )
    criterio = models.ForeignKey(
        Criterio,
        on_delete=models.CASCADE,
        related_name="documentos_estandar",
    )
    rol = models.CharField(
        max_length=16,
        choices=RolDocumentoCriterio.choices,
        default=RolDocumentoCriterio.PRINCIPAL,
        help_text="Principal = contenido en el documento; Complementario = buscar también en secundario.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["criterio__codigo", "documento__codigo"]
        constraints = [
            models.UniqueConstraint(
                fields=["criterio"],
                name="maestro_documentocriterio_criterio_uniq",
            ),
            models.UniqueConstraint(
                fields=["documento", "criterio"],
                name="maestro_documentocriterio_doc_crit_uniq",
            ),
        ]
        verbose_name = "Relación estándar documento-criterio"
        verbose_name_plural = "Relaciones estándar documento-criterio"

    def __str__(self) -> str:
        sec = f" + {self.documento_secundario.codigo}" if self.documento_secundario_id else ""
        return f"{self.criterio.codigo} · {self.documento.codigo}{sec} ({self.rol})"

    def clean(self) -> None:
        if (
            self.documento_id
            and self.documento_secundario_id
            and self.documento_id == self.documento_secundario_id
        ):
            raise ValidationError(
                {
                    "documento_secundario": (
                        "El documento secundario debe ser distinto del principal."
                    )
                }
            )


class OrigenEmpresaDocumento(models.TextChoices):
    CATALOGO = "CATALOGO", "Catálogo"
    PROPIO = "PROPIO", "Propio"


class EstadoInstalacionRevision(models.TextChoices):
    NUEVA = "NUEVA", "Nueva"
    EXISTENTE = "EXISTENTE", "Existente"


class FaseInstalacionRevision(models.TextChoices):
    PROYECTO = "PROYECTO", "Proyecto"
    CONSTRUCCION = "CONSTRUCCION", "Construcción"
    OPERACION = "OPERACION", "Operación"
    CIERRE = "CIERRE", "Cierre"


class Revision(models.Model):
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="revisiones",
    )
    anio = models.PositiveSmallIntegerField()
    nombre = models.CharField(max_length=255, blank=True)
    instalacion_deposito = models.CharField(
        "Instalación o depósito",
        max_length=255,
        blank=True,
    )
    estado = models.CharField(
        max_length=16,
        choices=EstadoInstalacionRevision.choices,
        blank=True,
        default="",
    )
    fase = models.CharField(
        max_length=16,
        choices=FaseInstalacionRevision.choices,
        blank=True,
        default="",
    )
    capacidad = models.CharField(max_length=128, blank=True)
    vida_util = models.CharField("Vida útil", max_length=128, blank=True)
    fecha_inicio = models.DateField(
        null=True,
        blank=True,
        help_text="Inicio del periodo planificado (curva Plan / Forecast).",
    )
    fecha_fin = models.DateField(
        null=True,
        blank=True,
        help_text="Fin del periodo planificado (meta 100%).",
    )
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

    def delete(self, using=None, keep_parents=False):
        """No se borra: se desactiva para conservar documentos y evidencias."""
        self.activo = False
        self.save(update_fields=["activo", "actualizado_en"])

    def clean(self) -> None:
        if self.fecha_inicio and self.fecha_fin and self.fecha_fin < self.fecha_inicio:
            raise ValidationError(
                {"fecha_fin": "La fecha fin no puede ser anterior a la fecha inicio."}
            )


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
    prioritario = models.BooleanField(
        default=False,
        help_text="Si está marcado, aparece en Documentos prioritarios del dashboard de Indicadores.",
    )
    # Gestión interna del consultor (independiente de PENDIENTE/APROBADO/RECHAZADO).
    revision_texto = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Revisión",
        help_text="Etiqueta definida por el consultor (ej. Iniciado, Rev0, RevA, RevB u otra).",
    )
    revision_porcentaje = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Porcentaje de revisión",
        help_text="Porcentaje interno del documento (0–100). No afecta el avance calculado.",
    )
    fecha_limite_entrega = models.DateField(
        null=True,
        blank=True,
        verbose_name="Fecha límite de entrega",
        help_text=(
            "Plan interno del consultor: fecha objetivo de entrega de este documento. "
            "No genera notificaciones por sí sola."
        ),
    )
    frecuencia_actualizacion = models.CharField(
        max_length=16,
        blank=True,
        choices=[
            ("MENSUAL", "Mensual"),
            ("TRIMESTRAL", "Trimestral"),
            ("SEMESTRAL", "Semestral"),
            ("ANUAL", "Anual"),
            ("BIANUAL", "Cada 2 años"),
            ("OTRO", "Otro / según necesidad"),
        ],
        verbose_name="Frecuencia de actualización",
    )
    frecuencia_otro = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Detalle de frecuencia",
    )
    fecha_ultima_revision = models.DateField(
        null=True,
        blank=True,
        verbose_name="Rev. 0",
    )
    fecha_caducidad = models.DateField(
        null=True,
        blank=True,
        verbose_name="Fecha de caducidad",
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
            models.CheckConstraint(
                condition=(
                    models.Q(revision_porcentaje__isnull=True)
                    | models.Q(revision_porcentaje__gte=0, revision_porcentaje__lte=100)
                ),
                name="maestro_empresadoc_revision_pct_0_100",
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


class EstadoRevisionCriterio(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente"
    APROBADO = "APROBADO", "Aprobado"
    OBSERVADO = "OBSERVADO", "Observado"


class EstadoRevisionPregunta(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente"
    CUMPLE = "CUMPLE", "Cumple"
    NO_CUMPLE = "NO_CUMPLE", "No cumple"


class EmpresaDocumentoCriterio(models.Model):
    """
    Un criterio por revisión, con documento principal y opcionalmente secundario.
    El avance del criterio (preguntas) es único; ambos documentos aportan evidencia.
    """

    empresa_documento = models.ForeignKey(
        EmpresaDocumento,
        on_delete=models.CASCADE,
        related_name="vinculos_criterio",
        help_text="Documento principal de la evidencia del criterio.",
    )
    empresa_documento_secundario = models.ForeignKey(
        EmpresaDocumento,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="vinculos_criterio_secundarios",
        help_text="Documento complementario opcional en esta revisión.",
    )
    revision = models.ForeignKey(
        Revision,
        on_delete=models.CASCADE,
        related_name="vinculos_criterio",
        help_text="Denormalizado: un criterio solo una vez por revisión.",
    )
    criterio = models.ForeignKey(
        Criterio,
        on_delete=models.PROTECT,
        related_name="vinculos_empresa_documento",
    )
    rol = models.CharField(
        max_length=16,
        choices=RolDocumentoCriterio.choices,
        default=RolDocumentoCriterio.PRINCIPAL,
    )
    area = models.ForeignKey(
        Area,
        on_delete=models.PROTECT,
        related_name="vinculos_empresa_documento",
    )
    estado_revision = models.CharField(
        max_length=16,
        choices=EstadoRevisionCriterio.choices,
        default=EstadoRevisionCriterio.PENDIENTE,
        help_text="Aprobación parcial por criterio: solo APROBADO suma al avance.",
    )
    observacion = models.TextField(
        blank=True,
        help_text="Última observación del consultor para este criterio.",
    )
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="criterios_documento_revisados",
    )
    revisado_en = models.DateTimeField(null=True, blank=True)
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
        sec = ""
        if self.empresa_documento_secundario_id:
            sec = f" + {self.empresa_documento_secundario.codigo}"
        return f"{self.empresa_documento.codigo}{sec} → {self.criterio.codigo}"

    def clean(self) -> None:
        if self.empresa_documento_id:
            self.revision_id = self.empresa_documento.revision_id
        if self.empresa_documento_id and self.area_id:
            if self.area.empresa_id != self.empresa_documento.revision.empresa_id:
                raise ValidationError(
                    {"area": "El área debe pertenecer a la misma empresa del documento."}
                )
        if (
            self.empresa_documento_id
            and self.empresa_documento_secundario_id
            and self.empresa_documento_id == self.empresa_documento_secundario_id
        ):
            raise ValidationError(
                {
                    "empresa_documento_secundario": (
                        "El documento secundario debe ser distinto del principal."
                    )
                }
            )
        if self.empresa_documento_secundario_id and self.revision_id:
            if self.empresa_documento_secundario.revision_id != self.revision_id:
                raise ValidationError(
                    {
                        "empresa_documento_secundario": (
                            "El documento secundario debe pertenecer a la misma revisión."
                        )
                    }
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
        skip_seed = kwargs.pop("skip_seed", False)
        super().save(*args, **kwargs)
        if not skip_seed:
            # Idempotente: siembra preguntas activas del catálogo aún no vinculadas.
            sembrar_preguntas_vinculo(self)


class EmpresaDocumentoPregunta(models.Model):
    """Respuesta sí/no de una pregunta dentro del vínculo documento–criterio."""

    vinculo_criterio = models.ForeignKey(
        EmpresaDocumentoCriterio,
        on_delete=models.CASCADE,
        related_name="preguntas",
    )
    revision = models.ForeignKey(
        Revision,
        on_delete=models.CASCADE,
        related_name="preguntas_documento",
        help_text="Denormalizado para unicidad pregunta–revisión.",
    )
    pregunta = models.ForeignKey(
        Pregunta,
        on_delete=models.PROTECT,
        related_name="respuestas_empresa",
    )
    estado_revision = models.CharField(
        max_length=16,
        choices=EstadoRevisionPregunta.choices,
        default=EstadoRevisionPregunta.PENDIENTE,
        help_text="CUMPLE suma al % del criterio; al 100% el criterio se aprueba.",
    )
    observacion = models.TextField(
        blank=True,
        help_text="Observación del consultor cuando la pregunta no cumple.",
    )
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="preguntas_documento_revisadas",
    )
    revisado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pregunta__orden", "pregunta__codigo"]
        constraints = [
            models.UniqueConstraint(
                fields=["revision", "pregunta"],
                name="maestro_empresadocpreg_revision_pregunta_uniq",
            ),
        ]
        verbose_name = "Respuesta a pregunta"
        verbose_name_plural = "Respuestas a preguntas"

    def __str__(self) -> str:
        return f"{self.pregunta.codigo} · {self.estado_revision}"

    def save(self, *args, **kwargs) -> None:
        if self.vinculo_criterio_id:
            self.revision_id = self.vinculo_criterio.revision_id
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


class RevisionCatalogoCriterio(models.Model):
    """
    Snapshot del criterio (y su jerarquía) al crear/abrir la revisión.
    El avance histórico usa estos textos y pesos, no el catálogo vivo.
    """

    revision = models.ForeignKey(
        Revision,
        on_delete=models.CASCADE,
        related_name="catalogo_criterios",
    )
    criterio = models.ForeignKey(
        Criterio,
        on_delete=models.PROTECT,
        related_name="snapshots_revision",
        help_text="Referencia al catálogo vivo (para vínculos documento–criterio).",
    )
    codigo = models.CharField(max_length=32)
    descripcion = models.TextField()
    requisito_codigo = models.CharField(max_length=32)
    requisito_descripcion = models.TextField(blank=True)
    principio_codigo = models.CharField(max_length=32)
    principio_descripcion = models.TextField(blank=True)
    tema_codigo = models.CharField(max_length=32)
    tema_nombre = models.CharField(max_length=255, blank=True)
    ponderacion = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("1"))
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["tema_codigo", "principio_codigo", "requisito_codigo", "codigo"]
        constraints = [
            models.UniqueConstraint(
                fields=["revision", "criterio"],
                name="maestro_rev_cat_criterio_uniq",
            ),
            models.UniqueConstraint(
                fields=["revision", "codigo"],
                name="maestro_rev_cat_criterio_codigo_uniq",
            ),
        ]
        verbose_name = "Snapshot criterio de revisión"
        verbose_name_plural = "Snapshots criterios de revisión"

    def __str__(self) -> str:
        return f"{self.revision} · {self.codigo}"

    @property
    def criterio_id_catalogo(self) -> int:
        return self.criterio_id


class RevisionCatalogoPregunta(models.Model):
    """Snapshot de pregunta del catálogo asociada a un criterio en la revisión."""

    revision_criterio = models.ForeignKey(
        RevisionCatalogoCriterio,
        on_delete=models.CASCADE,
        related_name="preguntas",
    )
    pregunta = models.ForeignKey(
        "Pregunta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="snapshots_revision",
    )
    codigo = models.CharField(max_length=32)
    texto = models.TextField()
    orden = models.PositiveSmallIntegerField(default=0)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["orden", "codigo"]
        constraints = [
            models.UniqueConstraint(
                fields=["revision_criterio", "codigo"],
                name="maestro_rev_cat_pregunta_codigo_uniq",
            ),
        ]
        verbose_name = "Snapshot pregunta de revisión"
        verbose_name_plural = "Snapshots preguntas de revisión"

    def __str__(self) -> str:
        return f"{self.revision_criterio.codigo} · {self.codigo}"


def criterios_deshabilitados_en_catalogo():
    """
    Criterios que no deben entrar en revisiones nuevas:
    el propio criterio o su requisito/principio/tema están deshabilitados.
    No afecta revisiones ya creadas (histórico / en curso).
    """
    return Criterio.objects.filter(
        Q(activo=False)
        | Q(requisito__activo=False)
        | Q(principio__activo=False)
        | Q(tema__activo=False)
    )


def aplicar_deshabilitados_catalogo_a_revision(revision: "Revision") -> int:
    """
    Al crear una revisión: marca como no aplicables los criterios deshabilitados
    en catálogo. Las revisiones existentes no se tocan.
    """
    motivo = "Deshabilitado en el catálogo GISTM al crear la revisión."
    creados = 0
    for criterio_id in criterios_deshabilitados_en_catalogo().values_list("pk", flat=True):
        _, created = RevisionCriterioDesactivado.objects.get_or_create(
            revision=revision,
            criterio_id=criterio_id,
            defaults={"motivo": motivo},
        )
        if created:
            creados += 1
    return creados


def crear_snapshot_catalogo_revision(
    revision: "Revision", *, force: bool = False
) -> int:
    """
    Congela criterios/preguntas aplicables del catálogo en la revisión.
    Por defecto no regenera si ya hay snapshot (force=True lo reemplaza).
    """
    if force:
        revision.catalogo_criterios.all().delete()
    elif revision.catalogo_criterios.exists():
        return 0

    deshabilitados = set(
        criterios_deshabilitados_en_catalogo().values_list("pk", flat=True)
    )
    criterios = (
        Criterio.objects.exclude(pk__in=deshabilitados)
        .select_related("requisito", "principio", "tema")
        .prefetch_related("preguntas")
        .order_by("codigo")
    )
    creados = 0
    for criterio in criterios:
        snap = RevisionCatalogoCriterio.objects.create(
            revision=revision,
            criterio=criterio,
            codigo=criterio.codigo,
            descripcion=criterio.descripcion or "",
            requisito_codigo=criterio.requisito.codigo,
            requisito_descripcion=criterio.requisito.descripcion or "",
            principio_codigo=criterio.principio.codigo,
            principio_descripcion=criterio.principio.descripcion or "",
            tema_codigo=criterio.tema.codigo,
            tema_nombre=criterio.tema.nombre or "",
            ponderacion=criterio.ponderacion or Decimal("1"),
        )
        creados += 1
        for pregunta in criterio.preguntas.filter(activo=True).order_by(
            "orden", "codigo"
        ):
            RevisionCatalogoPregunta.objects.create(
                revision_criterio=snap,
                pregunta=pregunta,
                codigo=pregunta.codigo,
                texto=pregunta.texto or "",
                orden=pregunta.orden or 0,
            )
    return creados


def asegurar_snapshot_catalogo_revision(revision: "Revision") -> int:
    """Crea snapshot si falta (revisiones antiguas / primer acceso)."""
    if revision.catalogo_criterios.exists():
        return 0
    return crear_snapshot_catalogo_revision(revision)


def aplicar_estandar_a_revision(
    revision: "Revision",
    area: "Area",
) -> tuple[int, int]:
    """
    Activa documentos del catálogo estándar (principal y secundarios) y crea
    vínculos EmpresaDocumentoCriterio (un criterio por revisión, con rol y
    documento secundario opcional). Siembra preguntas.
    Devuelve (documentos_creados, vinculos_creados).
    """
    if area.empresa_id != revision.empresa_id:
        raise ValidationError("El área debe pertenecer a la empresa de esta revisión.")

    relaciones = list(
        DocumentoCriterio.objects.select_related(
            "documento", "documento_secundario", "criterio"
        ).order_by("id")
    )
    doc_ids_estandar: set[int] = set()
    for rel in relaciones:
        doc_ids_estandar.add(rel.documento_id)
        if rel.documento_secundario_id:
            doc_ids_estandar.add(rel.documento_secundario_id)

    docs_creados = 0
    vinculos_creados = 0

    for documento in Documento.objects.filter(pk__in=doc_ids_estandar):
        _, created = EmpresaDocumento.objects.get_or_create(
            revision=revision,
            documento=documento,
            defaults={
                "codigo": documento.codigo,
                "nombre": documento.nombre,
                "tipo_documento": documento.tipo_documento,
                "origen": OrigenEmpresaDocumento.CATALOGO,
                "activo": True,
            },
        )
        if created:
            docs_creados += 1
        else:
            EmpresaDocumento.objects.filter(
                revision=revision, documento=documento, activo=False
            ).update(activo=True)

    emp_docs = {
        ed.documento_id: ed
        for ed in EmpresaDocumento.objects.filter(
            revision=revision, documento_id__in=doc_ids_estandar
        )
    }
    desactivados_ids = set(
        RevisionCriterioDesactivado.objects.filter(revision=revision).values_list(
            "criterio_id", flat=True
        )
    )
    vinculos_existentes = {
        v.criterio_id: v
        for v in EmpresaDocumentoCriterio.objects.filter(revision=revision)
    }
    for rel in relaciones:
        if rel.criterio_id in desactivados_ids:
            continue
        emp_doc = emp_docs.get(rel.documento_id)
        if emp_doc is None:
            continue
        emp_sec = None
        if rel.documento_secundario_id:
            emp_sec = emp_docs.get(rel.documento_secundario_id)
        existente = vinculos_existentes.get(rel.criterio_id)
        if existente is not None:
            # Sincroniza rol/secundario desde la plantilla sin mover el principal
            # ni el área ya asignados en la revisión.
            updates: dict = {}
            if existente.rol != rel.rol:
                updates["rol"] = rel.rol
            if existente.empresa_documento_secundario_id != (
                emp_sec.pk if emp_sec else None
            ):
                updates["empresa_documento_secundario"] = emp_sec
            if updates:
                for field, value in updates.items():
                    setattr(existente, field, value)
                existente.save(update_fields=[*updates.keys(), "actualizado_en"])
            continue
        _, created = EmpresaDocumentoCriterio.objects.get_or_create(
            revision=revision,
            criterio=rel.criterio,
            defaults={
                "empresa_documento": emp_doc,
                "empresa_documento_secundario": emp_sec,
                "rol": rel.rol,
                "area": area,
            },
        )
        if created:
            vinculos_creados += 1
            vinculos_existentes[rel.criterio_id] = _

    return docs_creados, vinculos_creados


def documento_carga_upload_to(instance: "DocumentoCarga", filename: str) -> str:
    """Ruta única por versión para no sobrescribir archivos previos en Blob Storage."""
    from uuid import uuid4

    revision_id = instance.empresa_documento.revision_id
    safe = Path(filename).name
    return (
        f"revisiones/{revision_id}/documentos/{instance.empresa_documento_id}/"
        f"{uuid4().hex[:10]}_{safe}"
    )


class EstadoRevisionCarga(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente de revisión"
    PARCIAL = "PARCIAL", "Aprobación parcial"
    APROBADO = "APROBADO", "Aprobado"
    RECHAZADO = "RECHAZADO", "Rechazado"


class DocumentoCarga(models.Model):
    """Evidencia del cliente: archivo en Blob o URL externa."""

    empresa_documento = models.ForeignKey(
        EmpresaDocumento,
        on_delete=models.CASCADE,
        related_name="cargas",
    )
    archivo = models.FileField(
        upload_to=documento_carga_upload_to,
        blank=True,
        null=True,
        help_text="Archivo en Blob. Vacío si la evidencia es solo una URL.",
    )
    url_evidencia = models.URLField(
        max_length=1000,
        blank=True,
        default="",
        verbose_name="URL de evidencia",
        help_text="Enlace externo cuando no se sube archivo.",
    )
    nombre_original = models.CharField(max_length=255, blank=True)
    version = models.PositiveIntegerField(
        default=1,
        help_text="Número de versión correlativo (1, 2, 3…). No se borran archivos previos.",
    )
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
        help_text="La carga vigente es la más reciente con activo=True. Las anteriores quedan como historial.",
    )
    estado_revision = models.CharField(
        max_length=16,
        choices=EstadoRevisionCarga.choices,
        default=EstadoRevisionCarga.PENDIENTE,
        help_text=(
            "APROBADO = todos los criterios del documento aprobados; "
            "PARCIAL = algunos criterios aprobados; el avance de criterios "
            "usa el estado de cada vínculo documento–criterio."
        ),
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
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(archivo__isnull=False) & ~models.Q(archivo="")
                )
                | ~models.Q(url_evidencia=""),
                name="maestro_documentocarga_archivo_o_url",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.empresa_documento.codigo} · {self.etiqueta_evidencia}"

    @property
    def es_url(self) -> bool:
        return bool((self.url_evidencia or "").strip())

    @property
    def evidencia_url(self) -> str | None:
        """URL para abrir la evidencia (archivo en Blob o enlace externo)."""
        url = (self.url_evidencia or "").strip()
        if url:
            return url
        if self.archivo:
            try:
                return self.archivo.url
            except ValueError:
                return None
        return None

    @property
    def etiqueta_evidencia(self) -> str:
        if (self.nombre_original or "").strip():
            return self.nombre_original.strip()
        if (self.url_evidencia or "").strip():
            return self.url_evidencia.strip()
        if self.archivo:
            return Path(self.archivo.name).name
        return "evidencia"

    @property
    def esta_pendiente(self) -> bool:
        return self.estado_revision == EstadoRevisionCarga.PENDIENTE

    @property
    def esta_parcial(self) -> bool:
        return self.estado_revision == EstadoRevisionCarga.PARCIAL

    @property
    def esta_aprobado(self) -> bool:
        return self.estado_revision == EstadoRevisionCarga.APROBADO

    @property
    def esta_rechazado(self) -> bool:
        return self.estado_revision == EstadoRevisionCarga.RECHAZADO

    def save(self, *args, **kwargs) -> None:
        if self.archivo and not self.nombre_original:
            self.nombre_original = Path(self.archivo.name).name
        elif self.url_evidencia and not self.nombre_original:
            self.nombre_original = (self.url_evidencia or "").strip()[:255]
        # Asignar versión correlativa en altas nuevas (no borra blobs previos).
        if self._state.adding and self.empresa_documento_id:
            ultima = (
                DocumentoCarga.objects.filter(
                    empresa_documento_id=self.empresa_documento_id
                )
                .order_by("-version")
                .values_list("version", flat=True)
                .first()
            )
            self.version = int(ultima or 0) + 1
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
    VERSION = "VERSION", "Nueva versión de archivo"
    ACUERDO = "ACUERDO", "Acuerdo"
    SISTEMA = "SISTEMA", "Sistema"


class DocumentoCaducidadEnvio(models.Model):
    """Evita duplicar el mismo aviso de caducidad el mismo día."""

    empresa_documento = models.ForeignKey(
        EmpresaDocumento,
        on_delete=models.CASCADE,
        related_name="caducidad_envios",
    )
    tipo = models.CharField(max_length=8, choices=TipoNotificacionAcuerdo.choices)
    dias = models.PositiveIntegerField()
    dia_referencia = models.DateField()
    enviado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-enviado_en"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa_documento", "tipo", "dias", "dia_referencia"],
                name="maestro_doc_caducidad_envio_uniq",
            ),
        ]
        verbose_name = "Envío de caducidad de documento"
        verbose_name_plural = "Envíos de caducidad de documentos"

    def __str__(self) -> str:
        return (
            f"{self.empresa_documento_id} · {self.tipo}/{self.dias} · "
            f"{self.dia_referencia}"
        )


class AlcanceActividad(models.TextChoices):
    PERSONAL = "PERSONAL", "Solo consultor"
    CON_CLIENTE = "CON_CLIENTE", "Con empresa / cliente"


class TipoActividad(models.TextChoices):
    GENERAL = "GENERAL", "General"
    ACUERDO = "ACUERDO", "Acuerdo"
    RECORDATORIO = "RECORDATORIO", "Recordatorio"


class EstadoActividad(models.TextChoices):
    PROGRAMADA = "PROGRAMADA", "Programada"
    PASADA = "PASADA", "Pasada"
    COMPLETADA = "COMPLETADA", "Completada"
    # Cierre sin ejecución (anulación o vencida sin realizarse).
    CANCELADA = "CANCELADA", "No se llevó a cabo"


class Actividad(models.Model):
    """Tarea o reunión programada por el consultor (personal o con una empresa)."""

    titulo = models.CharField(max_length=255)
    descripcion = models.TextField(
        blank=True,
        help_text="Tenor / agenda / notas de la actividad.",
    )
    tipo = models.CharField(
        max_length=16,
        choices=TipoActividad.choices,
        default=TipoActividad.GENERAL,
    )
    alcance = models.CharField(
        max_length=16,
        choices=AlcanceActividad.choices,
        default=AlcanceActividad.PERSONAL,
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="actividades",
        help_text="Obligatoria si la actividad es con el cliente.",
    )
    area = models.ForeignKey(
        Area,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="actividades",
        verbose_name="Área responsable",
        help_text="Área de la empresa configurada en el maestro.",
    )
    responsable = models.ForeignKey(
        Responsable,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="actividades",
        help_text="Responsable del área seleccionada.",
    )
    actividad_padre = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="hijas",
        help_text="Actividad origen (reunión) de la que nace un acuerdo o recordatorio.",
    )
    inicio = models.DateTimeField()
    fin = models.DateTimeField(null=True, blank=True)
    enlace_reunion = models.URLField(
        blank=True,
        help_text="Enlace externo (Teams, Meet, Zoom, etc.).",
    )
    documentos = models.ManyToManyField(
        EmpresaDocumento,
        blank=True,
        related_name="actividades",
    )
    enviar_correo = models.BooleanField(
        default=False,
        help_text="Se marca automáticamente cuando el alcance es con cliente.",
    )
    correo_enviado_en = models.DateTimeField(null=True, blank=True)
    ultimo_dias_antes_aviso = models.IntegerField(
        null=True,
        blank=True,
        help_text="Último umbral (días antes del vencimiento) por el que ya se generó aviso.",
    )
    estado = models.CharField(
        max_length=16,
        choices=EstadoActividad.choices,
        default=EstadoActividad.PROGRAMADA,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="actividades_creadas",
    )
    asignado_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="actividades_asignadas",
        help_text="Consultor responsable de ejecutar la actividad.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["inicio"]
        verbose_name = "Actividad"
        verbose_name_plural = "Actividades"

    def __str__(self) -> str:
        return f"{self.titulo} · {self.inicio:%d/%m/%Y %H:%M}"

    @property
    def es_con_cliente(self) -> bool:
        return self.alcance == AlcanceActividad.CON_CLIENTE

    @property
    def es_acuerdo(self) -> bool:
        return self.tipo == TipoActividad.ACUERDO

    @property
    def es_recordatorio(self) -> bool:
        return self.tipo == TipoActividad.RECORDATORIO

    def clean(self) -> None:
        if self.alcance == AlcanceActividad.CON_CLIENTE and not self.empresa_id:
            raise ValidationError({"empresa": "Selecciona la empresa del cliente."})
        if self.fin and self.inicio and self.fin < self.inicio:
            raise ValidationError({"fin": "La hora de fin no puede ser anterior al inicio."})
        if self.area_id and not self.empresa_id:
            raise ValidationError(
                {"area": "Selecciona una empresa para asignar el área responsable."}
            )
        if self.area_id and self.empresa_id and self.area.empresa_id != self.empresa_id:
            raise ValidationError(
                {"area": "El área debe pertenecer a la empresa de la actividad."}
            )
        if self.responsable_id and not self.area_id:
            raise ValidationError(
                {"responsable": "Selecciona el área para asignar un responsable."}
            )
        if (
            self.responsable_id
            and self.area_id
            and self.responsable.area_id != self.area_id
        ):
            raise ValidationError(
                {"responsable": "El responsable debe pertenecer al área seleccionada."}
            )


def fecha_vencimiento_actividad(actividad: Actividad):
    """Fecha local de vencimiento: fin, o inicio si no hay fin."""
    ref = actividad.fin or actividad.inicio
    if ref is None:
        return None
    return timezone.localtime(ref).date()


def sincronizar_acuerdos_vencidos() -> list[int]:
    """
    Pasa a estado VENCIDO los acuerdos ABIERTO cuya fecha de compromiso
    ya quedó en un día anterior al actual. Devuelve los IDs actualizados.
    """
    hoy = timezone.localdate()
    ids: list[int] = []
    qs = Acuerdo.objects.filter(
        estado=EstadoAcuerdo.ABIERTO,
        fecha_compromiso__isnull=False,
    ).only("id", "fecha_compromiso")
    for acuerdo in qs.iterator():
        ref = acuerdo.fecha_compromiso
        if timezone.is_aware(ref):
            dia = timezone.localtime(ref).date()
        else:
            dia = ref.date() if hasattr(ref, "date") else ref
        if dia is not None and dia < hoy:
            ids.append(acuerdo.pk)
    if ids:
        Acuerdo.objects.filter(pk__in=ids).update(
            estado=EstadoAcuerdo.VENCIDO,
            actualizado_en=timezone.now(),
        )
    return ids


def sincronizar_actividades_pasadas(*, creado_por_id: int | None = None) -> list[int]:
    """
    Pasa a estado PASADA las actividades PROGRAMADA cuya fecha fin
    (o inicio si no hay fin) ya quedó en un día anterior al actual.
    Devuelve los IDs actualizados.
    """
    hoy = timezone.localdate()
    qs = Actividad.objects.filter(estado=EstadoActividad.PROGRAMADA)
    if creado_por_id is not None:
        qs = qs.filter(creado_por_id=creado_por_id)
    ids: list[int] = []
    for act in qs.only("id", "inicio", "fin"):
        venc = fecha_vencimiento_actividad(act)
        if venc is not None and venc < hoy:
            ids.append(act.pk)
    if not ids:
        return []
    Actividad.objects.filter(pk__in=ids).update(
        estado=EstadoActividad.PASADA,
        actualizado_en=timezone.now(),
    )
    return ids


class EstadoAcuerdo(models.TextChoices):
    ABIERTO = "ABIERTO", "Abierto"
    VENCIDO = "VENCIDO", "Vencido"
    CUMPLIDO = "CUMPLIDO", "Cumplido"
    CANCELADO = "CANCELADO", "Cancelado"


class Acuerdo(models.Model):
    """
    Compromiso derivado de una actividad (no es una actividad del calendario).
    Puede asociar documentos y/o un texto libre.
    """

    actividad = models.ForeignKey(
        Actividad,
        on_delete=models.CASCADE,
        related_name="acuerdos",
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="acuerdos",
        null=True,
        blank=True,
    )
    titulo = models.CharField(max_length=255)
    descripcion = models.TextField(
        blank=True,
        help_text="Qué se acordó realizar.",
    )
    fecha_compromiso = models.DateTimeField(null=True, blank=True)
    documentos = models.ManyToManyField(
        EmpresaDocumento,
        blank=True,
        related_name="acuerdos",
    )
    estado = models.CharField(
        max_length=16,
        choices=EstadoAcuerdo.choices,
        default=EstadoAcuerdo.ABIERTO,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="acuerdos_creados",
    )
    correo_enviado_en = models.DateTimeField(null=True, blank=True)
    ultimo_recordatorio_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Acuerdo"
        verbose_name_plural = "Acuerdos"

    def __str__(self) -> str:
        return self.titulo

    @property
    def es_solo_texto(self) -> bool:
        return not self.documentos.exists()


class TipoAvisoConsultor(models.TextChoices):
    CARGA = "CARGA", "Nueva evidencia"
    MENSAJE = "MENSAJE", "Mensaje del cliente"
    DOCUMENTO_PROPIO = "DOCUMENTO_PROPIO", "Documento del cliente"
    ACTIVIDAD_PROXIMA = "ACTIVIDAD_PROXIMA", "Actividad por vencer"
    ACTIVIDAD_PASADA = "ACTIVIDAD_PASADA", "Actividad pasada"


class AvisoConsultor(models.Model):
    """Aviso in-app para gestores/administradores (cargas, mensajes, docs propios)."""

    destinatario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="avisos_consultor",
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="avisos_consultor",
    )
    tipo = models.CharField(max_length=24, choices=TipoAvisoConsultor.choices)
    titulo = models.CharField(max_length=255)
    detalle = models.TextField(blank=True)
    url = models.CharField(max_length=500, blank=True)
    empresa_documento = models.ForeignKey(
        EmpresaDocumento,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="avisos_consultor",
    )
    carga = models.ForeignKey(
        DocumentoCarga,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="avisos_consultor",
    )
    leido = models.BooleanField(default=False)
    leido_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Aviso al consultor"
        verbose_name_plural = "Avisos al consultor"
        indexes = [
            models.Index(fields=["destinatario", "leido", "-creado_en"]),
        ]

    def __str__(self) -> str:
        estado = "leído" if self.leido else "nuevo"
        return f"{self.titulo} · {estado}"


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
    criterio = models.ForeignKey(
        Criterio,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mensajes_documento",
        help_text="Criterio al que apunta la observación (aprobación parcial).",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["creado_en"]
        verbose_name = "Mensaje de documento"
        verbose_name_plural = "Mensajes de documento"

    def __str__(self) -> str:
        lado = "Consultor" if self.es_consultor else "Cliente"
        return f"{self.empresa_documento.codigo} · {lado} · {self.creado_en:%d/%m/%Y}"


def sincronizar_estado_carga_documento(
    empresa_documento: EmpresaDocumento,
    *,
    revisado_por=None,
) -> DocumentoCarga | None:
    """
    Ajusta el estado de la carga activa según los vínculos criterio:
    todos APROBADO → APROBADO; algunos APROBADO → PARCIAL; ninguno → PENDIENTE
    (si no está RECHAZADO sin aprobados).
    """
    carga = (
        DocumentoCarga.objects.filter(empresa_documento=empresa_documento, activo=True)
        .order_by("-creado_en")
        .first()
    )
    if carga is None:
        return None

    estados = list(
        EmpresaDocumentoCriterio.objects.filter(
            empresa_documento=empresa_documento
        ).values_list("estado_revision", flat=True)
    )
    if not estados:
        # Documento sin criterios: no cambiar lógica especial aquí.
        return carga

    total = len(estados)
    aprobados = sum(1 for e in estados if e == EstadoRevisionCriterio.APROBADO)

    if aprobados == total:
        nuevo = EstadoRevisionCarga.APROBADO
    elif aprobados > 0:
        nuevo = EstadoRevisionCarga.PARCIAL
    elif carga.estado_revision == EstadoRevisionCarga.RECHAZADO:
        nuevo = EstadoRevisionCarga.RECHAZADO
    else:
        nuevo = EstadoRevisionCarga.PENDIENTE

    if carga.estado_revision != nuevo:
        carga.estado_revision = nuevo
        update_fields = ["estado_revision"]
        if revisado_por is not None:
            carga.revisado_por = revisado_por
            carga.revisado_en = timezone.now()
            update_fields.extend(["revisado_por", "revisado_en"])
        elif nuevo in (EstadoRevisionCarga.APROBADO, EstadoRevisionCarga.PARCIAL):
            carga.revisado_en = timezone.now()
            update_fields.append("revisado_en")
        if nuevo != EstadoRevisionCarga.RECHAZADO:
            carga.motivo_rechazo = ""
            update_fields.append("motivo_rechazo")
        carga.save(update_fields=update_fields)
    return carga


def resetear_criterios_no_aprobados(empresa_documento: EmpresaDocumento) -> int:
    """Al subir nueva evidencia: OBSERVADO/PENDIENTE → PENDIENTE; APROBADO se mantiene."""
    n = (
        EmpresaDocumentoCriterio.objects.filter(empresa_documento=empresa_documento)
        .exclude(estado_revision=EstadoRevisionCriterio.APROBADO)
        .update(
            estado_revision=EstadoRevisionCriterio.PENDIENTE,
            observacion="",
            revisado_por=None,
            revisado_en=None,
        )
    )
    resetear_preguntas_no_cumplidas(empresa_documento)
    return n


def sembrar_preguntas_vinculo(vinculo: "EmpresaDocumentoCriterio") -> int:
    """Crea respuestas PENDIENTE para preguntas activas del criterio aún no sembradas."""
    creados = 0
    for pregunta in Pregunta.objects.filter(
        criterio_id=vinculo.criterio_id, activo=True
    ).order_by("orden", "codigo"):
        _, created = EmpresaDocumentoPregunta.objects.get_or_create(
            revision_id=vinculo.revision_id,
            pregunta=pregunta,
            defaults={"vinculo_criterio": vinculo},
        )
        if created:
            creados += 1
    return creados


def sincronizar_estado_criterio_desde_preguntas(
    vinculo: "EmpresaDocumentoCriterio",
    *,
    revisado_por=None,
) -> "EmpresaDocumentoCriterio":
    """
    Deriva el estado del criterio desde sus preguntas:
    - sin preguntas → no APROBADO (PENDIENTE)
    - todas CUMPLE → APROBADO
    - alguna NO_CUMPLE → OBSERVADO
    - resto → PENDIENTE
    """
    estados = list(
        EmpresaDocumentoPregunta.objects.filter(vinculo_criterio=vinculo).values_list(
            "estado_revision", flat=True
        )
    )
    if not estados:
        nuevo = EstadoRevisionCriterio.PENDIENTE
        obs = ""
    elif all(e == EstadoRevisionPregunta.CUMPLE for e in estados):
        nuevo = EstadoRevisionCriterio.APROBADO
        obs = ""
    elif any(e == EstadoRevisionPregunta.NO_CUMPLE for e in estados):
        nuevo = EstadoRevisionCriterio.OBSERVADO
        obs = vinculo.observacion or ""
    else:
        nuevo = EstadoRevisionCriterio.PENDIENTE
        obs = ""

    update_fields = ["actualizado_en"]
    if vinculo.estado_revision != nuevo:
        vinculo.estado_revision = nuevo
        update_fields.append("estado_revision")
    if nuevo == EstadoRevisionCriterio.APROBADO and vinculo.observacion:
        vinculo.observacion = ""
        update_fields.append("observacion")
    elif nuevo == EstadoRevisionCriterio.OBSERVADO and obs != vinculo.observacion:
        vinculo.observacion = obs
        update_fields.append("observacion")
    if revisado_por is not None:
        vinculo.revisado_por = revisado_por
        vinculo.revisado_en = timezone.now()
        update_fields.extend(["revisado_por", "revisado_en"])
    elif nuevo == EstadoRevisionCriterio.APROBADO and "estado_revision" in update_fields:
        vinculo.revisado_en = timezone.now()
        update_fields.append("revisado_en")
    if len(update_fields) > 1:
        vinculo.save(update_fields=update_fields, skip_seed=True)
    return vinculo


def resetear_preguntas_no_cumplidas(empresa_documento: EmpresaDocumento) -> int:
    """Al subir nueva evidencia: NO_CUMPLE/PENDIENTE → PENDIENTE; CUMPLE se mantiene."""
    n = (
        EmpresaDocumentoPregunta.objects.filter(
            vinculo_criterio__empresa_documento=empresa_documento
        )
        .exclude(estado_revision=EstadoRevisionPregunta.CUMPLE)
        .update(
            estado_revision=EstadoRevisionPregunta.PENDIENTE,
            observacion="",
            revisado_por=None,
            revisado_en=None,
        )
    )
    for vinculo in EmpresaDocumentoCriterio.objects.filter(
        empresa_documento=empresa_documento
    ):
        sincronizar_estado_criterio_desde_preguntas(vinculo)
    return n


def pct_preguntas_vinculo(vinculo: "EmpresaDocumentoCriterio") -> tuple[int, int, float]:
    """Devuelve (cumplen, total, pct) de preguntas del vínculo."""
    estados = list(
        EmpresaDocumentoPregunta.objects.filter(vinculo_criterio=vinculo).values_list(
            "estado_revision", flat=True
        )
    )
    total = len(estados)
    if total == 0:
        return 0, 0, 0.0
    ok = sum(1 for e in estados if e == EstadoRevisionPregunta.CUMPLE)
    return ok, total, round(100.0 * ok / total, 1)


def ponderaciones_revision(revision: Revision) -> dict[int, Decimal]:
    """
    Ponderación efectiva por criterio en la revisión: 1/N entre criterios
    del snapshot activos (no desactivados) del mismo requisito (código congelado).
    """
    asegurar_snapshot_catalogo_revision(revision)
    desactivados = set(
        RevisionCriterioDesactivado.objects.filter(revision=revision).values_list(
            "criterio_id", flat=True
        )
    )
    snaps = (
        RevisionCatalogoCriterio.objects.filter(revision=revision)
        .exclude(criterio_id__in=desactivados)
        .order_by("requisito_codigo", "codigo")
    )
    por_requisito: dict[str, list[int]] = {}
    for snap in snaps:
        por_requisito.setdefault(snap.requisito_codigo, []).append(snap.criterio_id)

    resultado: dict[int, Decimal] = {}
    for ids in por_requisito.values():
        n = len(ids)
        if n == 0:
            continue
        peso = (Decimal("1") / Decimal(n)).quantize(Decimal("0.0001"))
        for criterio_id in ids:
            resultado[criterio_id] = peso
    return resultado

