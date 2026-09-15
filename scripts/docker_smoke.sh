#!/usr/bin/env bash
# Docker 部署 smoke 检查：不创建业务数据、不执行 Agent，不输出 Token/密码。

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env.docker"
cd "$PROJECT_DIR"

die() { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }

command -v curl >/dev/null 2>&1 || die "未找到 curl。"
command -v python3 >/dev/null 2>&1 || die "未找到 python3。"
[[ -f "$ENV_FILE" ]] || die "缺少 $ENV_FILE，请先执行 ./docker-manage.sh init。"

compose() { docker compose --env-file "$ENV_FILE" -f "$PROJECT_DIR/docker-compose.yml" "$@"; }
curl_json() {
  curl --silent --show-error --fail --connect-timeout 5 --max-time 20 "$@"
}

if [[ -n "${SMOKE_BASE_URL:-}" ]]; then
  BASE_URL="${SMOKE_BASE_URL%/}"
else
  mapped_port="$(compose port app 8000 2>/dev/null || true)"
  mapped_port="${mapped_port##*:}"
  BASE_URL="http://localhost:${mapped_port:-${DATADECK_PORT:-8000}}"
fi

check_service_health() {
  local service="$1" container state health
  container="$(compose ps -q "$service" 2>/dev/null || true)"
  [[ -n "$container" ]] || die "$service 容器不存在。"
  state="$(docker inspect --format '{{.State.Status}}' "$container")"
  [[ "$state" == "running" ]] || die "$service 容器状态为 $state。"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container")"
  [[ "$health" == "healthy" ]] || die "$service 健康状态为 $health。"
  info "$service: healthy"
}

info "检查 Compose 核心依赖..."
for service in postgres qdrant redis app; do
  check_service_health "$service"
done

health_body="$(curl_json "$BASE_URL/api/system/health")"
printf '%s' "$health_body" | python3 -c '
import json
import sys

try:
    body = json.load(sys.stdin)
except (IndexError, json.JSONDecodeError) as exc:
    raise SystemExit(f"健康接口返回不是 JSON: {exc}")
if body.get("status") != "ok":
    raise SystemExit(f"健康接口状态异常: {body}")
'
info "${BASE_URL}/api/system/health: ok"

token="${SMOKE_TOKEN:-}"
if [[ -z "$token" && -n "${SMOKE_USERNAME:-}" && -n "${SMOKE_PASSWORD:-}" ]]; then
  login_body="$(curl_json -X POST "$BASE_URL/api/auth/token" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode "username=$SMOKE_USERNAME" \
    --data-urlencode "password=$SMOKE_PASSWORD")"
  token="$(printf '%s' "$login_body" | python3 -c '
import json
import sys

try:
    value = json.load(sys.stdin).get("access_token", "")
except (json.JSONDecodeError, AttributeError):
    value = ""
print(value)
')"
  [[ -n "$token" ]] || die "登录 smoke 失败：响应未包含 access_token。"
  info "鉴权登录: ok"
fi

if [[ -z "$token" ]]; then
  warn "未提供 SMOKE_TOKEN 或 SMOKE_USERNAME/SMOKE_PASSWORD，跳过受保护 API 检查。"
  info "基础 Docker smoke 检查通过。"
  exit 0
fi

auth_header=( -H "Authorization: Bearer $token" )
for endpoint in \
  "/api/auth/me" \
  "/api/agent/backends" \
  "/api/agent/configurable-items?backend_id=DataAgent" \
  "/api/chat/threads" \
  "/api/agent" \
  "/api/system/tools" \
  "/api/system/mcp-servers" \
  "/api/skills/accessible" \
  "/api/knowledge/databases" \
  "/api/knowledge/materials?limit=1&offset=0" \
  "/api/metrics/registry?limit=1&offset=0" \
  "/api/scheduled-tasks"; do
  curl_json "${auth_header[@]}" "$BASE_URL$endpoint" >/dev/null
  info "$endpoint: ok"
done

info "Docker smoke 检查通过。未自动创建 Agent Run，避免消耗模型额度或写入业务数据。"
