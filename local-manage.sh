#!/usr/bin/env bash
# DataDeck 本地开发一键管理
# 用法：./local-manage.sh {start|stop|restart|status|logs}

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${DATADECK_LOCAL_VENV:-$PROJECT_DIR/.venv}"
RUNTIME_DIR="$PROJECT_DIR/tmp/local-runtime"
BACKEND_PID="$RUNTIME_DIR/backend.pid"
FRONTEND_PID="$RUNTIME_DIR/frontend.pid"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
FRONTEND_LOG="$RUNTIME_DIR/frontend.log"

cd "$PROJECT_DIR"
mkdir -p "$RUNTIME_DIR"

info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
die() { echo "[ERROR] $*" >&2; exit 1; }

env_value() {
  local key="$1" value
  [[ -f "$PROJECT_DIR/.env" ]] || return 0
  value="$(awk -F= -v key="$key" '$1 ~ "^[[:space:]]*" key "[[:space:]]*$" {sub(/^[^=]*=/, ""); print; exit}' "$PROJECT_DIR/.env")"
  value="${value#\"}"; value="${value%\"}"
  value="${value#\'}"; value="${value%\'}"
  printf '%s' "$value"
}

pid_from() {
  local file="$1" pid
  [[ -f "$file" ]] || return 1
  pid="$(tr -d '[:space:]' < "$file")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  printf '%s' "$pid"
}

check_pid_command() {
  local pid="$1" pattern="$2"
  ps -p "$pid" -o command= 2>/dev/null | grep -F -- "$pattern" >/dev/null
}

check_tools() {
  [[ -x "$VENV_DIR/bin/python" ]] || die "未找到虚拟环境：$VENV_DIR，请先创建并安装项目依赖。"
  command -v pnpm >/dev/null 2>&1 || die "未找到 pnpm，请先安装 pnpm。"
  [[ -d "$PROJECT_DIR/web/node_modules" ]] || die "前端依赖未安装，请执行：pnpm --dir web install"
}

check_dependencies() {
  local url
  for url in "${DATADECK_LOCAL_POSTGRES_HEALTH_URL:-}" "${DATADECK_LOCAL_QDRANT_HEALTH_URL:-http://127.0.0.1:6333/healthz}"; do
    [[ -z "$url" ]] && continue
    if ! curl --noproxy '*' --max-time 2 --silent --fail "$url" >/dev/null 2>&1; then
      warn "依赖服务未通过 HTTP 检查：$url（后端仍会启动，请确认本地服务已运行）"
    fi
  done
  if ! "$VENV_DIR/bin/python" - <<'PY'
import os, socket
dsn = os.getenv("DATABASE_URL", "postgresql+asyncpg://datadeck:datadeck_password@127.0.0.1:5432/datadeck")
host = dsn.split("@")[1].split("/")[0].split(":")[0] if "@" in dsn else "127.0.0.1"
port = int(dsn.split("@")[1].split("/")[0].rsplit(":", 1)[-1]) if "@" in dsn and ":" in dsn.split("@")[1].split("/")[0] else 5432
with socket.create_connection((host, port), timeout=2):
    pass
PY
  then
    warn "PostgreSQL TCP 检查失败；请确认 DATABASE_URL 对应的本地数据库已启动。"
  fi
}

stop_one() {
  local file="$1" pattern="$2" name="$3" pid
  pid="$(pid_from "$file" || true)"
  if [[ -n "$pid" ]] && check_pid_command "$pid" "$pattern"; then
    kill "$pid" 2>/dev/null || true
    for _ in {1..20}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    if kill -0 "$pid" 2>/dev/null; then
      warn "$name 未正常退出，发送 TERM 后跳过强制杀进程：$pid"
    fi
    info "已停止 $name：$pid"
  fi
  rm -f "$file"
}

start() {
  check_tools
  stop_one "$BACKEND_PID" "server.main:app" "后端"
  stop_one "$FRONTEND_PID" "vite" "前端"
  # .env 只作为 dotenv 配置文件读取，不能 source：Token 等值可能含有
  # shell 特殊字符，source 会把它们误当成命令执行。
  local database_url
  database_url="$(env_value DATABASE_URL)"
  [[ -n "$database_url" ]] && export DATABASE_URL="$database_url"
  check_dependencies

  info "执行数据库迁移..."
  "$VENV_DIR/bin/alembic" upgrade head

  info "启动后端：http://127.0.0.1:${DATADECK_LOCAL_BACKEND_PORT:-8000}"
  nohup "$VENV_DIR/bin/python" -m uvicorn server.main:app \
    --host "${DATADECK_LOCAL_BACKEND_HOST:-127.0.0.1}" \
    --port "${DATADECK_LOCAL_BACKEND_PORT:-8000}" \
    >"$BACKEND_LOG" 2>&1 &
  echo $! > "$BACKEND_PID"

  info "启动前端：http://127.0.0.1:${DATADECK_LOCAL_FRONTEND_PORT:-5173}"
  nohup pnpm --dir web dev --host 127.0.0.1 --port "${DATADECK_LOCAL_FRONTEND_PORT:-5173}" \
    >"$FRONTEND_LOG" 2>&1 &
  echo $! > "$FRONTEND_PID"
  info "启动完成，日志目录：$RUNTIME_DIR"
}

stop() {
  stop_one "$FRONTEND_PID" "vite" "前端"
  stop_one "$BACKEND_PID" "server.main:app" "后端"
}

status() {
  local backend frontend
  backend="$(pid_from "$BACKEND_PID" || true)"
  frontend="$(pid_from "$FRONTEND_PID" || true)"
  [[ -n "$backend" ]] && check_pid_command "$backend" "server.main:app" && echo "backend: running ($backend)" || echo "backend: stopped"
  [[ -n "$frontend" ]] && check_pid_command "$frontend" "vite" && echo "frontend: running ($frontend)" || echo "frontend: stopped"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  logs)
    tail -f "$BACKEND_LOG" "$FRONTEND_LOG"
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status|logs}"
    exit 1
    ;;
esac
