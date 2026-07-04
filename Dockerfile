# ====================================================================
# mini-agent Dockerfile (构建+运行时全镜像源版)
# ====================================================================
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# -------------------- 构建时探测可用 APT 镜像 --------------------
# 关键修复点：
# 1) 动态读取 /etc/os-release 里的真实代号（trixie/bookworm/...），不再写死
# 2) 同时处理新版 deb822 格式(/etc/apt/sources.list.d/debian.sources)和
#    旧版 sources.list —— 新版 Debian 基础镜像默认用前者，只改后者不生效
# 3) 用变量记录"是否真正写入成功"，而不是靠 test -s 误判
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
        # 新版镜像的源信息实际由这个 deb822 文件控制，必须一并覆盖/清空，
        # 否则它会和 sources.list 合并，继续把请求打到 deb.debian.org
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

# -------------------- 构建时 Node.js --------------------
ENV NODE_VERSION=20.11.0
RUN curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/nodejs-release/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz -o node.tar.xz \
    || curl -fsSL https://mirrors.aliyun.com/nodejs-release/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz -o node.tar.xz \
    || curl -fsSL https://mirrors.tencent.com/nodejs-release/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz -o node.tar.xz \
    && tar -xJf node.tar.xz -C /usr/local --strip-components=1 \
    && rm node.tar.xz

# -------------------- 构建时 GitHub CLI --------------------
RUN curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/github-cli/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    || curl -fsSL https://mirrors.aliyun.com/github-cli/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    || curl -fsSL https://mirrors.tencent.com/github-cli/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg

RUN set -eux; \
    ARCH=$(dpkg --print-architecture); \
    SELECTED=""; \
    for m in mirrors.tuna.tsinghua.edu.cn mirrors.aliyun.com mirrors.tencent.com; do \
        if curl -fsSL --connect-timeout 3 --max-time 5 "https://${m}/github-cli/" -o /dev/null; then \
            SELECTED="${m}"; \
            break; \
        fi; \
    done; \
    if [ -z "${SELECTED}" ]; then \
        echo "gh 镜像均不可用，回退官方源 cli.github.com"; \
        echo "deb [arch=${ARCH} signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list; \
    else \
        echo "==> 已选用 gh 镜像: ${SELECTED}"; \
        echo "deb [arch=${ARCH} signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://${SELECTED}/github-cli stable main" > /etc/apt/sources.list.d/github-cli.list; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# -------------------- 运行时 pip 全局配置 --------------------
RUN mkdir -p /etc/pip \
    && echo "[global]" > /etc/pip.conf \
    && echo "index-url = https://pypi.tuna.tsinghua.edu.cn/simple" >> /etc/pip.conf \
    && echo "extra-index-url = https://mirrors.aliyun.com/pypi/simple/ https://mirrors.tencent.com/pypi/simple/" >> /etc/pip.conf \
    && echo "trusted-host = pypi.tuna.tsinghua.edu.cn mirrors.aliyun.com mirrors.tencent.com" >> /etc/pip.conf

# -------------------- 运行时 npm 全局配置 --------------------
RUN npm config --global set registry https://registry.npmmirror.com \
    && npm config --global set disturl https://npmmirror.com/dist \
    && npm config --global set electron_mirror https://npmmirror.com/mirrors/electron/ \
    && npm config --global set python_mirror https://npmmirror.com/mirrors/python/

# -------------------- 构建时安装项目 Python 依赖 --------------------
RUN pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --extra-index-url https://mirrors.aliyun.com/pypi/simple/ \
        --trusted-host pypi.tuna.tsinghua.edu.cn \
        --trusted-host mirrors.aliyun.com \
        "openai>=1.0.0" \
        "pyyaml>=6.0" \
        "fastapi>=0.110.0" \
        "uvicorn[standard]>=0.27.0"

COPY main.py webui.py ./
COPY agent/  ./agent/
COPY web/    ./web/
COPY prompt/ ./prompt/
COPY config.example.yaml ./

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/ || exit 1

CMD ["python", "webui.py", "--host", "0.0.0.0", "--port", "8000"]