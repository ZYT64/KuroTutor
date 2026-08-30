# KuroTutor 多阶段构建 —— 低配服务器 / arm64（树莓派）可跑，无 GPU。
#
# 构建（含 RapidOCR 本地切题，镜像小、树莓派可跑）：
#   bash scripts/docker-build.sh   （先预构建 wheel 再 build，见脚本内说明）
# 海外构建可换回官方源：
#   docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple .

# ---------- 阶段 1：依赖安装 ----------
# 国内直连 Docker Hub 常失败，默认走 DaoCloud 镜像源；海外构建传
#   --build-arg BASE_IMAGE=python:3.11-slim
ARG BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim
FROM docker.m.daocloud.io/library/node:22-slim AS webui
WORKDIR /webui
COPY webui/package.json webui/package-lock.json* ./
RUN npm config set registry https://registry.npmmirror.com && npm install
COPY webui/ ./
RUN npm run build

FROM ${BASE_IMAGE} AS builder

# 国内默认 TUNA 镜像；botpy 不在 PyPI，走 ghfast.top 代理克隆（海外可改回 GitHub 原址）。
# ⚠️ 统一用 HTTP 并跳过 git 证书校验：宿主机代理/加速器会 TLS 中间人拦截，HTTPS 构建会报
#    certificate verify failed；HTTP + trusted-host 对此免疫。海外/无代理环境可换回 HTTPS。
ARG PIP_INDEX_URL=http://pypi.tuna.tsinghua.edu.cn/simple
ARG BOTPY_GIT_URL=https://ghfast.top/https://github.com/tencent-connect/botpy.git
# apt 源切 TUNA（HTTP）；兼容 bookworm/trixie 两种源文件布局
RUN sed -i 's@deb.debian.org@mirrors.tuna.tsinghua.edu.cn@g; s@https://@http://@g' /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null; \
    apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PIP_INDEX_URL="$PIP_INDEX_URL" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=5 \
    PIP_TRUSTED_HOST="pypi.tuna.tsinghua.edu.cn mirrors.tuna.tsinghua.edu.cn" \
    GIT_SSL_NO_VERIFY=true

# 安装预构建 wheel（先在宿主机执行：python -m pip wheel --no-deps -w dist .）
# ⚠️ 不在 BuildKit 内打包：其元数据阶段在本项目上会卡死（docker run 正常，BuildKit 异常，已实测）。
COPY dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -rf /tmp/*.whl
RUN pip install --no-cache-dir "git+$BOTPY_GIT_URL"
RUN pip install --no-cache-dir rapidocr_onnxruntime

# ---------- 阶段 2：运行时 ----------
FROM ${BASE_IMAGE} AS runtime

# onnxruntime 需要 libgomp1；libxcb1 是 pymupdf 的系统依赖；procps 提供 pgrep（健康检查用）；
# docker cli + compose 插件让 `kuro upgrade` 能在容器内重建整台部署。
# slim 镜像没有 curl/wget，用镜像自带的 Python 拉 GPG 密钥。
RUN sed -i 's@deb.debian.org@mirrors.tuna.tsinghua.edu.cn@g; s@https://@http://@g' /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null; \
    install -d -m 0755 /etc/apt/keyrings \
    && python -c "import urllib.request; urllib.request.urlretrieve('http://mirrors.aliyun.com/docker-ce/linux/debian/gpg', '/etc/apt/keyrings/docker.asc')" \
    && echo "deb [signed-by=/etc/apt/keyrings/docker.asc] http://mirrors.aliyun.com/docker-ce/linux/debian trixie stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libxcb1 libgl1 libglib2.0-0 procps git ca-certificates curl docker-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin kuro \
    && git config --system --add safe.directory /app/project

COPY --from=builder /opt/venv /opt/venv
COPY --from=webui /webui/dist /app/kurotutor/webui/dist
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app
RUN mkdir -p /app/data && chown -R kuro:kuro /app
USER kuro

# 配置经 compose 挂载：/app/kuro.json（容器内不落密钥）；数据卷 /app/data
VOLUME ["/app/data"]

# 健康检查：确认 kuro serve 进程存活（机器人是长连接进程，无 HTTP 端口可探测）
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f "kuro serve" > /dev/null || exit 1

CMD ["kuro", "serve", "--channel", "qq"]
