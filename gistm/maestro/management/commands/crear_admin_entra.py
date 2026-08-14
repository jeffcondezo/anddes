from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from maestro.models import PerfilUsuario, TipoUsuario

User = get_user_model()


class Command(BaseCommand):
    help = "Crea o actualiza un usuario Administrador pre-registrado para login con Entra ID."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            required=True,
            help="Correo corporativo que usará Entra ID para autenticarse.",
        )
        parser.add_argument("--first-name", default="", help="Nombres (opcional).")
        parser.add_argument("--last-name", default="", help="Apellidos (opcional).")

    @transaction.atomic
    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        if not email or "@" not in email:
            raise CommandError("Debes indicar un correo válido con --email.")

        first_name = (options.get("first_name") or "").strip()
        last_name = (options.get("last_name") or "").strip()

        user = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).first()
        created_user = False
        if user is None:
            user = User(username=email, email=email, first_name=first_name, last_name=last_name, is_active=True)
            user.set_unusable_password()
            user.save()
            created_user = True
        else:
            user.email = email
            user.username = email
            user.is_active = True
            if first_name:
                user.first_name = first_name
            if last_name:
                user.last_name = last_name
            user.save()

        perfil, created_perfil = PerfilUsuario.objects.get_or_create(
            user=user,
            defaults={
                "tipo": TipoUsuario.ADMINISTRADOR,
                "activo": True,
            },
        )
        if not created_perfil:
            perfil.tipo = TipoUsuario.ADMINISTRADOR
            perfil.activo = True
            perfil.empresa = None
            perfil.save()
            perfil.empresas.clear()

        action = "creado" if created_user or created_perfil else "actualizado"
        self.stdout.write(self.style.SUCCESS(f"Administrador {action}: {email}"))
