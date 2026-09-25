import os
from typing import Any

import streamlit as st

DEFAULT_OAUTH_SCOPE = "https://graph.microsoft.com/.default"
DEFAULT_REDIRECT_URI = "http://localhost:8501/"
DEFAULT_GLEAN_CHAT_PATH = "/rest/api/v1/chat"


def _secret_or_env(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    value = os.environ.get(name)
    return value or None


def glean_chat_url(server_url: str) -> str:
    url = server_url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    url = url.rstrip("/")
    if url.endswith(DEFAULT_GLEAN_CHAT_PATH):
        return url
    return f"{url}{DEFAULT_GLEAN_CHAT_PATH}"


def oauth_redirect_uri() -> str:
    """Use this Streamlit origin so Microsoft returns the code to the running app."""
    from urllib.parse import urlparse

    try:
        raw = str(getattr(st.context, "url", "") or "")
    except Exception:
        raw = ""
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/"
    return _secret_or_env("AZURE_REDIRECT_URI") or DEFAULT_REDIRECT_URI


def _microsoft_oauth_urls(tenant_id: str) -> tuple[str, str]:
    base = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0"
    return f"{base}/authorize", f"{base}/token"


def glean_oauth_config() -> dict[str, str] | None:
    client_id = _secret_or_env("GLEAN_CLIENT_ID")
    client_secret = _secret_or_env("GLEAN_CLIENT_SECRET")
    server_url = _secret_or_env("GLEAN_SERVER_URL")
    tenant_id = _secret_or_env("AZURE_TENANT_ID") or _secret_or_env("MICROSOFT_TENANT_ID")
    if not client_id or not client_secret or not server_url or not tenant_id:
        return None

    authorize_url, token_url = _microsoft_oauth_urls(tenant_id)
    config = {
        "client_id": client_id,
        "client_secret": client_secret,
        "tenant_id": tenant_id,
        "server_url": glean_chat_url(server_url),
        "scope": _secret_or_env("GLEAN_OAUTH_SCOPE") or DEFAULT_OAUTH_SCOPE,
        "redirect_uri": oauth_redirect_uri(),
        "authorize_url": _secret_or_env("AZURE_AUTHORIZE_URL") or authorize_url,
        "token_url": _secret_or_env("AZURE_TOKEN_URL") or token_url,
    }
    act_as = _secret_or_env("GLEAN_ACT_AS")
    if act_as:
        config["act_as"] = act_as
    return config


def _query_params() -> dict[str, str]:
    params: dict[str, str] = {}
    for key, value in st.query_params.items():
        if key in {"session_state", "state"}:
            continue
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        params[str(key)] = str(value)
    return params


def _clear_oauth_query_params() -> None:
    for key in ("code", "state", "session_state", "error", "error_description"):
        try:
            del st.query_params[key]
        except KeyError:
            pass


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    import base64

    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def microsoft_authorization_url(config: dict[str, str]) -> str:
    from urllib.parse import urlencode

    # Matches Insomnia: Authorization Code, no PKCE, no STATE, Graph /.default scope.
    params = {
        "client_id": config["client_id"],
        "response_type": "code",
        "redirect_uri": config["redirect_uri"],
        "response_mode": "query",
        "scope": config["scope"],
    }
    return f"{config['authorize_url']}?{urlencode(params)}"


def _store_microsoft_token(payload: dict[str, Any]) -> str:
    import time

    token = payload.get("access_token")
    if not token:
        detail = payload.get("error_description") or payload.get("error") or "unknown error"
        raise RuntimeError(f"Microsoft token request failed: {detail}")
    st.session_state.ms_token = str(token)
    st.session_state.ms_token_expires = time.time() + int(payload.get("expires_in") or 3600) - 60
    if refresh := payload.get("refresh_token"):
        st.session_state.ms_refresh_token = str(refresh)
    return str(token)


def _post_token(config: dict[str, str], data: dict[str, str]) -> dict[str, Any]:
    import requests

    response = requests.post(
        config["token_url"],
        headers={
            "Authorization": _basic_auth_header(config["client_id"], config["client_secret"]),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=data,
        timeout=30,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"error": response.text}
    if response.status_code >= 400 or "access_token" not in payload:
        detail = payload.get("error_description") or payload.get("error") or response.text
        raise RuntimeError(f"Microsoft token exchange failed: {detail}")
    return payload


def complete_microsoft_login(config: dict[str, str]) -> str | None:
    params = _query_params()
    if params.get("error"):
        detail = params.get("error_description") or params["error"]
        _clear_oauth_query_params()
        raise RuntimeError(f"Microsoft sign-in failed: {detail}")
    if "code" not in params:
        return None
    if st.session_state.get("_oauth_consumed_code") == params["code"]:
        token = st.session_state.get("ms_token")
        return str(token) if token else None

    # Insomnia does not send or validate OAuth `state`. Streamlit also starts a
    # new session on the Microsoft redirect, so a stored state check fails.
    st.session_state._oauth_consumed_code = params["code"]
    try:
        payload = _post_token(
            config,
            {
                "grant_type": "authorization_code",
                "code": params["code"],
                "redirect_uri": config["redirect_uri"],
            },
        )
    except Exception:
        st.session_state.pop("_oauth_consumed_code", None)
        raise
    _clear_oauth_query_params()
    return _store_microsoft_token(payload)


def _refresh_microsoft_token(config: dict[str, str]) -> str | None:
    refresh = st.session_state.get("ms_refresh_token")
    if not refresh:
        return None
    payload = _post_token(
        config,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        },
    )
    return _store_microsoft_token(payload)


def microsoft_access_token(config: dict[str, str]) -> str | None:
    import time

    if token := complete_microsoft_login(config):
        return token

    token = st.session_state.get("ms_token")
    expires = st.session_state.get("ms_token_expires") or 0
    if token and time.time() < expires:
        return str(token)
    if token:
        return _refresh_microsoft_token(config)
    return None


def sign_out() -> None:
    for key in (
        "ms_token",
        "ms_token_expires",
        "ms_refresh_token",
        "_oauth_consumed_code",
        "glean_chat_id",
    ):
        st.session_state.pop(key, None)
