"""
mini-agent WebUI 入口
=====================
启动 FastAPI 服务，提供对话页 + 设置页。

用法：
    python webui.py                 # 默认 127.0.0.1:8000
    python webui.py --port 8080
    python webui.py --host 0.0.0.0 --port 8000
"""

import argparse

from server.app import app


def main() -> None:
    parser = argparse.ArgumentParser(description="mini-agent WebUI")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
