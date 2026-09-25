from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from glean_auth import (
    glean_oauth_config,
    microsoft_access_token,
    microsoft_authorization_url,
    sign_out,
)

st.set_page_config(
    page_title="Bar Chart Dashboard",
    page_icon="📊",
    layout="wide",
)

CATEGORIES = ["North", "South", "East", "West", "Central"]
METRICS = {
    "Revenue": [184_000, 142_000, 167_000, 121_000, 156_000],
    "Orders": [920, 710, 840, 630, 780],
    "Customers": [410, 320, 365, 280, 350],
}

SETUP_MESSAGE = (
    "Glean chat is not configured. Set `GLEAN_CLIENT_ID`, `GLEAN_CLIENT_SECRET`, "
    "`GLEAN_SERVER_URL`, and `AZURE_TENANT_ID` in `.streamlit/secrets.toml` or as "
    "environment variables. See the README for Microsoft OAuth setup."
)
SIGN_IN_MESSAGE = "Sign in with Microsoft to ask questions about this dashboard."
GLEAN_HTTP_TIMEOUT_MS = 120_000


@st.cache_data
def load_data() -> pd.DataFrame:
    return pd.DataFrame({"Region": CATEGORIES, **METRICS})


def dashboard_context(
    data: pd.DataFrame,
    metric: str,
    orientation: str,
    total: int,
    top_region: str,
) -> str:
    table_csv = data.to_csv(index=False).strip()
    return (
        "You are helping a user with a Streamlit bar chart dashboard.\n"
        "Prefer the dashboard numbers below for questions about this page. "
        "Use Glean company knowledge for everything else.\n\n"
        f"Selected metric: {metric}\n"
        f"Chart orientation: {orientation}\n"
        f"Total {metric}: {total:,}\n"
        f"Top region: {top_region}\n\n"
        f"Source data (CSV):\n{table_csv}\n"
    )


def _attr(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def extract_glean_reply(response: Any) -> tuple[str, str | None]:
    chat_id = _attr(response, "chat_id", "chatId")
    messages = _attr(response, "messages") or []
    for message in reversed(list(messages)):
        message_type = str(_attr(message, "message_type", "messageType") or "CONTENT")
        if message_type != "CONTENT":
            continue
        fragments = _attr(message, "fragments") or []
        parts = [str(text) for fragment in fragments if (text := _attr(fragment, "text"))]
        if parts:
            return "\n".join(parts), chat_id
    return "No response received", chat_id


def ask_glean(
    question: str,
    context: str,
    chat_id: str | None,
    token: str,
    config: dict[str, str],
) -> tuple[str, str | None]:
    import requests

    prompt = f"{context}\nUser question: {question}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Glean-Auth-Type": "OAUTH",
    }
    if act_as := config.get("act_as"):
        headers["X-Glean-ActAs"] = act_as

    body: dict[str, Any] = {
        "messages": [
            {
                "author": "USER",
                "fragments": [{"text": prompt}],
            }
        ],
        "timeoutMillis": GLEAN_HTTP_TIMEOUT_MS,
    }
    if chat_id:
        body["chatId"] = chat_id

    response = requests.post(
        config["server_url"],
        headers=headers,
        json=body,
        timeout=GLEAN_HTTP_TIMEOUT_MS / 1000,
    )
    if response.status_code >= 400:
        detail = response.text[:800] or response.reason
        raise RuntimeError(f"Glean chat failed ({response.status_code}): {detail}")
    return extract_glean_reply(response.json())


def render_chat(
    data: pd.DataFrame,
    metric: str,
    orientation: str,
    total: int,
    top_region: str,
) -> None:
    st.divider()
    st.subheader("Ask the dashboard")
    st.caption(
        "Questions are answered by Glean Chat using this page's data. "
        'Try “Which region has the highest revenue?” or “How do orders compare across regions?”'
    )

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "glean_chat_id" not in st.session_state:
        st.session_state.glean_chat_id = None

    config = glean_oauth_config()
    if not config:
        st.info(SETUP_MESSAGE)
        return

    try:
        token = microsoft_access_token(config)
    except Exception as exc:
        st.error(str(exc))
        token = None

    if not token:
        st.info(SIGN_IN_MESSAGE)
        st.markdown(f"[Sign in with Microsoft]({microsoft_authorization_url(config)})")
        st.caption(
            "After Microsoft sign-in you are redirected back here. The app "
            "exchanges the code for a token and uses it for Glean Chat. "
            f"Entra redirect URI must be `{config['redirect_uri']}`."
        )
        return

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Ask a question about this data...")
    if not question:
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    context = dashboard_context(data, metric, orientation, total, top_region)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking..."):
                reply, chat_id = ask_glean(
                    question,
                    context,
                    st.session_state.glean_chat_id,
                    token,
                    config,
                )
            st.session_state.glean_chat_id = chat_id
        except Exception as exc:
            reply = f"Glean chat failed: {exc}"
        st.markdown(reply)

    st.session_state.messages.append({"role": "assistant", "content": reply})


def main() -> None:
    st.title("Bar Chart Dashboard")
    st.caption("A generic Streamlit dashboard for comparing values across categories.")

    data = load_data()
    metric = st.sidebar.selectbox("Metric", list(METRICS))
    orientation = st.sidebar.radio("Orientation", ["Vertical", "Horizontal"], horizontal=True)
    show_table = st.sidebar.checkbox("Show data table", value=True)

    config = glean_oauth_config()
    if config:
        try:
            signed_in = bool(microsoft_access_token(config))
        except Exception as exc:
            signed_in = False
            st.sidebar.error(str(exc))
        if signed_in:
            st.sidebar.success("Signed in with Microsoft")
            if st.sidebar.button("Sign out"):
                sign_out()
                st.rerun()
        else:
            st.sidebar.markdown(
                f"[Sign in with Microsoft]({microsoft_authorization_url(config)})"
            )

    chart_data = data[["Region", metric]].sort_values(metric, ascending=False)
    is_horizontal = orientation == "Horizontal"
    total = int(chart_data[metric].sum())
    top_region = str(chart_data.iloc[0]["Region"])

    fig = px.bar(
        chart_data,
        x=metric if is_horizontal else "Region",
        y="Region" if is_horizontal else metric,
        orientation="h" if is_horizontal else "v",
        color="Region",
        title=f"{metric} by region",
        text=metric,
    )
    fig.update_traces(texttemplate="%{text:,}", textposition="outside")
    fig.update_layout(
        showlegend=False,
        margin=dict(t=60, r=20, b=40, l=20),
        yaxis_title=None if is_horizontal else metric,
        xaxis_title=metric if is_horizontal else None,
    )

    left, right = st.columns(2)
    left.metric("Total", f"{total:,}")
    right.metric("Top region", top_region)

    st.plotly_chart(fig, use_container_width=True)

    if show_table:
        st.subheader("Source data")
        st.dataframe(data, use_container_width=True, hide_index=True)

    render_chat(data, metric, orientation, total, top_region)


if __name__ == "__main__":
    main()
