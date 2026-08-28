# KuroTutor 🌙

> **QQ 私聊里的 24 小时 AI 私人老师** —— 全科（小学到大学）、从零自研、开源 MIT。
> 像真人私教一样答疑、拍照解题、批改作业、记错题、定时上 1v1 小课。

---

## 30 秒快速开始

```bash
# ① 安装（Python 3.11+；建议用国内镜像）
python -m venv .venv
.venv\Scripts\pip install -e .          # Windows git-bash；Linux/mac 用 .venv/bin/pip

# ② 生成配置（跟着引导填；先选 echo 离线模型即可跑通链路）
kuro init

# ③ 健康检查
kuro doctor

# ④ 本地联调 —— 在终端里扮演学生，跟老师对话（Ctrl+C 退出）
kuro serve --channel console
```

**从零到跟老师说话，就这 4 步。** QQ 接入见下文「接入 QQ 私聊」。

---

## 核心概念

### 它是什么 / 不是什么

| 做 | 不做 |
|---|---|
| QQ 私聊里的 AI 私人老师 | 独立课程体系替代学校 |
| 全科辅助学习 | 只做单科 |
| Agent‑first 对话式交互 | 菜单式功能列表 |
| 命令行管理（无 WebUI） | WebUI 管理界面 |

### 架构红线

1. **Agent‑first**：一切功能都是 Agent 的工具，产品里没有菜单。
2. **沙箱（自动单模式）**：文件操作限定工作区、禁止改动系统设置；命令白名单。
3. **渠道独立**：QQ 私聊长连接；控制台渠道用于本地联调。
4. **单一配置**：`kuro.json`，明文、密钥 BYOK（自带模型 key），严禁推公开仓库。
5. **长内容双模式**：回复 >2000 字 → 写讲义 or 分条发送。
6. **教学法**：引导式讲解（先思路后答案），按学段适配语气与深度。

### 数据模型（SQLite 可切 PG）

- **学生**：画像（学段、地区、备注）+ 渠道标识。
- **知识点**：学科 → 章节 → 名称，带掌握度(0–1)与置信度。
- **错题本**：题目、知识点归因、错误类型、复习状态，闭环到掌握。
- **方法卡片**：题型 → 方法 → 步骤 → 易错点，随解题沉淀，越用越强。
- **笔记本 / 课程 / 定时任务 / 会话**：长期记忆与课堂编排的基础。

### 目录速览

```
kurotutor/
├── config/    配置 Schema + 加载器（环境变量 > 配置文件 > 默认值）
├── storage/   SQLModel 模型 + 引擎工厂
├── agent/     主循环、工具注册表、沙箱、优先级队列、消息入口
├── services/  可插拔 LLM Provider（OpenAI 兼容 + 离线 echo）
├── tools/     领域工具（错题本 / 笔记本 / 知识库方法卡片 / 时间）
├── adapters/  渠道抽象（QQ OneBot + 控制台）+ 统一消息 + 路由
└── cli/       命令行管理（配置 / 学生 / 知识库 / 服务 / 健康检查）
```

---

## 可执行示例（递进）

### 示例 1 · 配置与健康检查
```bash
kuro init                     # 交互式初始化
kuro config show              # 查看配置（密钥自动打码）
kuro config set models.llm.provider echo   # 切到离线模型联调
kuro doctor                   # 逐项诊断配置/数据库/模型/渠道
```

### 示例 2 · 在终端里跟老师对话
```bash
kuro serve --channel console
# 输入：老师，帮我讲讲勾股定理
# 输入：老师这道题我不会 [发来题目图片]   ← 自动调用视觉模型读题 → 引导式讲解
```
在 `kuro.json` 的 `models.llm` / `models.vision` 填入真实模型（DeepSeek / 通义等 OpenAI 兼容端点）与密钥，即得真实讲解。
`echo` 为离线联调（无密钥先跑通链路），`deepseek-v4-flash-vision-exp` 这类视觉模型由 `models.vision` 独立配置、可插拔。

### 示例 3 · 查看 Agent 掌握了哪些工具
```bash
kuro agent tools     # 列出 7 个已注册工具：错题本/笔记本/知识库/时间
```

### 示例 4 · 学生学情（画像 / 错题 / 笔记）
```bash
kuro student list    # 互动后自动建档
kuro student show 1  # 画像、错题数、知识点掌握度
kuro student remove 1 --yes   # 合规删除学生全部数据
```

### 示例 5 · 知识库沉淀与查询
```bash
kuro kb status       # 方法卡片 / 笔记本统计
```
Agent 每次解题后会自动 `kb_deposit` 沉淀方法卡片，`kb_search` 在讲解/出题时引用。

---

## 接入 QQ 私聊

QQ 渠道使用**官方 botpy SDK**（QQ 开放平台机器人，C2C 私聊长连接），已真机验证：私聊收发、Markdown 消息、原生「对方正在输入」、跨段记忆。

1. 在 [QQ 开放平台](https://q.qq.com) 创建机器人，拿到 AppID / AppSecret。
2. 在 `kuro.json` 的 `channel` 块配置：
   ```json
   "channel": {
     "app_id": "你的AppID",
     "secret": "你的AppSecret"
   }
   ```
3. 安装 botpy（不在 PyPI，从 GitHub 安装）后启动服务：
   ```bash
   pip install git+https://github.com/tencent-connect/botpy.git
   kuro serve --channel qq
   ```

> 说明：markdown 发送用 `msg_type=2`，失败自动回退纯文本；原生「正在输入」状态用 `msg_type=6`。学生称呼一律用昵称（无昵称叫「同学」），不暴露 openid。

---

## Docker 一键部署（低配服务器 / 树莓派可跑）

镜像为多阶段构建、多架构（amd64/arm64），无 GPU、默认含 RapidOCR 本地切题（免费无限、纯 CPU）。

```bash
# ① 准备配置（含密钥，严禁提交到公开仓库）
cp kuro.example.json kuro.json   # 填好 channel 与 models 密钥

# ② 构建并启动（一条龙：预构建 wheel + docker build；后台常驻、重启自恢复）
bash scripts/docker-build.sh
docker compose up -d

# ③ 看日志 / 状态
docker compose logs -f kuro
docker compose ps

# ④ 管理命令全部走 CLI（无 WebUI）
docker compose run --rm cli kuro student list
docker compose run --rm cli kuro doctor
docker compose run --rm cli kuro export wrongbook <学生>
```

- `kuro.json` 以**只读**方式挂载进容器（服务）/ 读写（CLI，支持 `kuro config set`）；`data/` 目录持久化数据库、工作区、知识库与导出文件。
- 国内拉不动 Docker Hub 基础镜像时，Dockerfile 已默认走 DaoCloud 镜像源；海外构建传 `--build-arg BASE_IMAGE=python:3.11-slim`。

---

## 里程碑

- **M1 ✅（已完成）**：Agent 主链路、配置、数据层、CLI 全套、控制台联调；拍照解题 + 画像 + 错题/沉淀闭环、作业批改、讲义、自动切题（RapidOCR 题号锚定，真实卷验证）、复习引擎 + 到期推送、调度器、对话编排（无感分段/分层压缩/跨段背景）、知识库双库（方法卡 + 语料）、QQ botpy 真机联调；Docker Compose 一键部署。语音（ASR/TTS）按需求取消。
- **M2（规划）**：定时课堂完整、个性化出题、学习周报/校本同步、自进化。
- **M3（规划）**：入学诊断、目标管理、公式图表渲染、代码沙箱、评测集、开源发布。

详见 `产品规格书.md` 与 `docs/`（目录规划）。开发按 `CLAUDE.md` 六阶段推进，进度见 `SUGGESTIONS.md`。

---

## 开源与许可

MIT License。开发全程私有，**全部做完再公开**（代码/文档整理干净后一次性发布）。学生数据本地存储、最小收集，提供导出/删除（合规）。
