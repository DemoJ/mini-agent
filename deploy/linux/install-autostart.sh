#!/usr/bin/env bash
# 一键本机部署 mini-agent WebUI（venv + 依赖 + 配置 + systemd 开机自启）
#
# 用法：
#   ./deploy/linux/install-autostart.sh
#   ./deploy/linux/install-autostart.sh --host 0.0.0.0 --port 8080

set -euo pipefail

HOST="0.0.0.0"
PORT="8000"
SERVICE_NAME="mini-agent"
PIP_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --name) SERVICE_NAME="$2"; shift 2 ;;
    --pip-index) PIP_INDEX="$2"; shift 2 ;;
    -h|--help)
      echo "用法: $0 [--host 0.0.0.0] [--port 8000]"
      exit 0
      ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

step() { echo ""; echo "==> $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
echo "项目目录: $PROJECT_ROOT"

# ---------- 1. 找系统 Python ----------
step "检查 Python"
SYS_PYTHON=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      SYS_PYTHON="$(command -v "$cand")"
      break
    fi
  fi
done
if [[ -z "$SYS_PYTHON" ]]; then
  echo "错误: 未找到 Python ≥ 3.10，请先安装 python3" >&2
  exit 1
fi
echo "系统 Python: $SYS_PYTHON"

# ---------- 2. 创建 / 复用 .venv ----------
step "准备虚拟环境 .venv"
VENV_PY="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  "$SYS_PYTHON" -m venv "$PROJECT_ROOT/.venv"
  echo "已创建 .venv"
else
  echo ".venv 已存在，跳过创建"
fi

# ---------- 3. 安装依赖 ----------
step "安装项目依赖"
"$VENV_PY" -m pip install -U pip -i "$PIP_INDEX" || true
if ! "$VENV_PY" -m pip install -e "$PROJECT_ROOT" -i "$PIP_INDEX"; then
  echo "镜像安装失败，改用官方源重试..."
  "$VENV_PY" -m pip install -e "$PROJECT_ROOT"
fi
"$VENV_PY" -c "import fastapi, uvicorn; print('依赖 OK')"

# ---------- 4. 配置文件 ----------
step "检查 config.yaml"
if [[ ! -f "$PROJECT_ROOT/config.yaml" ]]; then
  if [[ ! -f "$PROJECT_ROOT/config.example.yaml" ]]; then
    echo "错误: 缺少 config.example.yaml" >&2
    exit 1
  fi
  cp "$PROJECT_ROOT/config.example.yaml" "$PROJECT_ROOT/config.yaml"
  echo "已生成 config.yaml（请稍后在 WebUI 设置页或编辑文件填入 api_key）"
else
  echo "config.yaml 已存在"
fi

# ---------- 5. 注册 systemd ----------
step "注册 systemd 服务 $SERVICE_NAME"
UNIT_SRC="$SCRIPT_DIR/mini-agent.service"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="${SUDO_USER:-$USER}"

if [[ ! -w /etc/systemd/system ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    echo "错误: 需要 root 权限写入 $UNIT_DST" >&2
    exit 1
  fi
else
  SUDO=""
fi

TMP_UNIT="$(mktemp)"
sed \
  -e "s|__USER__|${RUN_USER}|g" \
  -e "s|__PROJECT_ROOT__|${PROJECT_ROOT}|g" \
  -e "s|__PYTHON__|${VENV_PY}|g" \
  -e "s|--host 0.0.0.0 --port 8000|--host ${HOST} --port ${PORT}|g" \
  "$UNIT_SRC" > "$TMP_UNIT"

$SUDO cp "$TMP_UNIT" "$UNIT_DST"
rm -f "$TMP_UNIT"
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now "$SERVICE_NAME"

echo ""
echo "=== 部署完成 ==="
echo "服务名:   $SERVICE_NAME"
echo "Python:   $VENV_PY"
echo "监听:     ${HOST}:${PORT}（局域网可访问）"
echo "本机访问: http://127.0.0.1:${PORT}"
echo "运行用户: $RUN_USER"
echo ""
echo "下一步: 浏览器打开本机或局域网 IP 对应端口，在「设置」填入 API Key。"
echo "状态:   systemctl status $SERVICE_NAME"
echo "日志:   journalctl -u $SERVICE_NAME -f"
echo "卸载:   $SCRIPT_DIR/uninstall-autostart.sh"
echo ""
