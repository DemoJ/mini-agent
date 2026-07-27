"""WebUI 常量。"""

from pathlib import Path

CONFIG_PATH = "config.yaml"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 文件上传大小上限（50MB）
MAX_UPLOAD_SIZE = 50 * 1024 * 1024
