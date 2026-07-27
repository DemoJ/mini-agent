#!/usr/bin/env bash
# 卸载 mini-agent systemd 服务。
#
# 用法：
#   ./deploy/linux/uninstall-autostart.sh
#   ./deploy/linux/uninstall-autostart.sh --name mini-agent

set -euo pipefail

SERVICE_NAME="mini-agent"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) SERVICE_NAME="$2"; shift 2 ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

if [[ ! -w /etc/systemd/system ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
if [[ ! -f "$UNIT" ]]; then
  echo "未找到服务单元: $UNIT（可能已卸载）"
  exit 0
fi

$SUDO systemctl stop "$SERVICE_NAME" 2>/dev/null || true
$SUDO systemctl disable "$SERVICE_NAME" 2>/dev/null || true
$SUDO rm -f "$UNIT"
$SUDO systemctl daemon-reload
echo "已卸载服务: $SERVICE_NAME"
