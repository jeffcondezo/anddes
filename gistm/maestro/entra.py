"""Microsoft Entra ID helpers using MSAL confidential client."""

from __future__ import annotations

import secrets
from typing import Any

import msal
from django.conf import settings


GRAPH_SCOPES = ["User.Read"]


def build_msal_app(cache: msal.SerializableTokenCache | None = None) -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.AZURE_AD_CLIENT_ID,
        client_credential=settings.AZURE_AD_CLIENT_SECRET,
        authority=settings.AZURE_AD_AUTHORITY,
        token_cache=cache,
    )


def build_auth_url(state: str) -> str:
    app = build_msal_app()
    return app.get_authorization_request_url(
        scopes=GRAPH_SCOPES,
        state=state,
        redirect_uri=settings.AZURE_AD_REDIRECT_URI,
        prompt="select_account",
    )


def exchange_code_for_token(code: str) -> dict[str, Any]:
    app = build_msal_app()
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=GRAPH_SCOPES,
        redirect_uri=settings.AZURE_AD_REDIRECT_URI,
    )
    if "error" in result:
        raise RuntimeError(
            f"{result.get('error')}: {result.get('error_description', 'unknown error')}"
        )
    return result


def new_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def claims_from_id_token(result: dict[str, Any]) -> dict[str, Any]:
    claims = result.get("id_token_claims") or {}
    return claims
