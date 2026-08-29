#!/usr/bin/env bash
# KuroTutor 服务器自更新：拉代码 → Docker 内重建 wheel → 重建镜像 → 滚动重启 → 健康检查。
# 用法：bash scripts/upgrade.sh [--check]
set -euo pipefail
cd "$(dirname "$0")/.."

# wheel 的构建镜像：取 Dockerfile 的 BASE_IMAGE 默认值（国内 DaoCloud 源），可被环境变量覆盖
BASE_IMAGE="${BASE_IMAGE:-$(grep -m1 'ARG BASE_IMAGE' Dockerfile | awk -F'"' '{print $2}')}"
BASE_IMAGE="${BASE_IMAGE:-docker.m.daocloud.io/library/python:3.11-slim}"

echo "── 1/5 拉取最新代码"
git fetch origin
BEHIND=$(git rev-list --count HEAD..origin/main || echo 0)
if [ "$BEHIND" = "0" ]; then
  echo "已是最新版本（$(git log --oneline -1)）。"
  [ "${1:-}" = "--check" ] && exit 0
  echo "如需强制重建，运行：bash scripts/upgrade.sh --force"
  [ "${1:-}" != "--force" ] && exit 0
fi
echo "远端有 $BEHIND 个新提交，开始更新……"
git pull --ff-only origin main

echo "── 2/5 Docker 内重建 wheel（宿主机无需 Python 环境）"
docker run --rm -v "$PWD":/io -w /io "$BASE_IMAGE" \
  sh -c "pip install -q --upgrade pip && pip wheel --no-deps -w dist . -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"

echo "── 3/5 重建镜像"
docker compose build kuro

echo "── 4/5 滚动重启"
docker compose up -d kuro

echo "── 5/5 健康检查"
sleep 20
if docker compose ps kuro | grep -q "running"; then
  echo "✅ 更新完成，服务运行中。最近日志："
  docker compose logs --tail 5 kuro
else
  echo "❌ 容器未正常运行，请检查：docker compose logs kuro"
  exit 1
fi
