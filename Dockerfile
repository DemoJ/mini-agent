# ====================================================================
# mini-agent Dockerfile (构建+运行时全镜像源版)
# - 目标：预装 node.js、curl、pipx、git，构建时全部走国内镜像加速
# ====================================================================
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PATH="/root/.local/bin:${PATH}"

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

# -------------------- 安装基础工具：curl、git、pipx 等 --------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        curl \
        ca-certificates \
        tzdata \
        git \
        xz-utils \
        pipx \
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

# -------------------- 运行时 pip 全局配置 --------------------
# 这里配置的是系统级 /etc/pip.conf，无论是直接 pip install 还是通过 pipx
# 在隔离的 venv 里安装工具，pip 都会读取这个全局配置文件，自动走国内源。
RUN mkdir -p /etc/pip \
    && echo "[global]" > /etc/pip.conf \
    && echo "index-url = https://pypi.tuna.tsinghua.edu.cn/simple" >> /etc/pip.conf \
    && echo "extra-index-url = https://mirrors.aliyun.com/pypi/simple/ https://mirrors.tencent.com/pypi/simple/" >> /etc/pip.conf \
    && echo "trusted-host = pypi.tuna.tsinghua.edu.cn mirrors.aliyun.com mirrors.tencent.com" >> /etc/pip.conf

# -------------------- 拷贝项目源码（config.yaml 由 .dockerignore 排除）---------------
# 提前拷贝项目文件，以便 pip install . 可以读取 pyproject.toml 安装依赖
COPY pyproject.toml ./
COPY main.py webui.py ./
COPY agent/  ./agent/
COPY web/    ./web/
COPY prompt/ ./prompt/
COPY config.example.yaml ./

# -------------------- 构建时安装项目 Python 依赖 --------------------
# 直接使用 pip install . 读取 pyproject.toml，自动安装全部声明依赖
# 已通过 /etc/pip.conf 全局配置国内镜像源，无需重复指定 -i 参数
RUN pip install --no-cache-dir .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/ || exit 1

CMD ["python", "webui.py", "--host", "0.0.0.0", "--port", "8000"]