# SUGGESTIONS.md — 开发待办与进度

> 项目宪法（CLAUDE.md）要求：开发全程同步维护本文件，记录待办、调整、新增需求与完成状态。
> 本文档是「没做完的」与「刚做完的」的权威清单。

---

## 一、当前状态

**阶段**：M1 基石（Agent 主链路 + 配置 + 数据层 + 核心工具 + CLI + 渠道层）
**可运行**：✅ 代码可安装、测试全绿（32 通过 + 1 跳过）、CLI 可执行、控制台渠道可端到端对话
**尚未接入**：❌ 真实 QQ 后端（需 napcat 等 OneBot 后端）、❌ 真实 LLM（需密钥）、❌ 视觉/语音/讲义/切题等模型驱动功能

---

## 二、已完成（本次落地）

> 本次先后完成：**视觉链路（拍照解题）+ 学生画像 + 确定性错题/沉淀闭环 + 笔记本发图归类入库**，并全部真实验证。

### 底层基座
- [x] `pyproject.toml`：可安装、`kuro` 入口、Python 3.11+/3.12 实测
- [x] 配置系统：Schema 校验、加载器、环境变量覆盖、密钥脱敏、键值点分读写
- [x] 数据层：SQLModel 模型（学生/知识点/错题/方法卡片/笔记本/课程/定时任务/会话）、引擎工厂、事务会话
- [x] 统一错误体系 + 结构化日志（代替裸 print）

### Agent 核心
- [x] 工具注册表（装饰器 + JSON Schema + 依赖注入 ToolContext）
- [x] 沙箱：工作区路径校验（防穿越 + 软链逃逸）、命令白名单/黑名单、端点白名单
- [x] Agent 主循环：错误不崩溃、可重试、有日志、迭代上限
- [x] 优先级队列（P0–P3）+ 语境感知打断（暂停恢复）
- [x] 消息入口：**四段式**（短事务定位会话 → 确认处理：学生确认记错题即确定性落库并清 PendingRecord → 跑 Agent → 事务落消息），避免 SQLite 写锁冲突；跨轮待确认错题由 `PendingRecord` 持久化

### 学生画像（本次新增）
- [x] `services/profile.py`：掌握度 EMA 更新、置信度、最近练习时间、错误类型分布
- [x] **错题询问策略**（确定性）：简单题已熟练→默认不存；新题/中难题做错→主动问；薄弱点/连续错→直接记

### 视觉链路（本次新增）
- [x] `services/vision.py`：可插拔 VisionProvider（OpenAI 兼容多模态；base64/URL；失败重试；`detail` 按需下传）
- [x] `tools/solve_photo.py`：**确定性闭环**——视觉读题判分（返回 JSON）→ 画像更新 → 方法卡沉淀(kb_deposit 去重) → 按策略记/问错题；结构化结果存 ctx.state 供 Agent 教学
- [x] `tools/notebook.py::parse_photo`：发笔记图 → 视觉解析 → 智能归类（已有笔记本 / 学科映射 / 主题兜底三档）→ 入库
- [x] `tools/solve_photo.py::image_understand`：通用看图理解（批改/解析共用）

### 业务工具（真实 DB 读写，非空壳）
- [x] `now`、`wrongbook_add/query`、`notebook_add/query`、`kb_deposit/search`、`solve_photo`、`notebook_photo`、`image_understand`

### 渠道层
- [x] 统一消息格式（Inbound/Outbound）+ 长内容双模式切分（>2000 字）
- [x] 抽象渠道接口 + 路由（学生解析 → 长内容分流）
- [x] 控制台渠道（含 `/photo <路径> [备注]` 模拟发图；文本 + 图片均实测）
- [x] QQ OneBot v11 WebSocket 渠道（真实收发，待后端联调）

### CLI 管理（无 WebUI）
- [x] `init` / `version` / `help`、`config show/get/set/validate`、`doctor`（含视觉检查）、`agent tools`、`student list/show/remove`、`kb status`、`serve --channel console|qq`

### 体验 / 交互优化（本次落地，真实交互验证）
- [x] **① 答案出口**：学生明确要答案（「直接告诉我/我不想了/卡住了」）→ 立即给完整解答+方法，不再追问；入口检测 + 提示词双保险。
- [x] **② 错题少打扰**：策略改为——简单/粗心/未知做错→默认不打扰；概念性中难题→才问；薄弱点/连续错→直存；且有「最多一问」护栏（已有待确认记录则不再问）。
- [x] **③ 进行中反馈**：收到图片先回「老师正在看」，避免等待无响应。
- [x] **④ 不说过头话**：提示词约束只承诺已具备能力（讲解/记错题/沉淀方法卡/记笔记），不承诺尚未有的（自动复习推送、可下载讲义、语音、出题）。
- [x] **⑤ 跨轮问题记忆**：`WorkingContext` 持久化最近讲解的题（题干/答案/方法），下一轮注入为背景，追问/变式顺滑衔接。
- [x] **⑥ 长内容偏好**：首次超长先说明并记 split 偏好（讲义模式等落地后再开放，不空许诺）。
- [x] **⑦ 整页多题感知**：视觉返回 `problem_count`，>1 时告知「一道一道来」。
- [x] **⑧ 确认记录更稳**：放宽触发词（「嗯记/要记/记一下」等），老师问得明确（「回『要』就帮你记」）；`record_wrong_question` 加去重护栏防双记。

### M1 收尾（对话编排 / 语料库 / 推送 / CLI / 验收）
- [x] **① 对话编排**：`agent/conversation.py`（无感分段：时间/换题/修正规则 + `decide_segment`；分层压缩：`compress_history` 最近 6 轮原文+更早摘要；语境感知打断：换题→开新轮、修正→并入、补充→并入）；已接入 `entry`。
- [x] **② 知识库·语料库**：`CorpusEntry` 表 + `corpus_add`/`corpus_search` 工具（嵌入可选，回退关键词），与方法库构成双库结构。
- [x] **③ 复习推送落地**：`services/push.py`（到期→生成复习推送→`deliver` 回调）；`serve` 新增后台调度循环（每 60s `process_due`，线程安全送回主循环）。真实测试：到期复习任务 → `process_due` → `deliver` 触发。
- [x] **④ 学生视角验收**：`STUDENT-VIEW-ACCEPTANCE.md`（4 学段 × 6 问，真实交互）；4 学段真实对话均正确适配（小学亲切/初中问进度/高中讲方法/大学讲体系）。
- [x] **CLI `kuro schedule`**：`list / show / cancel`（定时任务管理）。
- [x] **CLI `kuro export`**：`wrongbook <id>` / `report <id>`（导出 Markdown 到 data/exports/）。

### 工程与文档
- [x] 测试：配置/沙箱/队列/Agent 循环/Router 集成/业务工具闭环/画像服务/视觉与闭环/**体验优化（答案出口、确认检测、长内容偏好、跨轮上下文、最多一问、去重）**（44 项通过 + 1 跳过）、ruff 全绿
- [x] `README.md`、`SUGGESTIONS.md`、`REAL-SCENE-REPORT.md`

---

## 三、待办（按规划优先级）

### 新增待办（2026-08-28 用户点名）
- [x] **图片鲁棒性** ✅（`services/imgprep.py` 预处理层：cv2 页面四边形透视矫正（保守策略）+ 投影剖面倾斜矫正（纯 numpy）+ 低对比自动增强 + 限边长；已接入切题管线入口（失败自动用原图不阻塞）；切图产物轻量 OCR 完整性评估（截断疑点/字迹乱提示注入切题输出）；solve/split 视觉提示词增加手写字迹规则——手写是学生作答不混入题干。测试：3° 斜图矫正回检 + 空白图不旋转 + 缺文件回退）
- [x] **Agent 通用文档能力** ✅（`services/docs.py` + 5 工具：`doc_read`（docx/pptx/pdf/txt/md → 结构化文本）、`doc_write`（轻量标记 → docx/pptx/pdf，支持 `![](图)` 嵌图）、`doc_edit`（docx 追加/替换、pptx 加页）、`pdf_ops`（合并/抽页）、`doc_convert`（LibreOffice 互转）。组卷 `bank_extract` 补 `format=docx`（用户定位：组卷只出 pdf/word，通用编辑能力覆盖 docx/pptx/pdf）
- [x] **web_search 真机验证** ✅（实测发现 DuckDuckGo 国内不通 → 加 **Bing 兜底**（免密钥、国内可达），真机返回真实搜索结果；DuckDuckGo 保留为海外兜底）
- [x] **搜索供应商选项** ✅（用户要求 + 提供 Tavily key）：`web_search` 增 `provider` 参数——**默认 bing**（免密钥国内可达）、**tavily**（结果带正文摘要，免月费 1000 次/月）、duckduckgo（海外兜底）；配置块 `models.search`（provider/api_key），所选供应商失败自动沿 bing→duckduckgo 兜底、无 key 明确提示；Tavily 已用真实 key 真机调通；`api.tavily.com` 已入端点白名单。
- [x] **YOLO 切题路线**：已按用户决定**整体移除**（合成数据模型真实页失效；RapidOCR 锚定在真实卷已全对，无必要保留）。相关代码/脚本/训练产物/依赖（ultralytics+torch）已清理；切题 fallback 链收敛为：题号锚定 → 墨迹投影 → 百度专用切题 → 版面分组 → 视觉转写。若未来遇到无题号页面切不准，可重启此路线。
- [ ] 知识库语义检索需嵌入 key（用户提供后配置，可回退关键词，不阻塞）。

### M1 剩余
- [x] **视觉链路 + 拍照解题闭环** ✅（读题判分 → 画像 → 沉淀方法卡 → 记/问错题，真实 API 验证）
- [x] **学生画像 + 错题询问策略** ✅
- [x] **知识库沉淀 / 笔记本发图归类入库** ✅
- [x] **调度器 + 复习引擎** ✅（`services/scheduler.py` 统一调度持久化；`services/review.py` 间隔重复：到期→复测→掌握/强化；记录错题自动排复习，真实验证）
- [x] **作业批改** ✅（`tools/grade_homework.py`：视觉逐题判分+错因归类→错题本+画像；真实 API 验证 1 对 1 错并记录）
- [x] **讲义生成** ✅（`services/lecture.py` + `tools/lecture_gen.py`：LLM 写结构化 Markdown 落盘；长内容模式①；真实验证 22 节/5787 字）
- [x] **自动切题（题集→题库）** ✅：`services/layout.py` 可插拔 LayoutProvider——**默认 RapidOCR（免费、本地、无限、纯 CPU、零配额）**，`group_lines_into_questions`（剔除步骤/答案行 + 垂直间距守卫）+ PIL 裁成每题一张图到 `qbench/`。**已真机验证**：双题页（含"步骤"、"提示"行）→ 只裁出题干，步骤/提示被剔除；切题干净。备选：百度通用文字识别（每日约500次免费）/专用切题 paper_cut（一次性1000次）/Tesseract。**注：云端切题 API（百度/腾讯）都只给一次性 ~1000 次免费，无每月免费；要「一直免费用不完」须自托管（RapidOCR 最轻）。**
  - 配置：`kuro.json` → `models.layout = { provider:"baidu", model:"general", api_key:"<你的API Key>", client_secret:"<你的Secret Key>" }`（百度免费额度，30 天 token）；或 `provider:"tesseract"`（需装 tesseract 二进制）。
- [x] **网络工具** ✅（`tools/web.py`：web_fetch + web_search（DuckDuckGo HTML 免密钥），SSRF 防护）
- [x] **向量检索 RAG** ✅（`kb/embeddings.py` 可插拔嵌入 + `kb/reranker.py` 余弦/关键词重排；`kb_search` 接入，无嵌入密钥回退关键词；语料库未建，仅方法库）
- [~] **语音**：已按用户要求**取消 ASR / TTS 功能**（`services/voice.py`、`tools/voice_tools.py`、`models.asr/tts`、`kuro.json/example` 的 asr/tts 均已移除）。如需恢复可重建。（此前结论记录：无"edge-tts 同款"的稳定免费无 Key 云端 ASR）
- [x] **QQ 渠道 → 官方 botpy SDK** ✅（重写 `adapters/channel/qq.py`：`botpy.Client` + `on_c2c_message_create` 私聊 + `message.reply` 被动回复；`botpy` 不在 PyPI，需 `pip install git+https://github.com/tencent-connect/botpy.git`，未装不影响其他渠道）
- [x] **Docker Compose 一键部署** ✅（多阶段 `Dockerfile`：slim 基础、国内源适配（DaoCloud 拉基础镜像 / HTTP 源 apt+pip 绕过宿主机代理 TLS 拦截 / ghfast.top 装 botpy）、非 root 运行、`kuro.json` 只读挂载不落镜像、`data/` 持久化卷、进程级健康检查；`compose.yaml` 含 `kuro` 服务 + `cli` 附属服务；系统依赖 libgomp1/libxcb1/libgl1/libglib2.0-0。⚠️ BuildKit 内 pip 对本项目打包会死卡（docker run 正常），方案=`scripts/docker-build.sh` 一条龙：宿主机预构建 wheel → 镜像内直接安装。**已实测**：镜像 700MB、容器内 `kuro version`/`config validate`/`agent tools`（26 工具）/关键依赖（rapidocr+botpy+pymupdf）导入全过；容器内端到端切真实 PDF → 14 块与宿主机一致。多架构 amd64/arm64（树莓派可跑）。启动：`bash scripts/docker-build.sh && docker compose up -d`）

### 切题 · 跨页 · 题集（2026-08-28 真机验证）
- [x] **切题工具定位纠正**（用户要求）：split_photo/split_document 仅用于**题集录入**（学生要求录入题库时 Agent 才调）；**讲题不切图**，走 solve_photo 整图视觉。工具描述已写死该边界。
- [x] **真实卷切题验证** ✅：RapidOCR 题号锚定在真实物理考卷（2792×4032 手机拍摄，含跨页残句/大题标题/实验图）近乎完美——题干完整、图形保留、解答不混入、大题标题不进题图。MLLM 划框路线实测不合格（方差大/空返回/坐标系不稳），已封存。
- [x] **跨页题策略** ✅：`plan_question_spans` 纯逻辑规划（残句单独成块 q0_residual / 大题标题丢弃）+ `stitch_crops` 垂直缝合 + 视觉模型二元连续性判断（真实 API 验证：用户的 2 页物理 PDF，第 1 页 15 题尾块 × 第 2 页残句自动缝合为完整题图）。手工缝合工具 `merge_crops` 供 Agent 兜底。
- [x] **文档录入** ✅：`split_document` —— PDF 原生（pymupdf 逐页渲染）、Word/PPT 经 LibreOffice 无头转 PDF（未装给出含修复建议的错误）。真实场景测试：2 页物理 PDF → 14 块题图，跨页自动缝合命中。
- [x] **题集系统** ✅：`QuestionItem` 表（错题+好题收藏，学生一行去重护栏）+ `bank_add`/`bank_list`/`bank_remove`/`bank_extract`（筛选组卷导出 PDF，pymupdf 中文排版+题图嵌入）。**录入策略**注入系统提示词：简单/粗心错不录；有价值错题先问；学生完全没懂自动录；好题 Agent 自主判定（非常好自动录、较好问一嘴、普通不录）。

### M2（闭环与课堂）✅ 全部完成（2026-08-29，真机 7/7）
- [x] 个性化出题：真题四级链（web→jszkk→有道/火花→生成）+ 绘图 + 判分闭环 + 题图多模态校验
- [x] 定时课堂完整：排课/系列课大纲/自动备课/开课推送/课后闭环/应急改期取消（真机 7/7）
- [x] 学习周报：统计+LLM 润色+Word 导出+周日订阅推送
- [x] 校本同步：教材/章节/考试登记，出题备课贴合校情
- [x] 自进化：每 6 条发言提取学生事实→长期记忆→提示词注入

### M3（产品化与开源）✅ 代码完成（2026-08-29）
- [x] 入学诊断：分级摸底→判分→画像基线→学情报告（真机验证）
- [x] 目标管理 + 每日打卡激励（连续天数/里程碑）
- [x] 代码沙箱：AST 白名单 + 隔离子进程 + 超时（验算计算）
- [x] 评测集：evals/eval_cases.json + scripts/evaluate.py（开源质量基线）
- [~] 公式 LaTeX 渲染：QQ 官方 markdown 不支持——公式以题库图片呈现；富文本渲染待渠道能力
- [ ] 开源发布：README 已更新能力清单；待 Docker 最终构建（用户启动 Docker Desktop 后）+ 敏感信息发布前终审

---

## 四、用户反馈记录

- **2026-08-26**：⚠️ 用户强调「每一个功能不能糊我，必须是精美的，不能只是能用就行」。
  已写入长期记忆 [[quality-bar]]，作为后续所有功能的默认验收标准：
  文案完整友好、错误含「现象+原因+建议」、状态反馈闭环、空状态有引导、
  CLI/界面精致美观、防呆减负、可独立验证。
- **2026-08-26**：发现并修复「查询只显示知识点数字 ID 不显示名称」等体验粗糙点。

---

## 五、设计决策备忘

- **会话对象跨层传递**：storage 会话统一 `expire_on_commit=False`，避免 router→entry 分离对象访问抛 `DetachedInstanceError`。
- **LLM 可插拔**：`services/llm.py` 用 `provider` 字段工厂化；`echo/mock` 为离线联调，不要求密钥；`openai` 走 OpenAI 兼容端点（覆盖国产厂商）。
- **工具错误回填**：`registry.execute` 把任何失败包成 `[工具错误/异常]` 字符串回填给模型，保证「错误不崩溃」。
- **视觉可插拔**：`services/vision.py` 与 llm 同构（接口 + OpenAI 兼容实现 + 工厂），走 `user` 消息多模态
  `[{type:text},{type:image_url,image_url:{url:data:...}}]`；`detail` 参数仅在显式给出时下传，
  避免不兼容厂商因未知字段报错。Vendor 由配置 `models.vision` 决定，不写死。
- **拍照解题分工**：视觉模型负责「准确读题 + 结构化方法」，文本 Agent 负责「引导式教学打磨」
  （先思路后答案、向学生提问、学段语气）——两者解耦，视觉/文本可各自换厂商。
- **DeepSeek 实测要点**：Base URL `https://api.deepseek.com`（不带 /v1，代码追加 `/chat/completions`）；
  文本 `deepseek-chat`、视觉 `deepseek-v4-flash-vision-exp` 均 OpenAI 兼容；图片 base64 data URL 放 user 消息。
- **沙箱文件访问**：授权为 `allow` 时仍禁止系统敏感目录（`C:\Windows`、`/etc`、`/usr` 等）；`workspace_only` 限定工作区。
