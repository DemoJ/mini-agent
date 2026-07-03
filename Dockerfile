# ====================================================================
# mini-agent Dockerfile (构建+运行时全镜像源版)
# - 构建时：APT、Node.js、gh、pip 全部走国内镜像
# - 运行时：apt、pip、npm 全局配置已设好，开箱即用
# ====================================================================
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# -------------------- 构建时 & 运行时 APT 源 --------------------
# 配置 /etc/apt/sources.list，apt-get update/install 自动走国内源
RUN echo "deb http://mirrors.aliyun.com/debian/ bookworm main contrib non-free" > /etc/apt/sources.list \
    && echo "deb http://mirrors.aliyun.com/debian/ bookworm-updates main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb http://mirrors.aliyun.com/debian-security bookworm-security main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb http://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb http://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-updates main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb http://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb http://mirrors.tencent.com/debian/ bookworm main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb http://mirrors.tencent.com/debian/ bookworm-updates main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb http://mirrors.tencent.com/debian-security bookworm-security main contrib non-free" >> /etc/apt/sources.list

# 安装基础工具（构建时使用已配置的 APT 源）
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

# -------------------- 构建时 Node.js（从国内镜像下载）-----------------
ENV NODE_VERSION=20.11.0
RUN curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/nodejs-release/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz -o node.tar.xz \
    || curl -fsSL https://mirrors.aliyun.com/nodejs-release/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz -o node.tar.xz \
    || curl -fsSL https://mirrors.tencent.com/nodejs-release/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz -o node.tar.xz \
    && tar -xJf node.tar.xz -C /usr/local --strip-components=1 \
    && rm node.tar.xz

# -------------------- 构建时 GitHub CLI（从国内镜像安装）------------
RUN curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/github-cli/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    || curl -fsSL https://mirrors.aliyun.com/github-cli/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    || curl -fsSL https://mirrors.tencent.com/github-cli/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://mirrors.tuna.tsinghua.edu.cn/github-cli stable main" > /etc/apt/sources.list.d/github-cli.list \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://mirrors.aliyun.com/github-cli stable main" >> /etc/apt/sources.list.d/github-cli.list \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://mirrors.tencent.com/github-cli stable main" >> /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

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