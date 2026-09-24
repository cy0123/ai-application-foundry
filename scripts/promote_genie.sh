#!/usr/bin/env bash
# Export a development Genie agent and deploy it to a test agent.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Optional local configuration. Keep this file out of version control.
if [[ -f "${ROOT}/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env.local"
  set +a
fi

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment variable: ${name}" >&2
    exit 1
  fi
}

require_env RESOURCE_KEY
require_env SOURCE_GENIE_NAME
require_env TARGET_GENIE_NAME
require_env TEST_GENIE_PARENT_PATH
require_env DEV_DATABRICKS_HOST
require_env ARM_TENANT_ID
require_env ARM_CLIENT_ID
require_env ARM_CLIENT_SECRET

SOURCE_GENIE_SPACE_ID="${SOURCE_GENIE_SPACE_ID:-}"
TARGET_GENIE_SPACE_ID="${TARGET_GENIE_SPACE_ID:-}"
TEST_WAREHOUSE_ID="${TEST_WAREHOUSE_ID:-}"

export DATABRICKS_AUTH_TYPE="${DATABRICKS_AUTH_TYPE:-azure-client-secret}"

DEV_DATABRICKS_HOST="${DEV_DATABRICKS_HOST%/}"
TEST_DATABRICKS_HOST="${TEST_DATABRICKS_HOST:-$DEV_DATABRICKS_HOST}"
TEST_DATABRICKS_HOST="${TEST_DATABRICKS_HOST%/}"

for host in "$DEV_DATABRICKS_HOST" "$TEST_DATABRICKS_HOST"; do
  case "$host" in
    https://*) ;;
    *)
      echo "Workspace host must be an HTTPS URL." >&2
      exit 1
      ;;
  esac
done

normalize_path() {
  local path="$1"
  path="${path%/}"
  path="${path#/Workspace}"
  path="${path#/}"
  printf '/%s' "$path"
}

TITLE_VAR_NAME="${TITLE_VAR_NAME:-test_genie_title}"
PARENT_VAR_NAME="${PARENT_VAR_NAME:-test_genie_parent_path}"
WAREHOUSE_VAR_NAME="${WAREHOUSE_VAR_NAME:-test_warehouse_id}"

bundle_vars() {
  local warehouse="${1:-pending-export}"

  printf '%s\0' \
    --var "dev_workspace_host=${DEV_DATABRICKS_HOST}" \
    --var "test_workspace_host=${TEST_DATABRICKS_HOST}" \
    --var "${TITLE_VAR_NAME}=${TARGET_GENIE_NAME}" \
    --var "${PARENT_VAR_NAME}=${TEST_GENIE_PARENT_PATH}" \
    --var "${WAREHOUSE_VAR_NAME}=${warehouse}" \
    --var "warehouse_id=${warehouse}"
}

run_bundle() {
  local warehouse="$1"
  shift

  local args=()
  while IFS= read -r -d '' arg; do
    args+=("$arg")
  done < <(bundle_vars "$warehouse")

  databricks "$@" "${args[@]}"
}

# Run authentication and API calls outside the bundle directory.
databricks_api() {
  (cd /tmp && databricks "$@")
}

find_spaces_by_title() {
  local title="$1"
  local page_token=""
  local response

  while true; do
    if [[ -n "$page_token" ]]; then
      response="$(
        databricks_api genie list-spaces \
          --page-size 200 \
          --page-token "$page_token" \
          --output json
      )"
    else
      response="$(
        databricks_api genie list-spaces \
          --page-size 200 \
          --output json
      )"
    fi

    printf '%s' "$response" |
      jq -r --arg title "$title" '
        (.spaces // [])[]
        | select(.title == $title)
        | [.space_id, (.parent_path // ""), (.warehouse_id // "")]
        | @tsv
      '

    page_token="$(printf '%s' "$response" | jq -r '.next_page_token // empty')"
    [[ -z "$page_token" ]] && break
  done
}

resolve_space_id() {
  local title="$1"
  local parent="${2:-}"
  local exclude_parent="${3:-}"
  local rows count wanted excluded

  rows="$(find_spaces_by_title "$title")"

  if [[ -z "$rows" ]]; then
    return 1
  fi

  if [[ -n "$parent" ]]; then
    wanted="$(normalize_path "$parent")"

    rows="$(
      printf '%s\n' "$rows" |
        awk -F '\t' -v wanted="$wanted" '
          function norm(p) {
            sub(/\/$/, "", p)
            sub(/^\/Workspace/, "", p)
            sub(/^\//, "", p)
            return "/" p
          }
          norm($2) == wanted { print }
        '
    )"
  fi

  if [[ -n "$exclude_parent" && -n "$rows" ]]; then
    excluded="$(normalize_path "$exclude_parent")"

    rows="$(
      printf '%s\n' "$rows" |
        awk -F '\t' -v excluded="$excluded" '
          function norm(p) {
            sub(/\/$/, "", p)
            sub(/^\/Workspace/, "", p)
            sub(/^\//, "", p)
            return "/" p
          }
          norm($2) != excluded { print }
        '
    )"
  fi

  if [[ -z "$rows" ]]; then
    return 1
  fi

  count="$(printf '%s\n' "$rows" | sed '/^$/d' | wc -l | tr -d ' ')"

  if [[ "$count" -ne 1 ]]; then
    echo "Multiple Genie agents matched the selection." >&2
    echo "Provide an explicit space ID and rerun." >&2
    return 2
  fi

  printf '%s\n' "$rows" |
    awk -F '\t' 'NR == 1 { print $1 }'
}

read_exported_warehouse() {
  python3 - "$ROOT/resources/${RESOURCE_KEY}.genie_space.yml" <<'PY'
import pathlib
import re
import sys

text = pathlib.Path(sys.argv[1]).read_text()
match = re.search(
    r"(?m)^[ \t]+warehouse_id:[ \t]*['\"]?([^'\"\s#]+)",
    text,
)

if not match or match.group(1) == "pending-export":
    sys.exit(1)

print(match.group(1))
PY
}

refuse_source_overwrite() {
  local target_id="$1"

  if [[ "$target_id" == "$SOURCE_ID" &&
        "$DEV_DATABRICKS_HOST" == "$TEST_DATABRICKS_HOST" ]]; then
    echo "Refusing to update the source Genie agent as the test target." >&2
    echo "Use a different target or provide an explicit target space ID." >&2
    exit 1
  fi
}

source_exclude_parent() {
  local title="$1"

  if [[ "$title" == "$TARGET_GENIE_NAME" ]]; then
    printf '%s' "$TEST_GENIE_PARENT_PATH"
  fi
}

echo "Checking development workspace access."
export DATABRICKS_HOST="$DEV_DATABRICKS_HOST"
databricks_api current-user me --output json >/dev/null

if [[ -z "$SOURCE_GENIE_SPACE_ID" ]]; then
  echo "Locating the source Genie agent."

  set +e
  SOURCE_ID="$(
    resolve_space_id \
      "$SOURCE_GENIE_NAME" \
      "" \
      "$(source_exclude_parent "$SOURCE_GENIE_NAME")"
  )"
  status=$?
  set -e

  if [[ "$status" -ne 0 ]]; then
    if [[ "$status" -eq 1 ]]; then
      echo "The source Genie agent could not be found." >&2
    fi
    exit 1
  fi
else
  SOURCE_ID="$SOURCE_GENIE_SPACE_ID"
fi

echo "Exporting the source Genie agent."
run_bundle "pending-export" bundle generate genie-space \
  --existing-id "$SOURCE_ID" \
  --key "$RESOURCE_KEY" \
  --force \
  --target dev

resource_file="resources/${RESOURCE_KEY}.genie_space.yml"
space_file="src/${RESOURCE_KEY}.geniespace.json"

if [[ ! -f "$resource_file" || ! -f "$space_file" ]]; then
  echo "The export did not produce the expected files." >&2
  exit 1
fi

if grep -Eq 'warehouse_id: ["'\'']?pending-export["'\'']?' "$resource_file"; then
  echo "The export still contains a placeholder warehouse ID." >&2
  exit 1
fi

if [[ -z "$TEST_WAREHOUSE_ID" ]]; then
  TEST_WAREHOUSE_ID="$(read_exported_warehouse)"
fi

echo "Checking test workspace access."
export DATABRICKS_HOST="$TEST_DATABRICKS_HOST"
databricks_api current-user me --output json >/dev/null

echo "Ensuring the test folder exists."
databricks_api workspace mkdirs "$TEST_GENIE_PARENT_PATH"

state_id=""
set +e
summary_json="$(
  run_bundle "$TEST_WAREHOUSE_ID" \
    bundle summary \
    --target test \
    --force-pull \
    --output json
)"
summary_status=$?
set -e

if [[ "$summary_status" -eq 0 ]]; then
  state_id="$(
    printf '%s' "$summary_json" |
      jq -r --arg key "$RESOURCE_KEY" \
        '.resources.genie_spaces[$key].id // empty' ||
      true
  )"
fi

resolved_target_id=""

if [[ -n "$TARGET_GENIE_SPACE_ID" ]]; then
  resolved_target_id="$TARGET_GENIE_SPACE_ID"
elif [[ -z "$state_id" ]]; then
  echo "Locating an existing test Genie agent."

  set +e
  resolved_target_id="$(
    resolve_space_id \
      "$TARGET_GENIE_NAME" \
      "$TEST_GENIE_PARENT_PATH"
  )"
  status=$?
  set -e

  if [[ "$status" -eq 2 ]]; then
    exit 1
  elif [[ "$status" -ne 0 ]]; then
    resolved_target_id=""
  fi
fi

if [[ -n "$resolved_target_id" ]]; then
  refuse_source_overwrite "$resolved_target_id"
fi

if [[ -n "$state_id" ]]; then
  refuse_source_overwrite "$state_id"
fi

if [[ -n "$resolved_target_id" && "$resolved_target_id" != "$state_id" ]]; then
  echo "Binding the deployment to the existing test agent."

  run_bundle "$TEST_WAREHOUSE_ID" \
    bundle deployment bind "$RESOURCE_KEY" "$resolved_target_id" \
    --target test \
    --auto-approve
fi

echo "Deploying the Genie agent to the test target."

run_bundle "$TEST_WAREHOUSE_ID" \
  bundle deploy \
  --target test \
  --auto-approve \
  --select "$RESOURCE_KEY"

echo "Deployment completed successfully."

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### Genie promotion"
    echo "- Deployment completed successfully"
    echo "- Resource: configured"
    echo "- Target: test"
  } >> "$GITHUB_STEP_SUMMARY"
fi
