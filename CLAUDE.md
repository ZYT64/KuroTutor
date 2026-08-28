# KuroTutor 项目 CLAUDE.md（开发者指令）

> 版本：v1.0
> 日期：2026-08-26
> 用途：开发指令。产品规格见 `docs/SPEC.md`。
> 本文档定义“怎么开发”——目录结构、技术栈、架构实现、代码规范、任务执行方式。


## 1. 项目概述

**KuroTutor**：QQ 私聊里的 24 小时 AI 私人老师。

- **技术栈**：Python 3.11+ / FastAPI（仅内部服务） / SQLModel(SQLite，可切 PG) / CLI 框架 / 调度框架 / QQ SDK
- **部署**：Docker Compose 一键部署，arm64 可跑，无 GPU
- **模型**：可插拔 Provider 架构——文本模型、视觉模型、嵌入模型、语音模型均可配置
- **配置**：单一配置文件（全部明文存储，私有 git 仓库，⚠️ 严禁推到公开仓库）
- **管理**：所有管理操作通过交互式 CLI 完成，**无 WebUI**

### 1.1 架构红线（必须遵守）

1. **Agent‑first**：所有功能 = Agent 工具，无一例外；管理操作走 CLI，不另设 WebUI
2. **Agent 沙箱（自动单模式）**：权限单一自动放行模式；两条硬约束——① 所有文件操作限定工作区（路径校验防穿越+软链逃逸）② 禁止修改系统设置（系统文件/配置/服务/包管理）；命令白名单 + 系统级命令黑名单；API 端点白名单；**稳定性优先**（错误不崩溃、可重试、有日志、行为可预测）
3. **渠道独立模块**：渠道适配器抽象接口，当前仅实现 QQ（私聊，长连接）
4. **Agent 架构参考**：工具驱动循环 + 技能定义渐进式披露 + 插件打包
5. **单一配置文件**：模型/密钥/权限/路径全部明文存储（私有仓库）；禁止推到公开仓库
6. **长内容双模式**：>2000 字 → ①写讲义 ②分条发送
7. **教学法**：引导式讲解（先思路后答案），按学段适配
8. **命令行管理**：所有运维操作走 CLI，不依赖 Web 服务


## 2. 项目目录结构

```
~/kurotutor/
├── AGENTS.md                    # 项目级 Agent 指令
├── kuro.json                    # 唯一配置文件（明文，私有仓库）
├── kuro.example.json            # 配置示例
├── pyproject.toml               # 依赖管理
├── compose.yaml                 # Docker Compose
├── Dockerfile                   # 多阶段构建
├── .gitignore
│
├── kurotutor/                   # 主包
│   ├── agent/                   # Agent 循环 + 编排
│   │   ├── core.py              # Agent 主循环
│   │   ├── entry.py             # 消息入口
│   │   ├── context.py           # 上下文
│   │   ├── queue.py             # 优先级队列 + 打断
│   │   ├── sandbox.py           # 沙箱
│   │   └── registry.py          # 工具注册表
│   │
│   ├── tools/                   # 领域工具
│   │   ├── solve_photo.py       # 拍照解题
│   │   ├── grade_homework.py    # 作业批改
│   │   ├── wrong_book.py        # 错题本
│   │   ├── review.py            # 复习引擎
│   │   ├── quiz.py              # 出题
│   │   ├── kb_search.py         # 知识库检索
│   │   ├── kb_deposit.py        # 方法沉淀
│   │   ├── lecture_gen.py       # 讲义生成
│   │   ├── schedule_class.py    # 排课
│   │   ├── notebook.py          # 笔记本
│   │   ├── asr.py / tts.py      # 语音
│   │   └── registry.py          # 工具注册
│   │
│   ├── services/                # 领域服务
│   │   ├── vision.py            # 视觉识别
│   │   ├── profile.py           # 画像
│   │   ├── grading.py           # 批改
│   │   ├── review.py            # 复习
│   │   ├── kb_service.py        # 知识库
│   │   ├── lecture.py           # 讲义渲染
│   │   ├── voice.py             # 语音
│   │   ├── notebook.py          # 笔记本
│   │   ├── image_split.py       # 自动切题
│   │   ├── layout.py            # 版面分析
│   │   ├── ocr.py               # 文字识别
│   │   └── scheduler.py         # 调度器
│   │
│   ├── storage/                 # 数据层
│   │   ├── models.py            # 数据模型
│   │   └── engine.py            # 引擎工厂
│   │
│   ├── kb/                      # 知识库底层
│   │   ├── embeddings.py        # 嵌入服务
│   │   ├── vector_store.py      # 向量库
│   │   └── reranker.py          # 重排
│   │
│   ├── adapters/                # 渠道适配器
│   │   ├── base.py              # 抽象接口
│   │   ├── router.py            # 消息路由
│   │   ├── message.py           # 统一消息格式
│   │   └── channel/qq/          # QQ 渠道
│   │
│   ├── skills/                  # 技能系统
│   │   ├── loader.py            # 技能加载器
│   │   ├── teaching-style/      # 教学法技能
│   │   ├── stage-adapt/         # 学段适配技能
│   │   ├── wrong-book-policy/   # 错题策略技能
│   │   ├── image-message-handling/  # 图片回应技能
│   │   └── auto/                # 自动生成的技能
│   │
│   ├── plugins/                 # 插件系统
│   │   └── tutor-core/          # 核心插件
│   │
│   ├── cli/                     # CLI 命令树
│   │   ├── main.py              # 入口
│   │   ├── init.py              # kuro init
│   │   ├── config.py            # kuro config
│   │   ├── agent.py             # kuro agent
│   │   ├── skill.py             # kuro skill
│   │   ├── plugin.py            # kuro plugin
│   │   ├── student.py           # kuro student
│   │   ├── kb.py                # kuro kb
│   │   ├── schedule.py          # kuro schedule
│   │   ├── export.py            # kuro export
│   │   └── doctor.py            # kuro doctor
│   │
│   └── config/                  # 配置系统
│       ├── schema.py            # 配置模型
│       └── loader.py            # 配置加载器
│
├── tests/                       # 测试
│   ├── conftest.py
│   └── test_*.py                # 各模块测试
│
├── data/                        # 运行时数据（gitignore）
│   ├── workspaces/
│   ├── kb/
│   └── kurotutor.db
│
├── docs/                        # 方案文档
│   ├── SPEC.md
│   ├── COMPLETE-PLAN.md
│   ├── COURSES-CRON.md
│   ├── SELF-EVOLUTION.md
│   ├── CONVERSATION.md
│   ├── IMAGE-SPLITTING.md
│   ├── NOTEBOOK.md
│   └── STUDENT-VIEW.md
│
├── scripts/                     # 工具脚本
│
└── .claude/                     # Claude Code 项目级配置
    └── skills/
```


## 3. 开发环境与配置

### 3.1 环境要求
- Python 3.11+
- pip 走国内镜像
- 注意：虚拟环境建磁盘，不要建 tmpfs 目录

### 3.2 配置文件结构
```json
{
  "name": "kurotutor",
  "version": "0.1.0",
  "workspace": "data/workspaces",
  "channel": {
    "app_id": "xxx",
    "secret": "xxx"
  },
  "models": {
    "llm": { "provider": "xxx", "model": "xxx", "api_key": "xxx" },
    "vision": { "provider": "xxx", "model": "xxx", "api_key": "xxx" },
    "transcriber": { "provider": "xxx", "model": "xxx" },
    "embedding": { "provider": "xxx", "model": "xxx", "api_key": "xxx" },
    "reranker": { "provider": "xxx", "model": "xxx" },
    "asr": { "provider": "xxx", "model": "xxx" },
    "tts": { "provider": "xxx", "model": "xxx", "fallback": "xxx" }
  },
  "permissions": {
    "shell": "deny",
    "file_access": "workspace_only",
    "model_endpoints": ["https://api.xxx"]
  },
  "kb": { "vector_store": "xxx", "path": "data/kb" },
  "paths": { "skills_dir": "skills", "plugins_dir": "plugins" },
  "log_level": "info"
}
```

### 3.3 配置驱动原则
- 所有模型 Provider 可插拔，通过配置文件切换，不硬编码
- 密钥存于配置文件，仅限私有仓库
- 配置加载器支持明文优先、环境变量兼容


## 4. 架构实现指南

### 4.1 Agent 循环

```
组装上下文（指令 + 工具定义 + 历史 + 当前消息）
  → LLM 调用
  → 解析工具调用
  → 沙箱校验
  → 执行工具 → 结果回填
  → 循环直到最终回复 → 分片返回
```

- **工具注册**：装饰器注册，统一注册表
- **沙箱**：自动单模式——默认放行，仅两道硬约束（工作区 + 禁系统设置）

### 4.2 技能系统

- **技能定义格式**：元数据 + 正文
- **渐进式披露**：启动只载元数据；命中才读全文；引用文件按需加载
- **技能工具**：把技能作为可调用工具，注入领域指令

### 4.3 对话编排

- **优先级队列**：P0 学生消息 → P1 后续 → P2 推送 → P3 后台
- **语境感知打断**：补充→并入；修正→重定向；换题→开新轮；打断缓存可恢复
- **无感分段**：相关性判断 + 时间维度 → 自动开新段
- **分层压缩**：若干轮原文 → 摘要 → 剪掉

### 4.4 记忆体系

| 层 | 说明 |
|---|---|
| L0 | 上下文窗口 |
| L1 | 会话记录+摘要 |
| L2 | 短期状态 |
| L3 | 学生画像 |
| L4 | 长期记忆 |
| L5 | 教训记忆 |
| L6 | 技能记忆 |
| L7 | 知识层 |
| 贯穿 | 语义检索索引 |

- **写入**：交互后异步提取 → 分层路由 → 去重合并
- **蒸馏**：定期 cron
- **遗忘**：过期归档、低价值清理、学生要求删除

### 4.5 定时任务与课程

- **调度器**：统一调度 + 持久化存储（重启恢复）
- **任务类型**：备课/提醒/开课/下课/作业提醒/复习推送/周报
- **课程**：单次课 + 系列课
- **课后闭环**：总结 → 作业 → 画像 → 错题 → 知识库 → 进度 → 系列课调整 → 下次备课

### 4.6 自动切题

- **方案**：版面分析 + 锚点检测 + 程序切割
- **小题**：默认整体保留

### 4.7 笔记本功能

- **解析**：文字识别 + 视觉理解 → 主题/学科/知识点/摘要
- **归类**：语义匹配三档
- **查询**：关键词检索

### 4.8 CLI 命令树（替代 WebUI）

```
kuro init                     # 交互式初始化配置
kuro config show              # 查看当前配置（敏感信息打码）
kuro config get <key>         # 读取单值
kuro config set <key> <value> # 修改配置（校验后写入）
kuro config validate          # 校验配置完整性

kuro serve                    # 启动服务

kuro student list             # 列出学生
kuro student show <id>        # 查看学生详情（画像/错题/进度）
kuro student remove <id>      # 删除学生数据（合规）

kuro agent tools              # 列出已注册工具
kuro agent skills             # 列出可用技能

kuro skill list
kuro skill add <path>         # 安装技能
kuro skill remove <name>

kuro plugin list
kuro plugin install <name|path>
kuro plugin remove <name>

kuro kb status                # 知识库统计
kuro kb rebuild               # 重建索引
kuro kb import <path>         # 导入语料

kuro schedule list            # 列出定时任务
kuro schedule show <id>       # 查看任务详情

kuro export wrongbook <user>  # 导出错题本
kuro export report <user>     # 导出学习报告

kuro doctor                   # 健康检查
kuro version
kuro help
```


## 5. 代码规范

### 5.1 通用规范
- 遵循 PEP8 + type hints
- 新增功能必须带测试
- 不把密钥写进代码
- commit message 带任务前缀
- 工作树保持干净

### 5.2 并行任务的文件所有权
- 任务包必须显式声明文件所有权
- 共享文件由单一任务独占
- git 提交只 add 自己文件

### 5.3 稳定性要求
- 错误不崩溃：捕获 → 回填错误消息 → 继续
- 重试策略：可重试 1-2 次
- 日志记录：关键操作写入日志
- 入口兜底：try/except 包裹

### 5.4 真实场景全功能测试（强制）

- ❌ **禁止只跑单元测试就宣告完成**。单元测试仅用于快速验证逻辑，不能作为功能可用的最终证明。
- ✅ **必须完成至少一次真实场景端到端测试**，模拟真实学生使用路径，并记录测试结果。
- ✅ 真实场景测试必须覆盖核心用户旅程。
- ✅ 真实场景测试应使用真实输入文件、真实 API 调用。
- ✅ 测试完成后必须输出真实场景测试报告。
- ✅ 只有真实场景测试全部通过，任务才能被验收。

**真实场景测试清单**：

| 功能 | 必测场景 | 验证方式 |
|---|---|---|
| 拍照解题 | 发测试题图，Agent 返回引导式讲解 + 方法卡片 | 查看返回内容，确认错题询问策略触发 |
| 作业批改 | 发作业图，返回逐题批改 + 错题归类 | 查看批改结果，确认错题自动进入错题本 |
| 复习引擎 | 触发复习推送，查看是否在安静时段内顺延 | 检查推送内容，确认间隔计算正确 |
| 知识库 | 调用检索，验证结果是否包含真实方法卡片 | 检查返回卡片内容与预期一致 |
| 讲义 | 生成文档文件，打开验证段落数/内容 | 输出文件大小 > 0，段落内容包含讲义标题 |
| 语音 | 合成音频播放确认内容正确；识别同一音频比对文本 | 音频可播放，识别文本与原文一致 |
| 切题 | 对测试图执行切题，检查输出图片数量及完整性 | 输出图片清晰可读，大题带小题不拆散 |
| 笔记本 | 发笔记图，确认智能归类结果正确 | 返回的归类结果与实际匹配 |
| CLI | 执行各命令，确认输出正确 | 命令返回预期结果 |


## 6. 任务执行与验收

### 6.1 任务包格式
每个任务包必须包含：
1. **任务范围**：明确做什么、不做什么
2. **文件所有权声明**：哪些文件可改、哪些不可改
3. **验收标准**：含真实场景测试要求
4. **约束**：禁止事项
5. **git 提交**：指定提交数量和信息格式

### 6.2 验收标准

1. **真实场景测试通过**：完成对应的真实用户旅程测试，测试报告记录完整
2. **代码可导入**：无导入错误
3. **无语法错误**：所有新增文件通过编译检查
4. **文件所有权合规**：只修改了应修改的文件
5. **工作树干净**：无非预期未提交文件
6. **CLI 命令可用**（如适用）
7. **服务启动正常**（如适用）

**注意：单元测试通过是辅助验证，不是唯一验收依据。**

### 6.3 真实场景测试报告模板

```
【任务编号】xxx
【测试场景】xxx
【测试时间】xxx
【测试输入】xxx
【预期输出】xxx
【实际输出】xxx
【判断】通过/不通过
【问题记录】xxx
```

每个任务必须至少有一份这样的报告。

### 6.4 学生视角验收

- 真实场景测试完成后，必须代入 4 个学段人物角色
- 回答 6 个通用问题
- 体验不适 → 打回修复


## 7. 约束与禁止事项

### 7.1 强制约束
- ❌ 禁止引入 TS/Node 后端依赖
- ❌ 禁止写死密钥（密钥存配置文件，仅限私有仓库；严禁推到公开仓库）
- ❌ 禁止绕过沙箱
- ❌ 禁止功能只做壳子不实现
- ❌ 禁止动 docs/ 方案文档
- ❌ 禁止在任务运行中改动配置/代理
- ❌ 禁止引入 WebUI 框架或前端依赖

### 7.2 推荐做法
- ✅ 任务运行期间用可靠方式判断进程存活
- ✅ 长任务用系统服务托管
- ✅ 虚拟环境建磁盘
- ✅ 配置变更在任务间隙做
- ✅ 管理功能全部走 CLI


## 8. 配置管理

| 配置项 | 说明 | 来源 |
|---|---|---|
| 渠道密钥 | QQ 机器人密钥 | 用户提供 |
| 模型密钥 | 各模型 API key | 用户提供 |

所有模型 key 通过配置文件管理，不硬编码。


## 9. 常用命令速查

| 命令 | 说明 |
|---|---|
| `kuro init` | 交互式初始化配置 |
| `kuro config validate` | 校验配置 |
| `kuro serve` | 启动服务 |
| `kuro agent tools` | 列出已注册工具 |
| `kuro agent skills` | 列出可用技能 |
| `kuro student list` | 列出学生 |
| `kuro kb status` | 知识库统计 |
| `kuro schedule list` | 列出定时任务 |
| `kuro doctor` | 健康检查 |
| `kuro export wrongbook <user>` | 导出错题本 |
| `kuro export report <user>` | 导出学习报告 |


## 10. 任务衔接

### 10.1 任务完成后
1. 提交代码
2. 输出真实场景测试报告
3. 通知验收
4. 等待验收通过后才能进入下一个任务

### 10.2 遇到阻塞
- 设计冲突：先问产品负责人
- 依赖缺失：用国内镜像安装
- 模型 API 问题：检查配置，确认 key 有效
- 环境异常：先跑健康检查