#!/bin/bash
# datadeck 一键启停脚本
# 用法: ./manage.sh start|stop|restart|status|logs

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="/tmp"
BACKEND_PID_FILE="$LOG_DIR/datadeck-backend.pid"
FRONTEND_PID_FILE="$LOG_DIR/datadeck-frontend.pid"
BACKEND_LOG="$LOG_DIR/datadeck-server.log"
FRONTEND_LOG="$LOG_DIR/datadeck-web.log"
BACKEND_PORT=8000
FRONTEND_PORT=5173

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

build_frontend() {
    if ! command -v pnpm >/dev/null 2>&1; then
        log_error "未找到 pnpm，请先安装 pnpm"
        return 1
    fi

    log_info "构建最新前端代码..."
    cd "$PROJECT_DIR/web"
    pnpm build
    log_info "前端构建完成，后端将使用最新 web/dist"
}

check_port() {
    # 检查端口是否有 LISTEN 状态的服务
    lsof -ti :$1 -sTCP:LISTEN 2>/dev/null | head -1
}

kill_by_pidfile() {
    local pidfile=$1
    local name=$2
    if [ -f "$pidfile" ]; then
        local pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null && log_info "停止 $name (PID: $pid)"
            sleep 1
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$pidfile"
    fi
}

kill_by_port() {
    local port=$1
    local name=$2
    local pid=$(check_port $port)
    if [ -n "$pid" ]; then
        kill "$pid" 2>/dev/null && log_info "停止 $name (PID: $pid)"
        sleep 1
        kill -9 "$pid" 2>/dev/null || true
    fi
}

start_backend() {
    if [ -n "$(check_port $BACKEND_PORT)" ]; then
        log_warn "后端已在运行 (端口 $BACKEND_PORT)"
        return 0
    fi

    log_info "启动后端 (FastAPI + Uvicorn)..."
    cd "$PROJECT_DIR"
    
    # 使用 python -c 启动以确保完全脱离终端
    "$VENV_PYTHON" -c "
import subprocess, sys, os
os.chdir('$PROJECT_DIR')
proc = subprocess.Popen(
    [sys.executable, '-m', 'uvicorn', 'server.main:app', '--host', '0.0.0.0', '--port', '$BACKEND_PORT'],
    stdout=open('$BACKEND_LOG', 'w'),
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    start_new_session=True,
)
with open('$BACKEND_PID_FILE', 'w') as f:
    f.write(str(proc.pid))
print(f'Backend PID: {proc.pid}')
" 2>&1
    
    # 等待后端启动
    for i in {1..10}; do
        sleep 1
        if [ -n "$(check_port $BACKEND_PORT)" ]; then
            log_info "后端启动成功 http://localhost:$BACKEND_PORT"
            log_info "API 文档 http://localhost:$BACKEND_PORT/docs"
            return 0
        fi
    done
    
    log_error "后端启动失败，查看日志: $BACKEND_LOG"
    tail -20 "$BACKEND_LOG" 2>/dev/null
    return 1
}

start_frontend() {
    if [ -n "$(check_port $FRONTEND_PORT)" ]; then
        log_warn "前端已在运行 (端口 $FRONTEND_PORT)"
        return 0
    fi

    log_info "启动前端 (Vite Dev Server)..."
    cd "$PROJECT_DIR/web"
    
    # 使用 subprocess 启动以确保完全脱离终端
    "$VENV_PYTHON" -c "
import subprocess, sys, os
os.chdir('$PROJECT_DIR/web')
proc = subprocess.Popen(
    ['pnpm', 'dev'],
    stdout=open('$FRONTEND_LOG', 'w'),
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    start_new_session=True,
)
with open('$FRONTEND_PID_FILE', 'w') as f:
    f.write(str(proc.pid))
print(f'Frontend PID: {proc.pid}')
" 2>&1
    
    # 等待前端启动
    for i in {1..15}; do
        sleep 1
        if [ -n "$(check_port $FRONTEND_PORT)" ]; then
            log_info "前端启动成功 http://localhost:$FRONTEND_PORT"
            return 0
        fi
    done
    
    log_error "前端启动失败，查看日志: $FRONTEND_LOG"
    tail -10 "$FRONTEND_LOG" 2>/dev/null
    return 1
}

stop_backend() {
    kill_by_pidfile "$BACKEND_PID_FILE" "后端"
    kill_by_port $BACKEND_PORT "后端"
}

stop_frontend() {
    kill_by_pidfile "$FRONTEND_PID_FILE" "前端"
    kill_by_port $FRONTEND_PORT "前端"
}

show_status() {
    echo ""
    echo "=== datadeck 服务状态 ==="
    echo ""

    # 后端
    local backend_pid=$(check_port $BACKEND_PORT)
    if [ -n "$backend_pid" ]; then
        echo -e "  后端:  ${GREEN}运行中${NC} (PID: $backend_pid, 端口: $BACKEND_PORT)"
        echo "         http://localhost:$BACKEND_PORT"
        echo "         http://localhost:$BACKEND_PORT/docs"
    else
        echo -e "  后端:  ${RED}未运行${NC}"
    fi

    # 前端
    local frontend_pid=$(check_port $FRONTEND_PORT)
    if [ -n "$frontend_pid" ]; then
        echo -e "  前端:  ${GREEN}运行中${NC} (PID: $frontend_pid, 端口: $FRONTEND_PORT)"
        echo "         http://localhost:$FRONTEND_PORT"
    else
        echo -e "  前端:  ${RED}未运行${NC}"
    fi

    # 数据库
    if psql "postgresql://lbc@localhost:5432/datadeck" -c "SELECT 1" >/dev/null 2>&1; then
        echo -e "  数据库: ${GREEN}已连接${NC} (PostgreSQL @ localhost:5432/datadeck)"
    else
        echo -e "  数据库: ${RED}未连接${NC}"
    fi

    echo ""
}

show_logs() {
    local target=${1:-backend}
    case $target in
        backend|be)
            echo "=== 后端日志 (最新 50 行) ==="
            tail -50 "$BACKEND_LOG" 2>/dev/null || echo "日志文件不存在"
            ;;
        frontend|fe)
            echo "=== 前端日志 (最新 50 行) ==="
            tail -50 "$FRONTEND_LOG" 2>/dev/null || echo "日志文件不存在"
            ;;
        all)
            show_logs backend
            echo ""
            show_logs frontend
            ;;
        *)
            echo "用法: $0 logs [backend|frontend|all]"
            ;;
    esac
}

# 主入口
case ${1:-help} in
    start)
        echo ""
        echo "=== datadeck 启动 ==="
        echo ""
        build_frontend
        start_backend
        start_frontend
        show_status
        echo "  默认账号: admin / admin123456"
        echo ""
        ;;
    stop)
        echo ""
        echo "=== datadeck 停止 ==="
        echo ""
        stop_backend
        stop_frontend
        log_info "已停止所有服务"
        ;;
    restart)
        echo ""
        echo "=== datadeck 重启 ==="
        echo ""
        # 先构建，构建失败时保留当前运行中的服务，避免切换到半成品。
        build_frontend
        stop_backend
        stop_frontend
        sleep 2
        start_backend
        start_frontend
        show_status
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs ${2:-backend}
        ;;
    help|*)
        echo ""
        echo "datadeck 管理脚本"
        echo ""
        echo "用法: $0 {start|stop|restart|status|logs}"
        echo ""
        echo "  start   - 启动前后端服务"
        echo "  stop    - 停止所有服务"
        echo "  restart - 重启所有服务"
        echo "  status  - 查看服务状态"
        echo "  logs    - 查看日志 (backend|frontend|all)"
        echo ""
        echo "访问地址:"
        echo "  前端: http://localhost:5173"
        echo "  后端: http://localhost:8000"
        echo "  文档: http://localhost:8000/docs"
        echo ""
        echo "默认账号: admin / admin123456"
        echo ""
        ;;
esac
