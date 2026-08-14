from __future__ import annotations

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class EntraLoginRequiredMiddleware:
    """Redirect unauthenticated users to Entra login when AZURE_AD_REQUIRE_LOGIN is enabled."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt_prefixes = (
            "/static/",
            "/admin/login/",
        )
        self.exempt_names = {
            "maestro:login",
            "maestro:entra_login",
            "maestro:entra_callback",
            "maestro:logout",
        }

    def __call__(self, request):
        if not settings.AZURE_AD_REQUIRE_LOGIN:
            return self.get_response(request)

        path = request.path
        if any(path.startswith(prefix) for prefix in self.exempt_prefixes):
            return self.get_response(request)

        if path.startswith("/admin/") and request.user.is_authenticated and request.user.is_staff:
            return self.get_response(request)

        try:
            exempt_paths = {reverse(name) for name in self.exempt_names}
        except Exception:  # noqa: BLE001
            exempt_paths = set()

        if path in exempt_paths or path.rstrip("/") + "/" in exempt_paths:
            return self.get_response(request)

        if not request.user.is_authenticated:
            login_url = reverse("maestro:login")
            if path not in ("/", ""):
                return redirect(f"{login_url}?next={path}")
            return redirect(login_url)

        return self.get_response(request)
