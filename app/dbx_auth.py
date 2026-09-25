import os
from typing import Any

import requests
import streamlit as st

# Global Azure Databricks application ID. Do not change this GUID.
DATABRICKS_AAD_SCOPE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default"


def _secret_or_env(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    value = os.environ.get(name)
    return value or None


def dbx_oauth_config() -> dict[str, str]:
    tenant_id = _secret_or_env("DATABRICKS_TENANT_ID") or _secret_or_env("AZURE_TENANT_ID")
    client_id = _secret_or_env("DATABRICKS_CLIENT_ID")
    client_secret = _secret_or_env("DATABRICKS_CLIENT_SECRET")
    instance = _secret_or_env("DATABRICKS_INSTANCE") or _secret_or_env("DATABRICKS_HOST")
    missing = [
        name
        for name, value in (
            ("DATABRICKS_TENANT_ID or AZURE_TENANT_ID", tenant_id),
            ("DATABRICKS_CLIENT_ID", client_id),
            ("DATABRICKS_CLIENT_SECRET", client_secret),
            ("DATABRICKS_INSTANCE", instance),
        )
        if not value
    ]
    if missing:
        listed = ", ".join(f"`{name}`" for name in missing)
        raise RuntimeError(
            "Databricks is not configured. Set "
            f"{listed} in `.streamlit/secrets.toml` or as environment variables."
        )
    assert tenant_id and client_id and client_secret and instance
    return {
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_url": f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        "scope": DATABRICKS_AAD_SCOPE,
        "instance": normalize_databricks_instance(instance),
        "workspace": _secret_or_env("DATABRICKS_WORKSPACE") or "",
    }


def normalize_databricks_instance(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if url.endswith("/oidc"):
        url = url[: -len("/oidc")]
    if url.startswith(("http://", "https://")):
        return url
    if "." not in url:
        return f"https://{url}.azuredatabricks.net"
    return f"https://{url}"


def databricks_instance() -> str:
    return dbx_oauth_config()["instance"]


def genie_space_id() -> str:
    space_id = _secret_or_env("GENIE_SPACE_ID")
    if not space_id:
        raise RuntimeError(
            "Genie space is not configured. Set `GENIE_SPACE_ID` "
            "in `.streamlit/secrets.toml` or as an environment variable."
        )
    return space_id


def get_databricks_token(
    tenant_id: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> str:
    """Generate an Azure AD bearer token for the Databricks service principal."""
    if tenant_id and client_id and client_secret:
        config = {
            "tenant_id": tenant_id,
            "client_id": client_id,
            "client_secret": client_secret,
            "token_url": f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            "scope": DATABRICKS_AAD_SCOPE,
        }
    else:
        config = dbx_oauth_config()

    response = requests.post(
        config["token_url"],
        data={
            "grant_type": "client_credentials",
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "scope": config["scope"],
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Error getting Databricks token: {response.text}")
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Databricks token response did not include access_token.")
    return str(token)


def databricks_headers(token: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token or get_databricks_token()}",
        "Content-Type": "application/json",
    }


def list_databricks_clusters(token: str | None = None) -> dict[str, Any]:
    config = dbx_oauth_config()
    instance = config.get("instance")
    if not instance:
        raise RuntimeError(
            "Set `DATABRICKS_INSTANCE` (workspace URL) in secrets to call the clusters API."
        )
    response = requests.get(
        f"{instance}/api/2.0/clusters/list",
        headers=databricks_headers(token),
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Databricks clusters API failed ({response.status_code}): {response.text}"
        )
    return response.json()


if __name__ == "__main__":
    print("Generating Token...")
    token = get_databricks_token()
    print(f"Token generated successfully! (First 10 chars): {token[:10]}...")
    try:
        clusters = list_databricks_clusters(token)
        print(f"Success! Found {len(clusters.get('clusters', []))} clusters.")
    except RuntimeError as exc:
        print(exc)
