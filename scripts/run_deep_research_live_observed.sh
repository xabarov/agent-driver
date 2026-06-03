#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
ARTIFACT_DIR="${CHAT_DEMO_LIVE_ARTIFACT_DIR:-/tmp/chat-demo-live-observed-${STAMP}}"
BACKEND_PORT="${CHAT_DEMO_BACKEND_PORT:-8010}"
FRONTEND_PORT="${CHAT_DEMO_FRONTEND_PORT:-5174}"
PHOENIX_ENDPOINT="${PHOENIX_COLLECTOR_ENDPOINT:-http://127.0.0.1:6006}"
PHOENIX_BASE_URL="${PHOENIX_BASE_URL:-${PHOENIX_ENDPOINT%/v1/traces}}"
PROFILES="${DEEP_RESEARCH_PROFILES:-medium}"
QUESTION_ID="${DEEP_RESEARCH_QUESTION_ID:-fork-join-canary}"
LIMIT="${DEEP_RESEARCH_LIMIT:-1}"
PREFLIGHT_SCENARIO="${DEEP_RESEARCH_PREFLIGHT_SCENARIO:-model-preflight-search-fetch}"
PREFLIGHT_CACHE="${DEEP_RESEARCH_PREFLIGHT_CACHE:-${ROOT_DIR}/.agent-driver/live-tool-preflight.sha256}"
SERVER_TIMEOUT="${DEEP_RESEARCH_SERVER_TIMEOUT:-120}"
COMMAND_TIMEOUT="${DEEP_RESEARCH_COMMAND_TIMEOUT:-300}"
PYTHON_CMD="${AGENT_DRIVER_PYTHON_CMD:-}"
if [[ -z "${PYTHON_CMD}" ]]; then
  PYTHON_CMD="uv run --with playwright python"
fi
BACKEND_PYTHON_CMD="${CHAT_DEMO_BACKEND_PYTHON_CMD:-}"
if [[ -z "${BACKEND_PYTHON_CMD}" ]]; then
  BACKEND_PYTHON_CMD="uv run --project ${ROOT_DIR}/examples/chat-demo/backend --with-editable ${ROOT_DIR} python"
fi
FRONTEND_COMMAND="${CHAT_DEMO_FRONTEND_COMMAND:-}"
if [[ -z "${FRONTEND_COMMAND}" ]]; then
  if command -v pnpm >/dev/null 2>&1; then
    FRONTEND_COMMAND="if [[ ! -x node_modules/.bin/vite ]]; then pnpm install --frozen-lockfile; fi; VITE_API_PROXY_TARGET=http://127.0.0.1:${BACKEND_PORT} pnpm run dev -- --host 127.0.0.1 --port ${FRONTEND_PORT}"
  else
    FRONTEND_COMMAND="if [[ ! -x node_modules/.bin/vite ]]; then npm install --no-package-lock; fi; VITE_API_PROXY_TARGET=http://127.0.0.1:${BACKEND_PORT} npm run dev -- --host 127.0.0.1 --port ${FRONTEND_PORT}"
  fi
fi
WITH_SERVER="${WEBAPP_WITH_SERVER:-/home/batman/.claude/skills/webapp-testing/scripts/with_server.py}"

mkdir -p "${ARTIFACT_DIR}"

if ! curl -fsS "${PHOENIX_BASE_URL}/healthz" >/dev/null; then
  echo "Shared Phoenix is not healthy at ${PHOENIX_BASE_URL}/healthz" >&2
  echo "Start the shared local Phoenix before running live Deep Research." >&2
  exit 2
fi
if [[ "${CHAT_DEMO_ALLOW_FAKE_LIVE:-0}" != "1" ]]; then
  PROVIDER_VALUE="${AGENT_DRIVER_PROVIDER:-fake}"
  API_KEY_VALUE="${AGENT_DRIVER_API_KEY:-}"
  if [[ "${PROVIDER_VALUE}" == "fake" || -z "${API_KEY_VALUE}" ]]; then
    echo "Live Deep Research requires a real provider and AGENT_DRIVER_API_KEY." >&2
    echo "Set AGENT_DRIVER_PROVIDER, AGENT_DRIVER_API_KEY, model/base URL env, then rerun." >&2
    echo "Use CHAT_DEMO_ALLOW_FAKE_LIVE=1 only for UI plumbing checks." >&2
    exit 2
  fi
fi

export CHAT_DEMO_LIVE_ARTIFACT_DIR="${ARTIFACT_DIR}"
export CHAT_DEMO_URL="http://127.0.0.1:${FRONTEND_PORT}"
export CHAT_DEMO_LIVE_REQUIRE_OBSERVABILITY="${CHAT_DEMO_LIVE_REQUIRE_OBSERVABILITY:-1}"
export CHAT_DEMO_TRACING_ENABLED="${CHAT_DEMO_TRACING_ENABLED:-true}"
export PHOENIX_PROJECT_NAME="${PHOENIX_PROJECT_NAME:-agent-driver-chat-demo}"
export PHOENIX_COLLECTOR_ENDPOINT="${PHOENIX_ENDPOINT}"
export AGENT_DRIVER_RUNTIME_STORE_KIND="${AGENT_DRIVER_RUNTIME_STORE_KIND:-sqlite}"
export AGENT_DRIVER_SQLITE_PATH="${CHAT_DEMO_LIVE_SQLITE_PATH:-${ARTIFACT_DIR}/runtime_store.sqlite3}"
export CHAT_DEMO_SESSIONS_PATH="${CHAT_DEMO_LIVE_SESSIONS_PATH:-${ARTIFACT_DIR}/sessions.json}"
export CHAT_DEMO_WORKSPACE_ROOT="${CHAT_DEMO_LIVE_WORKSPACE_ROOT:-${ARTIFACT_DIR}/workspace}"

{
  echo "artifact_dir=${ARTIFACT_DIR}"
  echo "chat_demo_url=${CHAT_DEMO_URL}"
  echo "phoenix_endpoint=${PHOENIX_COLLECTOR_ENDPOINT}"
  echo "phoenix_base_url=${PHOENIX_BASE_URL}"
  echo "phoenix_project=${PHOENIX_PROJECT_NAME}"
  echo "runtime_store=${AGENT_DRIVER_RUNTIME_STORE_KIND}:${AGENT_DRIVER_SQLITE_PATH}"
  echo "sessions_path=${CHAT_DEMO_SESSIONS_PATH}"
  echo "workspace_root=${CHAT_DEMO_WORKSPACE_ROOT}"
  echo "profiles=${PROFILES}"
  echo "question_id=${QUESTION_ID}"
  echo "limit=${LIMIT}"
  echo "preflight_scenario=${PREFLIGHT_SCENARIO}"
  echo "preflight_cache=${PREFLIGHT_CACHE}"
  echo "server_timeout=${SERVER_TIMEOUT}"
  echo "command_timeout=${COMMAND_TIMEOUT}"
  echo "python_cmd=${PYTHON_CMD}"
  echo "backend_python_cmd=${BACKEND_PYTHON_CMD}"
  echo "frontend_command=${FRONTEND_COMMAND}"
  echo "with_server=${WITH_SERVER}"
} | tee "${ARTIFACT_DIR}/run-env.txt"

set +e
(
  ${PYTHON_CMD} "${WITH_SERVER}" \
    --timeout "${SERVER_TIMEOUT}" \
    --server "cd examples/chat-demo/backend && CHAT_DEMO_WORKSPACE_ROOT='${CHAT_DEMO_WORKSPACE_ROOT}' CHAT_DEMO_SESSIONS_PATH='${CHAT_DEMO_SESSIONS_PATH}' AGENT_DRIVER_RUNTIME_STORE_KIND='${AGENT_DRIVER_RUNTIME_STORE_KIND}' AGENT_DRIVER_SQLITE_PATH='${AGENT_DRIVER_SQLITE_PATH}' ${BACKEND_PYTHON_CMD} -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port ${BACKEND_PORT}" \
    --port "${BACKEND_PORT}" \
    --server "cd examples/chat-demo/frontend && ${FRONTEND_COMMAND}" \
    --port "${FRONTEND_PORT}" \
    -- \
    env \
      CHAT_DEMO_URL="${CHAT_DEMO_URL}" \
      CHAT_DEMO_LIVE_ARTIFACT_DIR="${ARTIFACT_DIR}" \
      CHAT_DEMO_LIVE_REQUIRE_OBSERVABILITY="${CHAT_DEMO_LIVE_REQUIRE_OBSERVABILITY}" \
      PREFLIGHT_SCENARIO="${PREFLIGHT_SCENARIO}" \
      PREFLIGHT_CACHE="${PREFLIGHT_CACHE}" \
      PYTHON_CMD="${PYTHON_CMD}" \
      ROOT_DIR="${ROOT_DIR}" \
      PROFILES="${PROFILES}" \
      QUESTION_ID="${QUESTION_ID}" \
      LIMIT="${LIMIT}" \
      timeout "${COMMAND_TIMEOUT}" bash -lc '
        set -euo pipefail
        cd "${ROOT_DIR}"
        mkdir -p "$(dirname "${PREFLIGHT_CACHE}")"
        fingerprint="$(
          sha256sum \
            agent_driver/runtime/single_agent/llm_step/__init__.py \
            agent_driver/runtime/single_agent/llm_step/request.py \
            agent_driver/runtime/single_agent/llm_step/completion.py \
            agent_driver/runtime/single_agent/tool_stage/__init__.py \
            agent_driver/runtime/single_agent/tool_stage/research.py \
            agent_driver/runtime/single_agent/tool_stage/subagents.py \
            agent_driver/runtime/single_agent/tool_stage/subagent_execution.py \
            agent_driver/runtime/single_agent/tool_stage/planning.py \
            agent_driver/subagents/workers.py \
            agent_driver/runtime/single_agent/lifecycle/steps.py \
            agent_driver/runtime/research_session_contract.py \
            agent_driver/runtime/research_artifacts.py \
            agent_driver/runtime/deep_research_phase_gate.py \
            agent_driver/observability/run_trace/summary.py \
            scripts/run_deep_research_live_observed.sh \
            examples/chat-demo/backend/app/api/chat.py \
            examples/chat-demo/backend/app/api/tools.py \
            examples/chat-demo/backend/app/sse_relay.py \
            examples/chat-demo/frontend/src/lib/sse.ts \
            examples/chat-demo/frontend/src/hooks/useRunStream.ts \
            examples/chat-demo/frontend/tests/e2e/chat_live_probe.py \
          | sha256sum | cut -d " " -f 1
        )"
        cached="$(cat "${PREFLIGHT_CACHE}" 2>/dev/null || true)"
        if [[ "${DEEP_RESEARCH_FORCE_PREFLIGHT:-0}" == "1" || "${cached}" != "${fingerprint}" ]]; then
          echo "Running live model/tool preflight: ${PREFLIGHT_SCENARIO}"
          ${PYTHON_CMD} examples/chat-demo/frontend/tests/e2e/chat_live_probe.py \
            --scenario "${PREFLIGHT_SCENARIO}"
          printf "%s" "${fingerprint}" > "${PREFLIGHT_CACHE}"
        else
          echo "Skipping live model/tool preflight; code fingerprint unchanged."
        fi
        ${PYTHON_CMD} scripts/deep_research_live_matrix.py \
          --profiles "${PROFILES}" \
          --question-id "${QUESTION_ID}" \
          --limit "${LIMIT}"
      '
) 2>&1 | tee "${ARTIFACT_DIR}/live-run.log"
RUN_STATUS="${PIPESTATUS[0]}"
set -e

${PYTHON_CMD} scripts/export_phoenix_evidence.py \
  --base-url "${PHOENIX_BASE_URL}" \
  --project "${PHOENIX_PROJECT_NAME}" \
  --out "${ARTIFACT_DIR}/phoenix-evidence.json" \
  || true

exit "${RUN_STATUS}"
