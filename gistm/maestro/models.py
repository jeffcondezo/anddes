from __future__ import annotations

from django.conf import settings
from django.db import models


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
