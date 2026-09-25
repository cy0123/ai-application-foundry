from __future__ import annotations

import time
from typing import Any

import pandas as pd
import requests

from dbx_auth import databricks_headers, databricks_instance, genie_space_id

GENIE_HTTP_TIMEOUT_S = 60
GENIE_POLL_INTERVAL_S = 2
GENIE_POLL_TIMEOUT_S = 120
COMPLETED_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"}


def _genie_url(*parts: str) -> str:
    return "/".join([f"{databricks_instance()}/api/2.0/genie", *parts])


def _request(
    method: str,
    url: str,
    token: str,
    *,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = requests.request(
        method,
        url,
        headers=databricks_headers(token),
        json=json,
        timeout=GENIE_HTTP_TIMEOUT_S,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Genie API failed ({response.status_code}): {response.text[:800]}"
        )
    if not response.content:
        return {}
    return response.json()


def list_genie_spaces(token: str) -> list[dict[str, Any]]:
    payload = _request("GET", _genie_url("spaces"), token)
    return list(payload.get("spaces") or [])


def get_genie_space(token: str, space_id: str | None = None) -> dict[str, Any]:
    return _request("GET", _genie_url("spaces", space_id or genie_space_id()), token)


def start_genie_conversation(token: str, question: str) -> dict[str, Any]:
    return _request(
        "POST",
        _genie_url("spaces", genie_space_id(), "start-conversation"),
        token,
        json={"content": question},
    )


def send_genie_message(
    token: str, conversation_id: str, question: str
) -> dict[str, Any]:
    return _request(
        "POST",
        _genie_url("spaces", genie_space_id(), "conversations", conversation_id, "messages"),
        token,
        json={"content": question},
    )


def get_genie_message(
    token: str, conversation_id: str, message_id: str
) -> dict[str, Any]:
    return _request(
        "GET",
        _genie_url(
            "spaces",
            genie_space_id(),
            "conversations",
            conversation_id,
            "messages",
            message_id,
        ),
        token,
    )


def get_genie_query_result(
    token: str, conversation_id: str, message_id: str, attachment_id: str
) -> dict[str, Any]:
    return _request(
        "GET",
        _genie_url(
            "spaces",
            genie_space_id(),
            "conversations",
            conversation_id,
            "messages",
            message_id,
            "attachments",
            attachment_id,
            "query-result",
        ),
        token,
    )


def _attr(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj and obj[name] is not None:
            return obj[name]
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _message_id(payload: dict[str, Any]) -> str:
    message = payload.get("message") or payload
    value = _attr(message, "message_id", "id")
    if not value:
        raise RuntimeError(f"Genie response did not include a message id: {payload}")
    return str(value)


def _conversation_id(payload: dict[str, Any], fallback: str | None = None) -> str:
    conversation = payload.get("conversation") or {}
    message = payload.get("message") or payload
    value = (
        _attr(conversation, "id", "conversation_id")
        or _attr(message, "conversation_id")
        or fallback
    )
    if not value:
        raise RuntimeError(f"Genie response did not include a conversation id: {payload}")
    return str(value)


def poll_genie_message(
    token: str, conversation_id: str, message_id: str
) -> dict[str, Any]:
    deadline = time.time() + GENIE_POLL_TIMEOUT_S
    payload = get_genie_message(token, conversation_id, message_id)
    while str(payload.get("status") or "").upper() not in COMPLETED_STATUSES:
        if time.time() >= deadline:
            raise RuntimeError(
                "Genie timed out waiting for a completed answer. Try the question again."
            )
        time.sleep(GENIE_POLL_INTERVAL_S)
        payload = get_genie_message(token, conversation_id, message_id)
    status = str(payload.get("status") or "").upper()
    if status != "COMPLETED":
        error = payload.get("error") or payload
        raise RuntimeError(f"Genie did not complete ({status}): {error}")
    return payload


def _text_from_attachment(attachment: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    text = attachment.get("text")
    if isinstance(text, dict):
        content = text.get("content") or text.get("text")
        if content:
            parts.append(str(content))
    elif text:
        parts.append(str(text))

    query = attachment.get("query") or {}
    if isinstance(query, dict):
        if description := query.get("description"):
            parts.append(str(description))
        if sql := query.get("query"):
            parts.append(f"```sql\n{sql.strip()}\n```")
    return parts


def query_result_to_frame(payload: dict[str, Any]) -> pd.DataFrame:
    statement = payload.get("statement_response") or payload
    columns = [
        str(col.get("name") or f"col_{index}")
        for index, col in enumerate(
            ((statement.get("manifest") or {}).get("schema") or {}).get("columns") or []
        )
    ]
    rows = ((statement.get("result") or {}).get("data_array")) or []
    if columns:
        return pd.DataFrame(rows, columns=columns)
    return pd.DataFrame(rows)


def extract_query_frames(
    token: str, conversation_id: str, message: dict[str, Any]
) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    message_id = _message_id(message)
    for attachment in message.get("attachments") or []:
        query = attachment.get("query") or {}
        attachment_id = (
            attachment.get("attachment_id")
            or query.get("attachment_id")
            or attachment.get("id")
        )
        if not attachment_id or not query:
            continue
        try:
            result = get_genie_query_result(
                token, conversation_id, message_id, str(attachment_id)
            )
        except RuntimeError:
            continue
        frame = query_result_to_frame(result)
        if not frame.empty:
            frames.append(frame)
    return frames


def format_genie_reply(message: dict[str, Any]) -> str:
    parts: list[str] = []
    for attachment in message.get("attachments") or []:
        parts.extend(_text_from_attachment(attachment))
    if parts:
        return "\n\n".join(parts)
    if content := message.get("content"):
        return str(content)
    return "Genie returned no text for this question."


def ask_genie(
    question: str,
    conversation_id: str | None,
    token: str,
) -> tuple[str, str, list[pd.DataFrame]]:
    if conversation_id:
        payload = send_genie_message(token, conversation_id, question)
    else:
        payload = start_genie_conversation(token, question)

    conversation_id = _conversation_id(payload, conversation_id)
    message = poll_genie_message(token, conversation_id, _message_id(payload))
    frames = extract_query_frames(token, conversation_id, message)
    return format_genie_reply(message), conversation_id, frames
