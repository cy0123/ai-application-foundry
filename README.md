# ai-application-foundry
AI Application Foundry is an AI-generic platform for building and deploying enterprise AIML applications. It provides reusable CI/CD components for deploying Databricks Genie spaces, along with a reference web application that connects to the Glean API, Databricks Genie, and Glean Agents.

## Run the Genie Deployment Workflow:
    This workflow exports a Genie agent from a development Databricks workspace and deploys it to a test workspace.

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