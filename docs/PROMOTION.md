# OpenMAIC 社区案例投稿文案

> 用途：发到 OpenMAIC 的 Discord / 飞书社区，展示 KuroTutor 是它的生产级中文集成案例。
> 发帖前可把链接换成最新 Release。

---

**KuroTutor: a production QQ tutor built on OpenMAIC classrooms** 🏫

Hi OpenMAIC team & community! We just shipped v0.3.0 of [KuroTutor](https://github.com/ZYT64/KuroTutor) — an open-source (MIT) AI tutor for QQ private chat, and OpenMAIC is now its optional classroom engine.

**How it fits together:**

- A student books a lesson in QQ ("预约一节数学课讲二次函数")
- KuroTutor prepares automatically: generates a lesson plan (Word) from the student's mastery profile + school textbook progress
- It then submits the lesson to the OpenMAIC Live Demo API (`/api/generate-classroom`), polls until ready, and at class time pushes both the Word handout and the classroom link to the student's QQ
- The student studies in an OpenMAIC classroom with AI teachers/classmates; afterwards KuroTutor runs its own post-class loop: summary, homework, profile update, mistake notebook, next lesson scheduling

**What we love about the v1.0.0 API:**

- Clean job-based generation flow (202 + pollUrl) that fits background services perfectly
- `capabilities` in `/api/health` made feature-flagging trivial and forward-compatible
- TTS + web search flags map exactly to what a tutor needs

**Details worth sharing (for anyone integrating):**

1. The `pollUrl` returned is `http://` — reconstruct it from your own base URL or you'll eat a 301
2. Daily quota is 10 generations, so we compress the lesson plan into a ≤300-word brief with the main LLM before submitting — one classroom per lesson, no wasted quota
3. Classroom URL is stored per lesson instance and re-pushed if generation finishes after class start

Repo: https://github.com/ZYT64/KuroTutor (README in Chinese & English)
KuroTutor is a Chinese-market tutor (QQ, 人教版 textbooks, 中考/高考 oriented), MIT licensed. We'd love feedback — especially on whether a self-hosted OpenMAIC mode makes sense for us next.

---

# 演示视频脚本（3 分钟手机录屏版）

> 录制方式：电脑开 `kuro serve --channel console`，或直接手机录 QQ 真机对话（更有说服力，推荐）。
> 每段录完停顿 1 秒再发下一条，剪辑时快进等待时间。

## 开场（0:00-0:15）
画外音/字幕：「这是一个部署在自己服务器上的 AI 家教，就住在 QQ 里。今天让它给一个初中生上一节课。」

## 场景 1 · 摸底（0:15-0:50）
发送：「老师你好，我是新来的」
→ 机器人主动提出入学诊断，发 5 道由易到难的题
字幕：「新学生先摸底，答完自动建画像」
（快进答完）→ 收到判分报告 + 学习起点

## 场景 2 · 拍照解题（0:50-1:40）
发送：一张数学题照片
→ 「老师正在看」→ 引导式讲解（先思路不给答案）
字幕：「先给思路，不给答案——卡住了才给台阶」
追问一句「还是不懂」→ 收到更细的提示

## 场景 3 · 出真题 + 画图（1:40-2:20）
发送：「给我出 2 道二次函数的真题」
→ 收到带来源的真题（可含题图）
发送：「再画个 y=x²-2x-3 的图」
→ 收到函数图像
字幕：「题目来自网络真题，错题自动进错题本」

## 场景 4 · 排课与互动课堂（2:20-3:00）
发送：「约一节数学课，明天晚上八点」
→ 排课成功提示
字幕：「开课前自动备课：Word 讲义 + OpenMAIC 互动课堂（AI 老师+AI 同学）」
展示收到的讲义文件和课堂链接（如已有真机录屏可放）
结尾字幕：「开源 MIT · 自己的服务器自己的数据 · github.com/ZYT64/KuroTutor」
