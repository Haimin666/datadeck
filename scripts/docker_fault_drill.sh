#!/usr/bin/env bash
# DataDeck Docker 故障演练：只停止并恢复容器，不删除数据卷或业务数据。
# 必须显式设置 FAULT_DRILL_CONFIRM=YES 才会执行。

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env.docker"
cd "$PROJECT_DIR"

die() { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }

[[ "${FAULT_DRILL_CONFIRM:-}" == "YES" ]] || die \
  "故障演练会短暂停止依赖容器；请显式设置 FAULT_DRILL_CONFIRM=YES。"
command -v docker >/dev/null 2>&1 || die "未找到 docker。"
docker compose version >/dev/null 2>&1 || die "未找到 Docker Compose v2。"
command -v curl >/dev/null 2>&1 || die "未找到 curl。"
command -v python3 >/dev/null 2>&1 || die "未找到 python3。"
[[ -f "$ENV_FILE" ]] || die "缺少 $ENV_FILE，请先执行 ./docker-manage.sh init。"

compose() { docker compose --env-file "$ENV_FILE" -f "$PROJECT_DIR/docker-compose.yml" "$@"; }

wait_healthy() {
  local service="$1" container health i
  for i in {1..60}; do
    container="$(compose ps -q "$service" 2>/dev/null || true)"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
    if [[ "$health" == "healthy" ]]; then
      info "$service 已恢复 healthy"
      return 0
    fi
    sleep 2
  done
  compose ps
  compose logs --tail=60 "$service" || true
  die "$service 在规定时间内没有恢复 healthy。"
}

app_health() {
  local mapped_port base_url
  mapped_port="$(compose port app 8000 2>/dev/null || true)"
  mapped_port="${mapped_port##*:}"
  base_url="${SMOKE_BASE_URL:-http://localhost:${mapped_port:-${DATADECK_PORT:-8000}}}"
  curl --silent --show-error --fail --connect-timeout 5 --max-time 20 \
    "$base_url/api/system/health" | python3 -c '
import json
import sys
body = json.load(sys.stdin)
if body.get("status") != "ok":
    raise SystemExit(f"健康接口状态异常: {body}")
'
}

restore() {
  set +e
  info "恢复所有 DataDeck 服务（不触碰数据卷）..."
  compose up -d --wait postgres qdrant redis app >/dev/null 2>&1
  set -e
}
trap restore EXIT

for service in postgres qdrant redis app; do
  wait_healthy "$service"
done

info "演练 1/4：PostgreSQL 未就绪 → 应用重启 → 数据库恢复"
compose stop postgres
compose restart app >/dev/null
sleep 3
compose up -d --wait postgres
compose up -d --wait app
wait_healthy app
app_health

info "演练 2/4：Qdrant 不可用 → Qdrant 恢复"
compose stop qdrant
compose up -d --wait qdrant
wait_healthy qdrant
app_health

info "演练 3/4：Redis 重启 → Redis 恢复"
compose restart redis >/dev/null
wait_healthy redis
app_health

info "演练 4/4：应用重启 → 健康接口恢复"
compose restart app >/dev/null
wait_healthy app
app_health

if [[ -n "${FAULT_DRILL_RUN_ID:-}" ]]; then
  [[ -n "${FAULT_DRILL_TOKEN:-}" ]] || die "设置 FAULT_DRILL_RUN_ID 时必须同时提供 FAULT_DRILL_TOKEN。"
  mapped_port="$(compose port app 8000 2>/dev/null || true)"
  mapped_port="${mapped_port##*:}"
  base_url="${SMOKE_BASE_URL:-http://localhost:${mapped_port:-${DATADECK_PORT:-8000}}}"
  info "可选演练：主动断开 Run SSE（不创建新 Run）"
  set +e
  curl --silent --show-error --max-time "${FAULT_DRILL_SSE_SECONDS:-1}" \
    -H "Authorization: Bearer $FAULT_DRILL_TOKEN" \
    "$base_url/api/agent/runs/$FAULT_DRILL_RUN_ID/events?verbose=false" >/dev/null
  sse_rc=$?
  set -e
  [[ "$sse_rc" == 0 || "$sse_rc" == 28 ]] || die "SSE 断开演练失败，curl exit=$sse_rc。"
  curl --silent --show-error --fail --max-time 20 \
    -H "Authorization: Bearer $FAULT_DRILL_TOKEN" \
    "$base_url/api/agent/runs/$FAULT_DRILL_RUN_ID" >/dev/null
  info "Run 断流后仍可通过 run_id 查询。"
else
  info "未提供 FAULT_DRILL_RUN_ID，跳过 SSE 断流探针（不会创建 Agent Run）。"
fi

info "基础 Docker 故障演练通过；模型超时请在部署环境临时设置 DATADECK_MODEL_TIMEOUT 后单独验证。"
