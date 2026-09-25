# ai-application-foundry
AI Application Foundry is an AI-generic platform for building and deploying enterprise AIML applications. It provides reusable CI/CD components for deploying Databricks Genie spaces, along with a reference web application that connects to the Glean API, Databricks Genie, and Glean Agents.

## Run the Genie Deployment Workflow:
    The genie_deployment workflow exports a Genie agent from a development Databricks workspace and deploys it to a test workspace.

## Configure
    Create a protected databricks-test environment with these secrets:
    AZURE_TENANT_ID
    AZURE_CLIENT_ID
    AZURE_CLIENT_SECRET
    Add these variables:
    DEV_DATABRICKS_HOST
    TEST_DATABRICKS_HOST
    TEST_GENIE_PARENT_PATH
    Optional: TEST_WAREHOUSE_ID, DATABRICKS_GIT_FOLDER_PATH, SOURCE_GENIE_SPACE_ID, and TARGET_GENIE_SPACE_ID
    Do not place credentials or internal values directly in the workflow file.

# Bar Chart Dashboard

A Python Streamlit app that loads a generic bar chart dashboard. Compare sample metrics across regions, switch chart orientation, inspect the source table, and ask questions in the Glean chat at the bottom of the page. `dbx_interactive_dashboard.py` is a separate Databricks SQL page that asks questions through a Genie agent.

## Requirements

- Python 3.10 or later
- pip

## Run locally

1. Open a terminal in this folder.

2. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   On Windows:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Start the app from the `app` directory:

   ```bash
   cd app
   streamlit run glean_interactive_dashboard.py
   streamlit run dbx_interactive_dashboard.py
   ```

5. Open the URL Streamlit prints, usually [http://localhost:8501](http://localhost:8501).

Stop the server with `Ctrl+C`. Deactivate the virtual environment with `deactivate`.

## Glean chat
<img width="1294" height="738" alt="Screenshot 2026-09-25 at 11 34 36 AM" src="https://github.com/user-attachments/assets/42755a66-06a6-4528-9a7d-4e9af73bf9e3" />

The bottom chat widget on `glean_interactive_dashboard.py` calls Glean Chat with a snapshot of the current dashboard table and filters. Authentication uses the OAuth 2 authorization-code flow:

- Grant type: Authorization Code
- Authorize URL: `https://login.microsoftonline.com/{tenant-id}/oauth2/v2.0/authorize`
- Token URL: `https://login.microsoftonline.com/{tenant-id}/oauth2/v2.0/token`
- Scope: `https://graph.microsoft.com/.default`
- PKCE: off
- Client credentials sent as a Basic Auth header
- Glean chat: `POST {GLEAN_SERVER_URL}/rest/api/v1/chat`

Click **Sign in with Microsoft**. Microsoft redirects back to this app with an authorization code. The app exchanges that code for a token and uses it on Glean Chat (`X-Glean-Auth-Type: OAUTH`). There is no separate Fetch Tokens step.

Set Glean credentials in `.streamlit/secrets.toml` (this file is gitignored):

```toml
GLEAN_CLIENT_ID = "your-entra-app-client-id"
GLEAN_CLIENT_SECRET = "your-entra-app-client-secret"
AZURE_TENANT_ID = "your-tenant-id"
GLEAN_SERVER_URL = "https://your-company.glean.com"
GLEAN_OAUTH_SCOPE = "https://graph.microsoft.com/.default"
AZURE_REDIRECT_URI = "http://localhost:8501/"
```

Register `http://localhost:8501/` as a redirect URI on the Entra app. Restart Streamlit after changing secrets.

## Databricks Genie chat

`dbx_interactive_dashboard.py` is the only page that calls Databricks Genie. It lists spaces with `GET /api/2.0/genie/spaces`, then starts or continues a conversation against the configured Genie agent. The SQL panel runs `DATABRICKS_SQL_STATEMENT` on `DATABRICKS_WAREHOUSE_ID`.

- Host: Databricks workspace URL (`DATABRICKS_INSTANCE`)
- Authorization: `Bearer` token from the Databricks service principal

Set Databricks credentials in `.streamlit/secrets.toml`:

```toml
AZURE_TENANT_ID = "your-tenant-id"
DATABRICKS_CLIENT_ID = "your-databricks-sp-client-id"
DATABRICKS_CLIENT_SECRET = "your-databricks-sp-client-secret"
DATABRICKS_INSTANCE = "https://adb-xxxxxxxxxxxx.xx.azuredatabricks.net"
DATABRICKS_WORKSPACE = "your-workspace-name"
GENIE_SPACE_ID = "your-genie-space-id"
DATABRICKS_WAREHOUSE_ID = "your-sql-warehouse-id"
DATABRICKS_CATALOG = "your_catalog"
DATABRICKS_SCHEMA = "your_schema"
DATABRICKS_SQL_STATEMENT = """
SELECT 1 AS example
"""
```

`DATABRICKS_WORKSPACE`, `DATABRICKS_CATALOG`, and `DATABRICKS_SCHEMA` are optional. Restart Streamlit after changing secrets.
