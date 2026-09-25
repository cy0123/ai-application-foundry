import os
from typing import Any

import pandas as pd
import requests
import streamlit as st

from dbx_auth import (
    databricks_headers,
    databricks_instance,
    dbx_oauth_config,
    genie_space_id,
    get_databricks_token,
)
from genie import ask_genie, get_genie_space, list_genie_spaces

st.set_page_config(
    page_title="Databricks SQL Dashboard",
    page_icon="🧱",
    layout="wide",
)

SQL_ENDPOINT = "/api/2.0/sql/statements"


def _setting(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    value = os.environ.get(name)
    return value or None


def sql_statement_body() -> dict[str, Any]:
    warehouse_id = _setting("DATABRICKS_WAREHOUSE_ID")
    statement = _setting("DATABRICKS_SQL_STATEMENT")
    missing = [
        name
        for name, value in (
            ("DATABRICKS_WAREHOUSE_ID", warehouse_id),
            ("DATABRICKS_SQL_STATEMENT", statement),
        )
        if not value
    ]
    if missing:
        listed = ", ".join(f"`{name}`" for name in missing)
        raise RuntimeError(
            "Databricks SQL is not configured. Set "
            f"{listed} in `.streamlit/secrets.toml` or as environment variables."
        )
    assert warehouse_id and statement
    body: dict[str, Any] = {
        "warehouse_id": warehouse_id,
        "statement": statement,
        "wait_timeout": "30s",
        "format": "JSON_ARRAY",
    }
    if catalog := _setting("DATABRICKS_CATALOG"):
        body["catalog"] = catalog
    if schema := _setting("DATABRICKS_SCHEMA"):
        body["schema"] = schema
    return body


def run_sql_statement(token: str) -> dict[str, Any]:
    url = f"{databricks_instance()}{SQL_ENDPOINT}"
    response = requests.post(
        url,
        headers=databricks_headers(token),
        json=sql_statement_body(),
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Databricks SQL failed ({response.status_code}): {response.text[:800]}"
        )
    payload = response.json()
    state = str((payload.get("status") or {}).get("state") or "")
    if state in {"PENDING", "RUNNING"}:
        statement_id = payload.get("statement_id")
        if statement_id:
            payload = wait_for_statement(token, str(statement_id))
            state = str((payload.get("status") or {}).get("state") or "")
    if state not in {"SUCCEEDED", ""}:
        error = (payload.get("status") or {}).get("error") or payload
        raise RuntimeError(f"Databricks SQL did not succeed ({state}): {error}")
    return payload


def wait_for_statement(token: str, statement_id: str) -> dict[str, Any]:
    url = f"{databricks_instance()}{SQL_ENDPOINT}/{statement_id}"
    response = requests.get(url, headers=databricks_headers(token), timeout=60)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Databricks SQL poll failed ({response.status_code}): {response.text[:800]}"
        )
    return response.json()


def statement_to_frame(payload: dict[str, Any]) -> pd.DataFrame:
    columns = [
        str(col.get("name") or f"col_{index}")
        for index, col in enumerate(
            ((payload.get("manifest") or {}).get("schema") or {}).get("columns") or []
        )
    ]
    rows = ((payload.get("result") or {}).get("data_array")) or []
    if columns:
        return pd.DataFrame(rows, columns=columns)
    return pd.DataFrame(rows)


@st.cache_data(ttl=120, show_spinner=False)
def load_query_results() -> pd.DataFrame:
    token = get_databricks_token()
    return statement_to_frame(run_sql_statement(token))


@st.cache_data(ttl=300, show_spinner=False)
def load_genie_space() -> dict[str, Any]:
    token = get_databricks_token()
    spaces = list_genie_spaces(token)
    space_id = genie_space_id()
    for space in spaces:
        if str(space.get("space_id") or space.get("id") or "") == space_id:
            return space
    return get_genie_space(token, space_id)


def _render_table(table: Any) -> None:
    if not table:
        return
    try:
        frame = pd.DataFrame(table)
    except ValueError:
        return
    if not frame.empty:
        st.dataframe(frame, use_container_width=True, hide_index=True)


def render_genie_chat() -> None:
    st.divider()
    st.subheader("Ask Genie")
    try:
        space = load_genie_space()
        title = str(space.get("title") or "Databricks Genie")
        st.caption(
            f"Questions are answered by Databricks Genie `{title}` "
            f"(`{genie_space_id()}`) on `{databricks_instance()}`."
        )
    except Exception:
        st.caption(
            "Questions are answered by Databricks Genie. "
            'Try “What tables can you query?” or “Show a sample of the data.”'
        )

    if "genie_messages" not in st.session_state:
        st.session_state.genie_messages = []
    if "genie_conversation_id" not in st.session_state:
        st.session_state.genie_conversation_id = None

    try:
        token = get_databricks_token()
    except Exception as exc:
        st.error(str(exc))
        return

    for message in st.session_state.genie_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            for table in message.get("tables") or []:
                _render_table(table)

    question = st.chat_input("Ask the Databricks Genie agent...")
    if not question:
        return

    st.session_state.genie_messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    tables: list[dict[str, Any]] = []
    with st.chat_message("assistant"):
        try:
            with st.spinner("Asking Databricks Genie..."):
                reply, conversation_id, frames = ask_genie(
                    question,
                    st.session_state.genie_conversation_id,
                    token,
                )
            st.session_state.genie_conversation_id = conversation_id
            tables = [frame.to_dict(orient="list") for frame in frames]
        except Exception as exc:
            reply = f"Genie chat failed: {exc}"
        st.markdown(reply)
        for table in tables:
            _render_table(table)

    st.session_state.genie_messages.append(
        {"role": "assistant", "content": reply, "tables": tables}
    )


def main() -> None:
    st.title("Databricks SQL Dashboard")
    try:
        body = sql_statement_body()
    except RuntimeError as exc:
        st.error(str(exc))
        render_genie_chat()
        return
    location = ".".join(
        part for part in (body.get("catalog"), body.get("schema")) if part
    )
    statement = f"`{body['statement']}`"
    st.caption(f"{location} · {statement}" if location else statement)

    if st.sidebar.button("Refresh query"):
        load_query_results.clear()
        st.rerun()
    if st.sidebar.button("Reset Genie conversation"):
        st.session_state.genie_conversation_id = None
        st.session_state.genie_messages = []
        st.rerun()

    try:
        config = dbx_oauth_config()
        workspace = config.get("workspace")
        label = f"{workspace} · {config['instance']}" if workspace else config["instance"]
        st.sidebar.caption(label)
    except Exception as exc:
        st.sidebar.error(str(exc))

    try:
        with st.spinner("Running Databricks SQL query..."):
            data = load_query_results()
    except Exception as exc:
        st.error(str(exc))
        render_genie_chat()
        return

    st.metric("Rows", f"{len(data):,}")
    st.subheader("Query results")
    if data.empty:
        st.info("The query returned no rows.")
    else:
        st.dataframe(data, use_container_width=True, hide_index=True)
        st.subheader("Result list")
        for index, row in data.iterrows():
            title = next(
                (str(value) for value in row.tolist() if value not in (None, "")),
                f"Row {int(index) + 1}",
            )
            with st.expander(f"{int(index) + 1}. {title}", expanded=index == 0):
                st.write(row.to_dict())

    render_genie_chat()


if __name__ == "__main__":
    main()
