# ====================================================================
# mini-agent Dockerfile (构建+运行时全镜像源版)
# - 构建时：APT、Node.js、pip 全部走国内镜像（探测后择一或顺序 fallback）
# - 运行时：apt、pip、npm 全局配置已设好，开箱即用
# ====================================================================
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# -------------------- 构建时探测可用 APT 镜像 --------------------
# 动态读取真实系统代号（trixie/bookworm/...），并处理新版 Debian 基础镜像
# 默认使用的 deb822 格式源文件（/etc/apt/sources.list.d/debian.sources），
# 否则只改 sources.list 不会生效。
RUN set -eux; \
    CODENAME=$(. /etc/os-release && echo "${VERSION_CODENAME}"); \
    echo "==> 检测到系统代号: ${CODENAME}"; \
    SELECTED=""; \
    for m in mirrors.tuna.tsinghua.edu.cn mirrors.aliyun.com mirrors.tencent.com; do \
        if python3 -c "import urllib.request; urllib.request.urlopen('https://${m}/debian/dists/${CODENAME}/Release', timeout=5)" 2>/dev/null; then \
            SELECTED="${m}"; \
            break; \
        fi; \
    done; \
    if [ -z "${SELECTED}" ]; then \
        echo "所有 APT 镜像均不可用，回退使用官方源（deb.debian.org）"; \
    else \
        echo "==> 已选用 APT 镜像: ${SELECTED}"; \
        { \
            echo "deb https://${SELECTED}/debian/ ${CODENAME} main contrib non-free"; \
            echo "deb https://${SELECTED}/debian/ ${CODENAME}-updates main contrib non-free"; \
            echo "deb https://${SELECTED}/debian-security ${CODENAME}-security main contrib non-free"; \
        } > /etc/apt/sources.list; \
        if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
            rm -f /etc/apt/sources.list.d/debian.sources; \
        fi; \
    fi; \
    echo "==> 最终生效的 APT 源:"; \
    cat /etc/apt/sources.list 2>/dev/null || true; \
    cat /etc/apt/sources.list.d/*.sources 2>/dev/null || true

# 安装基础工具
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        curl \
        ca-certificates \
        tzdata \
        git \
        gnupg \
        xz-utils \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# -------------------- 构建时 Node.js（从国内镜像下载，顺序 fallback）-----------------
ENV NODE_VERSION=20.11.0
RUN curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/nodejs-release/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz -o node.tar.xz \
    || curl -fsSL https://mirrors.aliyun.com/nodejs-release/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz -o node.tar.xz \
    || curl -fsSL https://mirrors.tencent.com/nodejs-release/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz -o node.tar.xz \
    && tar -xJf node.tar.xz -C /usr/local --strip-components=1 \
    && rm node.tar.xz

# -------------------- 运行时 pip 全局配置（容器内任何 pip install 都走国内源）----
RUN mkdir -p /etc/pip \
    && echo "[global]" > /etc/pip.conf \
    && echo "index-url = https://pypi.tuna.tsinghua.edu.cn/simple" >> /etc/pip.conf \
    && echo "extra-index-url = https://mirrors.aliyun.com/pypi/simple/ https://mirrors.tencent.com/pypi/simple/" >> /etc/pip.conf \
    && echo "trusted-host = pypi.tuna.tsinghua.edu.cn mirrors.aliyun.com mirrors.tencent.com" >> /etc/pip.conf

# -------------------- 运行时 npm 全局配置（容器内任何 npm install 都走淘宝镜像）--
RUN npm config --global set registry https://registry.npmmirror.com \
    && npm config --global set disturl https://npmmirror.com/dist \
    && npm config --global set electron_mirror https://npmmirror.com/mirrors/electron/ \
    && npm config --global set python_mirror https://npmmirror.com/mirrors/python/

# -------------------- 构建时安装项目 Python 依赖（显式指定国内源）---------------
RUN pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --extra-index-url https://mirrors.aliyun.com/pypi/simple/ \
        --trusted-host pypi.tuna.tsinghua.edu.cn \
        --trusted-host mirrors.aliyun.com \
        "openai>=1.0.0" \
        "pyyaml>=6.0" \
        "fastapi>=0.110.0" \
        "uvicorn[standard]>=0.27.0"

# 拷贝项目源码（config.yaml 由 .dockerignore 排除）
COPY main.py webui.py ./
COPY agent/  ./agent/
COPY web/    ./web/
COPY prompt/ ./prompt/
COPY config.example.yaml ./

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/ || exit 1

CMD ["python", "webui.py", "--host", "0.0.0.0", "--port", "8000"]