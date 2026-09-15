#!/usr/bin/env bash
# datadeck Docker 一键管理命令
# 用法：./docker-manage.sh {init|up|restart|rebuild|stop|down|status|logs|inject|smoke|tool-probe|fault-drill|config}

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$PROJECT_DIR/.env.docker"
cd "$PROJECT_DIR"

die() { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }

require_docker() {
  command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装 Docker Engine。"
  docker compose version >/dev/null 2>&1 || die "未找到 Docker Compose 插件，请安装 docker compose v2。"
}

prepare_workspace() {
  [[ -f "$PROJECT_DIR/docker-compose.yml" ]] || die "缺少 docker-compose.yml"
  [[ -d "$PROJECT_DIR/data_material" ]] || die "缺少 data_material 目录，无法注入 DataAgent 物料"
  mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/data/temp-sessions" "$PROJECT_DIR/uploads"
  if [[ ! -f "$ENV_FILE" ]]; then
    [[ -f "$PROJECT_DIR/.env.docker.example" ]] || die "缺少 .env.docker.example"
    cp "$PROJECT_DIR/.env.docker.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    warn "已生成 $ENV_FILE；请填写模型 API Key 和 JWT_SECRET_KEY 后再启动。"
  fi
}

compose() { docker compose --env-file "$ENV_FILE" "$@"; }

validate_compose_config() {
  compose config --quiet || die "Compose 配置校验失败，请检查 $ENV_FILE"
}

env_value() {
  local key="$1" value
  value="$(awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")"
  value="${value#\"}"; value="${value%\"}"
  value="${value#\'}"; value="${value%\'}"
  printf '%s' "$value"
}

validate_runtime_env() {
  local environment jwt_secret postgres_password allow_all
  environment="$(env_value DATADECK_ENV | tr '[:upper:]' '[:lower:]')"
  jwt_secret="$(env_value JWT_SECRET_KEY)"
  postgres_password="$(env_value POSTGRES_PASSWORD)"
  allow_all="$(env_value DATADECK_AGENT_ALLOW_ALL_ACTIONS | tr '[:upper:]' '[:lower:]')"

  if [[ "$environment" == "prod" || "$environment" == "production" ]]; then
    [[ "$allow_all" != "true" ]] || die "生产环境禁止 DATADECK_AGENT_ALLOW_ALL_ACTIONS=true"
    [[ "${#jwt_secret}" -ge 32 ]] || die "生产环境 JWT_SECRET_KEY 至少需要 32 个字符"
    [[ "$jwt_secret" != *please-change* && "$jwt_secret" != *change-me* ]] || \
      die "请先替换 .env.docker 中的示例 JWT_SECRET_KEY"
  fi

  [[ -n "$postgres_password" ]] || die "必须配置 POSTGRES_PASSWORD"
  [[ "$postgres_password" != "datadeck_password" && "$postgres_password" != *please-change* ]] || \
    die "请先替换 .env.docker 中的示例 POSTGRES_PASSWORD"
}

validate_config() {
  validate_compose_config
  validate_runtime_env
}

start_dependencies() {
  info "确保 PostgreSQL、Qdrant、Redis 已启动并健康..."
  compose up -d --wait postgres qdrant redis
}

wait_for_app() {
  local container health i
  for i in {1..60}; do
    container="$(compose ps -q app 2>/dev/null || true)"
    if [[ -n "$container" ]]; then
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
      case "$health" in
        healthy) info "应用已就绪：http://localhost:${DATADECK_PORT:-8000}"; return 0 ;;
        unhealthy|dead)
          warn "应用未能通过健康检查，最近日志："
          compose logs --tail=80 app
          return 1
          ;;
      esac
    fi
    sleep 2
  done
  warn "等待应用健康检查超时，当前状态："
  compose ps
  compose logs --tail=80 app
  return 1
}

inject_materials() {
  info "执行数据库迁移并幂等注入 DataAgent 物料..."
  compose run --rm --no-deps app sh -c 'alembic upgrade head && python scripts/import_dataagent_materials.py'
}

start() {
  require_docker
  prepare_workspace
  validate_config
  info "构建并启动最新代码（数据卷保留）..."
  start_dependencies
  compose up -d --build --force-recreate --no-deps app
  wait_for_app
  inject_materials
}

case "${1:-help}" in
  init)
    require_docker; prepare_workspace; validate_compose_config
    info "初始化完成。首次部署执行：$0 up"
    ;;
  up|start|restart) start ;;
  rebuild)
    require_docker; prepare_workspace; validate_config
    info "无缓存构建并启动最新代码（数据卷保留）..."
    compose build --no-cache app
    start_dependencies
    compose up -d --force-recreate --no-deps app
    wait_for_app
    inject_materials
    ;;
  stop) require_docker; compose stop ;;
  down)
    require_docker
    # 不使用 -v：PostgreSQL、Qdrant、Redis 数据必须保留。
    compose down
    ;;
  status) require_docker; compose ps ;;
  logs) require_docker; compose logs -f --tail="${2:-100}" ;;
  inject)
    require_docker; prepare_workspace; validate_config; inject_materials
    ;;
  smoke)
    require_docker; prepare_workspace; validate_config
    info "执行 Docker 依赖、健康检查和受保护 API smoke 检查..."
    bash "$PROJECT_DIR/scripts/docker_smoke.sh"
    ;;
  tool-probe)
    require_docker; prepare_workspace; validate_config
    info "在应用容器网络中执行 Agent 全工具探针（不调用模型、不保留测试数据）..."
    compose run --rm --no-deps app python scripts/agent_tool_probe.py
    ;;
  fault-drill)
    require_docker; prepare_workspace; validate_config
    info "执行 Docker 故障恢复演练；不会删除数据卷。"
    bash "$PROJECT_DIR/scripts/docker_fault_drill.sh"
    ;;
  config)
    require_docker; prepare_workspace; validate_config
    info "Compose 配置校验通过（未输出密钥）。"
    ;;
  help|*)
    echo "用法: $0 {init|up|restart|rebuild|stop|down|status|logs [行数]|inject|smoke|tool-probe|fault-drill|config}"
    echo "  init      检查环境、创建挂载目录和 .env.docker"
    echo "  up        构建最新代码并启动，等待应用健康"
    echo "  restart   同 up，强制重建应用容器"
    echo "  rebuild   无缓存构建并启动，排查缓存问题时使用"
    echo "  stop      停止服务，保留容器和数据卷"
    echo "  down      删除容器和网络，不删除数据卷"
    echo "  status    查看服务状态和健康检查"
    echo "  logs      查看服务日志，默认最近 100 行并持续跟踪"
    echo "  inject    执行迁移并幂等注入业务物料"
    echo "  smoke     检查容器健康和核心 API；可用 SMOKE_TOKEN 或 SMOKE_USERNAME/SMOKE_PASSWORD 做鉴权检查"
    echo "  tool-probe 在容器网络内验证能力包、Agent 装配、SQL、OMD、RAG、指标、Skill、工作区、任务和 MCP"
    echo "  fault-drill 需 FAULT_DRILL_CONFIRM=YES；演练依赖故障、应用重启和可选 SSE 断流，不删除数据卷"
    echo "  config    校验 Compose 配置但不输出密钥"
    ;;
esac
