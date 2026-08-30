#!/usr/bin/env bash
# KuroTutor 一键部署：clone 后在本目录执行 bash scripts/setup.sh 即可。
# 流程：环境检查 → Docker 内构建 wheel → 交互式初始化配置 → 构建镜像并启动 → 健康检查。
set -euo pipefail
cd "$(dirname "$0")/.."

echo "── 1/5 环境检查"
if ! command -v docker >/dev/null 2>&1; then
  echo "❌ 未检测到 Docker。请先安装：https://docs.docker.com/engine/install/"
  exit 1
fi
docker info >/dev/null 2>&1 || { echo "❌ Docker 未运行，请先启动 Docker Desktop / daemon。"; exit 1; }
echo "Docker 就绪。"

BASE_IMAGE="${BASE_IMAGE:-$(grep -m1 'ARG BASE_IMAGE' Dockerfile | awk -F'"' '{print $2}')}"
BASE_IMAGE="${BASE_IMAGE:-docker.m.daocloud.io/library/python:3.11-slim}"

echo "── 2/5 构建 wheel（Docker 内，宿主机无需 Python）"
mkdir -p dist && rm -f dist/*.whl
docker run --rm -v "$PWD":/io -w /io "$BASE_IMAGE" \
  sh -c "pip install -q --upgrade pip && pip wheel --no-deps -w dist . -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"

echo "── 3/5 初始化配置（交互式引导，填 QQ 凭据与模型 key）"
if [ ! -f kuro.json ]; then
  docker compose run --rm cli kuro init
else
  echo "已存在 kuro.json，跳过初始化（重新引导请先备份并删除它）。"
fi

echo "── 4/5 构建镜像并启动"
docker compose up -d --build kuro

echo "── 5/5 健康检查"
sleep 20
STATUS=$(docker inspect -f '{{.State.Status}}' kurotutor 2>/dev/null || echo unknown)
if [ "$STATUS" = "running" ]; then
  echo "✅ 部署完成！机器人已上线（restart: unless-stopped，重启自恢复）。"
  echo "   看日志：docker compose logs -f kuro"
  echo "   管理命令：docker compose run --rm cli kuro doctor"
else
  echo "❌ 容器未正常运行，请检查：docker compose logs kuro"
  exit 1
fi
