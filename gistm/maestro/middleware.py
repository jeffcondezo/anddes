from __future__ import annotations

import secrets

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


def _ensure_csp_nonce(request) -> str:
    nonce = getattr(request, "csp_nonce", None)
    if not nonce:
        nonce = secrets.token_urlsafe(16)
        request.csp_nonce = nonce
    return nonce


def build_csp_header(nonce: str) -> str:
    """
    script-src / style-src sin 'unsafe-inline': nonces + hosts permitidos.
    Los style=\"...\" de UI se migraron a clases (csp_inline.css).
    Plantillas email/ conservan inline (solo se usan al enviar correo, no HTTP).
    """
    return (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'self'; "
        "form-action 'self' https://login.microsoftonline.com; "
        f"script-src 'self' 'nonce-{nonce}'; "
        f"style-src 'self' 'nonce-{nonce}'; "
        "font-src 'self' data:; "
        "img-src 'self' data: blob: https:; "
        "connect-src 'self' https://login.microsoftonline.com "
        "https://*.blob.core.windows.net; "
        "media-src 'self' https://*.blob.core.windows.net blob:; "
        "worker-src 'self' blob:;"
    )


class ContentSecurityPolicyMiddleware:
    """Nonce por request + Content-Security-Policy cuando CSP_ENABLED."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _ensure_csp_nonce(request)
        response = self.get_response(request)
        if not getattr(settings, "CSP_ENABLED", False):
            return response
        if "Content-Security-Policy" not in response:
            custom = (getattr(settings, "CSP_POLICY", None) or "").strip()
            if custom:
                response["Content-Security-Policy"] = custom.replace(
                    "{nonce}", request.csp_nonce
                )
            else:
                response["Content-Security-Policy"] = build_csp_header(request.csp_nonce)
        return response


class EntraLoginRequiredMiddleware:
    """Redirect unauthenticated users to Entra login when AZURE_AD_REQUIRE_LOGIN is enabled."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt_prefixes = ("/static/",)
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
