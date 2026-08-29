<div align="center">

# KuroTutor 🌙

**QQ 私聊里的 24 小时 AI 私人老师**

_全科（小学到大学）· 引导式教学 · 拍照解题 · 错题闭环 · 定时 1v1 小课_

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-149%20passed-brightgreen?style=flat-square&logo=pytest&logoColor=white)](#开发与测试)
[![Agent Tools](https://img.shields.io/badge/Agent_Tools-49-blueviolet?style=flat-square)](#核心能力)
[![SQLite](https://img.shields.io/badge/Storage-SQLite%20%7C%20PG-003B57?style=flat-square&logo=sqlite&logoColor=white)](#架构一览)
[![Docker](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-2496ED?style=flat-square&logo=docker&logoColor=white)](#docker-一键部署)
[![QQ 机器人](https://img.shields.io/badge/QQ-官方%20botpy%20SDK-12B7F5?style=flat-square&logo=tencentqq&logoColor=white)](#接入-qq-私聊)
[![No WebUI](https://img.shields.io/badge/管理-纯%20CLI-orange?style=flat-square&logo=gnu-bash&logoColor=white)](#cli-管理)

</div>

---

像真人私教一样答疑、拍照解题、批改作业、记错题、定时上小课。一切功能都是 Agent 的工具——学生只管像和老师聊天一样说话，没有菜单、没有指令表。

## ✨ 核心能力

| 能力 | 说明 |
|---|---|
| 📷 **拍照解题** | 发题图 → 视觉读题 → 引导式讲解（先思路后答案，按学段适配语气） |
| 📝 **作业批改** | 整页作业逐题判分 + 错因归类，错题自动进错题本并排复习 |
| 🗂 **题集（错题 + 好题）** | 录入策略自主判断；按知识点筛选组卷导出 PDF/Word；模糊去重 |
| ✂️ **自动切题** | RapidOCR 题号锚定（本地免费无限），支持跨页自动缝合、透视矫正 |
| 🎯 **个性化出题** | 真题四级链：web 搜索 → jszkk 免费题库 → 火花 K12 → 智能生成兜底 |
| 📅 **定时课堂** | 排课 / 系列课 / 自动备课 / 开课提醒 / 课后闭环，应急改期取消 |
| 📊 **学习周报** | 每周统计 + LLM 润色，Word 导出，周日自动推送 |
| 🏫 **校本同步** | 教材版本 / 教学进度 / 考试日期登记，出题备课贴合校情 |
| 🩺 **入学诊断** | 分级摸底（真题优先）→ 判分 → 画像基线 → 学情报告 |
| 🏆 **目标与打卡** | 学习目标管理、每日打卡激励（连续天数 / 里程碑） |
| 🧮 **代码沙箱** | AST 白名单 + 隔离子进程，验算计算题不跑偏 |
| 🧠 **自进化记忆** | 对话事实自动提取 → 长期记忆 → 讲解个性化；学生画像持续更新 |

## 🚀 30 秒快速开始

```bash
# ① 安装（Python 3.11+）
python -m venv .venv
.venv/Scripts/pip install -e .          # Windows；Linux/mac 用 .venv/bin/pip

# ② 生成配置（交互式引导；无密钥可选 echo 离线模型先跑通链路）
kuro init

# ③ 健康检查
kuro doctor

# ④ 终端里当学生，和老师对话（Ctrl+C 退出）
kuro serve --channel console
```

## 💬 用起来是什么样

```text
学生：老师这道题不会 [发题目图片]
老师：正在看题…📖 看清啦，解方程 x²−5x+6=0。先不急着看答案——
      有没有两个数乘起来是 6、加起来是 5？
学生：是 2 和 3！所以 (x−2)(x−3)=0，x=2 或 3
老师：漂亮✅ 方法已沉淀到你的方法库，这道题不用进错题本～

学生：给我出两道二次函数的真题
老师：[真题 · 来自网上] 第 1 题…（配图带链接）

学生：约一节数学课，讲函数，明晚八点
老师：约好啦✅ 开课前 1 小时我自动备课，到点叫你上课。
```

## 🏗 架构一览

**Agent-first**：一切功能 = Agent 工具（49 个），管理操作走 CLI，无 WebUI。

```text
kurotutor/
├── agent/      主循环 · 工具注册表 · 沙箱 · 优先级队列 · 消息入口
├── tools/      领域工具（解题/批改/切题/出题/课堂/诊断/文档/沙箱…）
├── services/   可插拔 Provider（LLM / 视觉 / OCR / 嵌入）+ 领域服务
├── adapters/   渠道抽象（QQ 官方 botpy + 控制台联调）
├── kb/         知识库（方法卡片 + 语料，向量检索可选）
├── skills/     技能系统（渐进式披露）
├── storage/    SQLModel 数据层（SQLite，可切 PG）
├── cli/        kuro 命令树
└── config/     配置 Schema + 加载器（环境变量 > 配置文件 > 默认值）
```

设计要点：

- **模型全可插拔**：文本 / 视觉 / 嵌入 / OCR / 搜索 / 题库均为独立配置块，BYOK（自带密钥）；未配置视觉模型时自动回落多模态主模型，零配置可用
- **沙箱（自动单模式）**：文件操作限定工作区（防穿越 + 软链逃逸）、系统设置禁改、命令白名单、API 端点白名单
- **稳定性优先**：工具异常包装回填不崩溃、可重试、结构化日志、长内容 >2000 字自动分流（讲义/分条）

## ⚙️ 配置

单一配置文件 `kuro.json`（明文、私有仓库，**严禁推到公开仓库**）：

| 配置块 | 说明 | 必填 |
|---|---|---|
| `channel` | QQ 开放平台 AppID / AppSecret | 用 QQ 时必填 |
| `models.llm` | 文本模型（任意 OpenAI 兼容端点） | ✅ |
| `models.vision` | 视觉模型（不配则回落主模型） | 可选 |
| `models.search` | web_search 供应商（默认 bing 免密钥；支持 tavily） | 可选 |
| `models.qbank` | 火花 K12 题库 key（真题链第三级，按次付费） | 可选 |
| `models.layout` | 切题引擎（默认 RapidOCR 本地免费） | 可选 |

完整字段见 [`kuro.example.json`](kuro.example.json)。

## 📦 Docker 一键部署

低配服务器 / 树莓派（arm64）可跑，无 GPU，默认内置 RapidOCR 本地切题。

```bash
cp kuro.example.json kuro.json        # 填好密钥
bash scripts/docker-build.sh          # 预构建 wheel + 构建镜像
docker compose up -d                  # 后台常驻，重启自恢复

docker compose logs -f kuro           # 看日志
docker compose run --rm cli kuro doctor   # 管理命令全走 CLI
```

国内拉不动基础镜像时 Dockerfile 已默认走 DaoCloud 镜像源；海外构建传 `--build-arg BASE_IMAGE=python:3.11-slim`。

## 🔌 接入 QQ 私聊

1. 在 [QQ 开放平台](https://q.qq.com) 创建机器人，拿到 AppID / AppSecret 填入 `channel` 块
2. 安装官方 SDK（不在 PyPI）：`pip install git+https://github.com/tencent-connect/botpy.git`
3. `kuro serve --channel qq`

已真机验证：私聊收发、Markdown 消息、原生「正在输入」、跨段记忆、图片全链路。

## 🛠 CLI 管理

```bash
kuro config show                 # 查看配置（密钥自动打码）
kuro agent tools                 # 列出 49 个已注册工具
kuro student list / show / remove  # 学生学情与合规删除
kuro kb status / rebuild         # 知识库
kuro schedule list               # 定时任务
kuro export wrongbook <学生>     # 导出错题本 / 学习报告
kuro doctor                      # 健康检查
```

## 🧪 开发与测试

```bash
pip install -e ".[test,dev]"
pytest                           # 149 项单元/集成测试
ruff check kurotutor/ tests/     # lint
python scripts/evaluate.py       # 评测集：10 条核心旅程驱动真实 Agent 循环
```

评测用例在 [`evals/eval_cases.json`](evals/eval_cases.json)，欢迎补充。测试用离线 `echo` Provider 即可跑通全链路，真实模型调用见 `scripts/`。

## 🗺 路线图

- [x] **M1** 基石：Agent 主链路 / 拍照解题 / 错题闭环 / 切题 / 复习引擎 / QQ 真机
- [x] **M2** 闭环：个性化出题 / 定时课堂 / 学习周报 / 校本同步 / 自进化记忆
- [x] **M3** 产品化：入学诊断 / 目标打卡 / 代码沙箱 / 评测集 / Docker 发布
- [ ] 知识库语义检索（等嵌入模型配置，当前回退关键词）
- [ ] 模糊去重进阶 / 系列课编排精简（候选优化）

## 🤝 贡献

Issue / PR 欢迎：补评测用例、新题库适配、新渠道适配器（Agent-first 架构下渠道与 Provider 均可插拔扩展）。

## 📄 许可

[MIT](LICENSE)。学生数据本地存储、最小收集，提供导出 / 删除（合规）。
