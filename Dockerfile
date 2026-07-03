# ====================================================================
# mini-agent Dockerfile
# 提供基于 FastAPI + Uvicorn 的 WebUI 运行环境
# 构建镜像不会包含 config.yaml（含 API Key），运行时通过 volume 注入
# ====================================================================
FROM python:3.11-slim

WORKDIR /app

# 环境变量：日志即时输出、不写 pyc、设置时区
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# 安装基础系统依赖：
#   - bash          : agent_loop.py 的 bash 工具需要
#   - curl          : 健康检查 / 调试
#   - ca-certificates: HTTPS 证书
#   - tzdata        : 时区
# 使用国内 Debian 镜像源（腾讯云）加速 apt 安装
RUN sed -i 's@deb.debian.org@mirrors.tencent.com@g; s@security.debian.org@mirrors.tencent.com@g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's@deb.debian.org@mirrors.tencent.com@g; s@security.debian.org@mirrors.tencent.com@g' /etc/apt/sources.list 2>/dev/null \
    || true \
    && apt-get update && apt-get install -y --no-install-recommends \
        bash \
        curl \
        ca-certificates \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 先安装 Python 依赖（与 pyproject.toml 中 dependencies 保持一致）
# 单独一层，源码变更不会触发依赖重装，充分利用 Docker 缓存
# 使用清华 PyPI 镜像源加速
RUN pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        "openai>=1.0.0" \
        "pyyaml>=6.0" \
        "fastapi>=0.110.0" \
        "uvicorn[standard]>=0.27.0"

# 拷贝项目源码
# 注意：config.yaml 由 .dockerignore 排除，不会进入镜像
COPY main.py agent_loop.py config_loader.py webui.py ./
COPY web/    ./web/
COPY prompt/ ./prompt/
COPY config.example.yaml ./

# WebUI 端口
EXPOSE 8000

# 健康检查：每 30s 探测首页
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/ || exit 1

# 启动 WebUI，绑定 0.0.0.0 以便容器外部访问
CMD ["python", "webui.py", "--host", "0.0.0.0", "--port", "8000"]
