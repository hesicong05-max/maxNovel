#!/usr/bin/env bash
set -euo pipefail

umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
DEMO_ROOT="${BACKEND_DIR}/data/demo"
HEALTHCHECK="${SCRIPT_DIR}/demo-healthcheck.py"

usage() {
  cat <<'USAGE'
Usage:
  scripts/demo-local.sh start [--run-id ID] [--backend-port PORT] [--frontend-port PORT]
  scripts/demo-local.sh resume --run-id ID
  scripts/demo-local.sh check --run-id ID
  scripts/demo-local.sh status --run-id ID
  scripts/demo-local.sh stop --run-id ID
  scripts/demo-local.sh credentials --run-id ID

Environment overrides used only by start:
  PYTHON_BIN=/absolute/path/to/python
  VITE_BIN=/absolute/path/to/vite
USAGE
}

fail() {
  printf 'demo-local: %s\n' "$*" >&2
  exit 1
}

command_name="${1:-}"
[[ -n "${command_name}" ]] || { usage; exit 2; }
shift

run_id=""
backend_port="8000"
frontend_port="5173"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-id)
      [[ $# -ge 2 ]] || fail "--run-id requires a value"
      run_id="$2"
      shift 2
      ;;
    --backend-port)
      [[ $# -ge 2 ]] || fail "--backend-port requires a value"
      backend_port="$2"
      shift 2
      ;;
    --frontend-port)
      [[ $# -ge 2 ]] || fail "--frontend-port requires a value"
      frontend_port="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

if [[ "${command_name}" == "start" && -z "${run_id}" ]]; then
  run_id="demo-$(date -u +%Y%m%dT%H%M%SZ)-$(python3 -c 'import secrets; print(secrets.token_hex(3))')"
fi
[[ "${run_id}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{2,47}$ ]] || fail "run-id must match [A-Za-z0-9][A-Za-z0-9_-]{2,47}"
[[ "${backend_port}" =~ ^[0-9]{2,5}$ ]] || fail "invalid backend port"
[[ "${frontend_port}" =~ ^[0-9]{2,5}$ ]] || fail "invalid frontend port"
(( backend_port >= 1024 && backend_port <= 65535 )) || fail "backend port must be 1024-65535"
(( frontend_port >= 1024 && frontend_port <= 65535 )) || fail "frontend port must be 1024-65535"
[[ "${backend_port}" != "${frontend_port}" ]] || fail "backend and frontend ports must differ"

run_dir="${DEMO_ROOT}/${run_id}"
marker="${run_dir}/run.json"
credentials_file="${run_dir}/credentials.json"
database="${run_dir}/demo.db"
backend_pid_file="${run_dir}/backend.pid"
frontend_pid_file="${run_dir}/frontend.pid"

json_get() {
  local key="$1"
  python3 - "${marker}" "${key}" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
item = value
for part in sys.argv[2].split("."):
    item = item[part]
print(item)
PY
}

validate_existing_run() {
  validate_demo_paths
  [[ -d "${run_dir}" && ! -L "${run_dir}" ]] || fail "run directory missing or unsafe: ${run_dir}"
  [[ -f "${marker}" && ! -L "${marker}" ]] || fail "run marker missing or unsafe"
  [[ -f "${credentials_file}" && ! -L "${credentials_file}" ]] || fail "credentials missing or unsafe"
  [[ -f "${database}" && ! -L "${database}" ]] || fail "database missing or unsafe"
  [[ "$(json_get schema_version)" == "1" ]] || fail "unsupported run marker schema"
  [[ "$(json_get run_id)" == "${run_id}" ]] || fail "run marker identity mismatch"
  [[ "$(json_get root)" == "${ROOT_DIR}" ]] || fail "run marker root mismatch"
  [[ "$(json_get database)" == "${database}" ]] || fail "database path mismatch"
  local stored_backend_port stored_frontend_port
  stored_backend_port="$(json_get backend_port)"
  stored_frontend_port="$(json_get frontend_port)"
  [[ "${stored_backend_port}" =~ ^[0-9]{2,5}$ ]] || fail "invalid stored backend port"
  [[ "${stored_frontend_port}" =~ ^[0-9]{2,5}$ ]] || fail "invalid stored frontend port"
  (( stored_backend_port >= 1024 && stored_backend_port <= 65535 )) || fail "stored backend port is unsafe"
  (( stored_frontend_port >= 1024 && stored_frontend_port <= 65535 )) || fail "stored frontend port is unsafe"
  [[ "${stored_backend_port}" != "${stored_frontend_port}" ]] || fail "stored ports must differ"
}

validate_demo_paths() {
  [[ ! -L "${BACKEND_DIR}/data" ]] || fail "backend/data must not be a symlink"
  [[ ! -L "${DEMO_ROOT}" ]] || fail "demo root must not be a symlink"
  [[ ! -L "${run_dir}" ]] || fail "run directory must not be a symlink"
  [[ ! -L "${database}" ]] || fail "database must not be a symlink"
  python3 - "${BACKEND_DIR}" "${DEMO_ROOT}" "${run_dir}" "${database}" "${run_id}" <<'PY'
from pathlib import Path
import sys

backend = Path(sys.argv[1]).resolve(strict=True)
demo_root = Path(sys.argv[2]).resolve(strict=False)
run_dir = Path(sys.argv[3]).resolve(strict=False)
database = Path(sys.argv[4]).resolve(strict=False)
run_id = sys.argv[5]
expected_demo_root = backend / "data" / "demo"
expected_run_dir = expected_demo_root / run_id
if demo_root != expected_demo_root:
    raise SystemExit("demo root escapes the checkout")
if run_dir != expected_run_dir or run_dir.parent != demo_root:
    raise SystemExit("run directory escapes the demo root")
if database != expected_run_dir / "demo.db" or database.parent != run_dir:
    raise SystemExit("database escapes the run directory")
PY
}

port_is_free() {
  python3 - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit(1)
PY
}

process_matches() {
  local pid="$1"
  local role="$2"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  local command python_bin vite_bin stored_backend_port stored_frontend_port
  command="$(ps -p "${pid}" -o command= 2>/dev/null)" || return 1
  python_bin="$(json_get python_bin)"
  vite_bin="$(json_get vite_bin)"
  stored_backend_port="$(json_get backend_port)"
  stored_frontend_port="$(json_get frontend_port)"
  case "${role}" in
    backend)
      [[ "${command}" == *"${python_bin}"* ]] &&
        [[ "${command}" == *"-m uvicorn app.main:app"* ]] &&
        [[ "${command}" == *"--host 127.0.0.1"* ]] &&
        [[ "${command}" == *"--port ${stored_backend_port}"* ]]
      ;;
    frontend)
      [[ "${command}" == *"${vite_bin}"* ]] &&
        [[ "${command}" == *"--host 127.0.0.1"* ]] &&
        [[ "${command}" == *"--port ${stored_frontend_port}"* ]]
      ;;
    *) return 1 ;;
  esac
}

stop_one() {
  local pid_file="$1"
  local role="$2"
  [[ -f "${pid_file}" && ! -L "${pid_file}" ]] || return 0
  local pid
  pid="$(tr -d '[:space:]' < "${pid_file}")"
  if ! kill -0 "${pid}" 2>/dev/null; then
    return 0
  fi
  process_matches "${pid}" "${role}" || fail "refusing to stop unknown process ${pid}"
  kill -TERM "${pid}"
  for _ in {1..40}; do
    kill -0 "${pid}" 2>/dev/null || return 0
    sleep 0.25
  done
  process_matches "${pid}" "${role}" || fail "process identity changed while stopping ${pid}"
  kill -KILL "${pid}"
}

stop_services() {
  stop_one "${frontend_pid_file}" frontend
  stop_one "${backend_pid_file}" backend
}

check_no_llm_key() {
  local python_bin="$1"
  (
    cd "${BACKEND_DIR}"
    env \
      ENV_FILE=/dev/null \
      APP_ENVIRONMENT=demo \
      DEBUG=true \
      DEMO_FIXTURE_ENABLED=true \
      JWT_SECRET=demo-preflight-only \
      LLM_API_KEY= \
      SENTRY_DSN= \
      "${python_bin}" -c 'from app.core.settings_store import load_settings; raise SystemExit(1 if bool(load_settings().get("api_key")) else 0)'
  ) || fail "an LLM API key is configured; use a clean worktree and do not modify the key file"
}

create_credentials() {
  python3 - "${credentials_file}" "${run_id}" <<'PY'
import hashlib
import json
import pathlib
import secrets
import sys

path = pathlib.Path(sys.argv[1])
run_id = sys.argv[2]
suffix = hashlib.sha256(run_id.encode()).hexdigest()[:10]
payload = {
    "run_id": run_id,
    "email": f"demo.{suffix}@example.com",
    "username": f"demo-{suffix}",
    "password": secrets.token_urlsafe(18),
}
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
}

write_marker() {
  local sha="$1" python_bin="$2" vite_bin="$3"
  python3 - "${marker}" "${run_id}" "${sha}" "${ROOT_DIR}" "${database}" \
    "${backend_port}" "${frontend_port}" "${python_bin}" "${vite_bin}" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "run_id": sys.argv[2],
    "git_sha": sys.argv[3],
    "root": sys.argv[4],
    "database": sys.argv[5],
    "backend_port": int(sys.argv[6]),
    "frontend_port": int(sys.argv[7]),
    "python_bin": sys.argv[8],
    "vite_bin": sys.argv[9],
}
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
}

start_services() {
  local python_bin="$1" vite_bin="$2" stored_backend_port="$3" stored_frontend_port="$4"
  local jwt_secret api_base frontend_url
  jwt_secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  api_base="http://127.0.0.1:${stored_backend_port}/api"
  frontend_url="http://127.0.0.1:${stored_frontend_port}"
  local -a backend_env=(
    ENV_FILE=/dev/null
    APP_ENVIRONMENT=demo
    DEBUG=true
    DEMO_FIXTURE_ENABLED=true
    DATABASE_URL="sqlite+aiosqlite:///${database}"
    JWT_SECRET="${jwt_secret}"
    LLM_API_KEY=
    LEGACY_JSON_WRITES_FROZEN=false
    RATE_LIMIT_STORAGE_URI=memory://
    SENTRY_DSN=
    SENTRY_SEND_PII=false
    CORS_ORIGINS="${frontend_url}"
    HOST=127.0.0.1
    PORT="${stored_backend_port}"
  )

  (
    cd "${BACKEND_DIR}"
    nohup env "${backend_env[@]}" "${python_bin}" -m uvicorn app.main:app \
      --host 127.0.0.1 --port "${stored_backend_port}" \
      >"${run_dir}/backend.log" 2>&1 &
    echo $! > "${backend_pid_file}"
  )
  (
    cd "${FRONTEND_DIR}"
    nohup env VITE_API_BASE_URL="${api_base}" VITE_SENTRY_DSN= "${vite_bin}" \
      --host 127.0.0.1 --port "${stored_frontend_port}" --strictPort \
      >"${run_dir}/frontend.log" 2>&1 &
    echo $! > "${frontend_pid_file}"
  )
}

run_healthcheck() {
  local stored_backend_port stored_frontend_port
  stored_backend_port="$(json_get backend_port)"
  stored_frontend_port="$(json_get frontend_port)"
  python3 "${HEALTHCHECK}" \
    --api-base "http://127.0.0.1:${stored_backend_port}/api" \
    --frontend-url "http://127.0.0.1:${stored_frontend_port}" \
    --database "${database}" \
    --credentials-file "${credentials_file}" "$@"
}

case "${command_name}" in
  start)
    validate_demo_paths
    [[ ! -e "${run_dir}" && ! -L "${run_dir}" ]] || fail "run-id already exists; choose a new run-id"
    mkdir -p "${DEMO_ROOT}"
    validate_demo_paths
    mkdir "${run_dir}"
    validate_demo_paths
    python_bin="${PYTHON_BIN:-${BACKEND_DIR}/.venv/bin/python}"
    vite_bin="${VITE_BIN:-${FRONTEND_DIR}/node_modules/.bin/vite}"
    [[ "${python_bin}" = /* && -x "${python_bin}" ]] || fail "PYTHON_BIN must be an executable absolute path"
    [[ "${vite_bin}" = /* && -x "${vite_bin}" ]] || fail "VITE_BIN must be an executable absolute path"
    port_is_free "${backend_port}" || fail "backend port is already in use"
    port_is_free "${frontend_port}" || fail "frontend port is already in use"
    check_no_llm_key "${python_bin}"
    create_credentials
    sha="$(git -C "${ROOT_DIR}" rev-parse HEAD)"
    write_marker "${sha}" "${python_bin}" "${vite_bin}"
    (
      cd "${BACKEND_DIR}"
      env \
        ENV_FILE=/dev/null APP_ENVIRONMENT=demo DEBUG=true DEMO_FIXTURE_ENABLED=true \
        DATABASE_URL="sqlite+aiosqlite:///${database}" JWT_SECRET=demo-migration-only \
        LLM_API_KEY= SENTRY_DSN= \
        "${python_bin}" -m alembic upgrade head
    ) >"${run_dir}/migration.log" 2>&1 || fail "migration failed; see ${run_dir}/migration.log"
    start_services "${python_bin}" "${vite_bin}" "${backend_port}" "${frontend_port}"
    if ! run_healthcheck --initialize-user --bootstrap-if-missing; then
      stop_services || true
      fail "initial healthcheck failed; logs and database were preserved in ${run_dir}"
    fi
    printf 'Demo ready\nrun-id: %s\nfrontend: http://127.0.0.1:%s\ncredentials: %s credentials --run-id %s\n' \
      "${run_id}" "${frontend_port}" "$0" "${run_id}"
    ;;
  resume)
    validate_existing_run
    current_sha="$(git -C "${ROOT_DIR}" rev-parse HEAD)"
    [[ "$(json_get git_sha)" == "${current_sha}" ]] || fail "run SHA differs from current checkout"
    backend_port="$(json_get backend_port)"
    frontend_port="$(json_get frontend_port)"
    python_bin="$(json_get python_bin)"
    vite_bin="$(json_get vite_bin)"
    [[ -x "${python_bin}" && -x "${vite_bin}" ]] || fail "recorded runtime is unavailable"
    port_is_free "${backend_port}" || fail "backend port is already in use"
    port_is_free "${frontend_port}" || fail "frontend port is already in use"
    check_no_llm_key "${python_bin}"
    start_services "${python_bin}" "${vite_bin}" "${backend_port}" "${frontend_port}"
    if ! run_healthcheck; then
      stop_services || true
      fail "resume healthcheck failed; run data was preserved"
    fi
    printf 'Demo resumed: %s at http://127.0.0.1:%s\n' "${run_id}" "${frontend_port}"
    ;;
  check)
    validate_existing_run
    run_healthcheck
    ;;
  status)
    validate_existing_run
    stored_backend_port="$(json_get backend_port)"
    stored_frontend_port="$(json_get frontend_port)"
    backend_pid="$(tr -d '[:space:]' < "${backend_pid_file}" 2>/dev/null || true)"
    frontend_pid="$(tr -d '[:space:]' < "${frontend_pid_file}" 2>/dev/null || true)"
    backend_state=stopped
    frontend_state=stopped
    process_matches "${backend_pid}" backend && backend_state=running
    process_matches "${frontend_pid}" frontend && frontend_state=running
    printf 'run-id=%s sha=%s backend=%s frontend=%s url=http://127.0.0.1:%s\n' \
      "${run_id}" "$(json_get git_sha)" "${backend_state}" "${frontend_state}" "${stored_frontend_port}"
    ;;
  stop)
    validate_existing_run
    stop_services
    printf 'Demo stopped; data preserved at %s\n' "${run_dir}"
    ;;
  credentials)
    [[ -f "${credentials_file}" && ! -L "${credentials_file}" ]] || fail "credentials missing or unsafe"
    python3 - "${credentials_file}" <<'PY'
import json
import pathlib
import sys

value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(f"run-id: {value['run_id']}")
print(f"email: {value['email']}")
print(f"password: {value['password']}")
PY
    ;;
  *)
    usage
    exit 2
    ;;
esac
