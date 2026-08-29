#!/usr/bin/env bash
# KuroTutor Docker 构建一条龙：预构建 wheel → docker build。
# 用法：bash scripts/docker-build.sh   （额外参数透传给 docker build）
set -euo pipefail
cd "$(dirname "$0")/.."

# ① 预构建 wheel（Dockerfile 直接安装它，不在 BuildKit 内打包）
rm -f dist/*.whl  # 清掉旧 wheel，避免新旧包并存
PY=".venv/Scripts/python.exe"        # Windows git-bash
[ -x "$PY" ] || PY=".venv/bin/python" # Linux/mac
"$PY" -m pip wheel --no-deps -w dist .

# ② docker build（空代理参数：绕开 Docker Desktop 继承的系统代理）
docker build \
  --build-arg HTTP_PROXY="" --build-arg HTTPS_PROXY="" \
  --build-arg http_proxy="" --build-arg https_proxy="" \
  -t kurotutor:latest . "$@"
echo "构建完成：kurotutor:latest"
