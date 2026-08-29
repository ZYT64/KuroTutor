<div align="center">

# KuroTutor 🌙

**QQ 私聊里的 24 小时 AI 私人老师**

_全科辅导（小学到大学）· 像真人私教一样教，不像题库一样甩答案_

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-2496ED?style=flat-square&logo=docker&logoColor=white)](#docker-部署)
[![QQ 机器人](https://img.shields.io/badge/QQ-官方%20SDK-12B7F5?style=flat-square&logo=tencentqq&logoColor=white)](#接入-qq)

</div>

---

KuroTutor 是一个跑在自己服务器上的 AI 家教机器人。学生在 QQ 里像跟老师聊天一样提问，它会讲解题目、批改作业、记住每道错题并安排复习、定期上课——所有这些都是通过自然对话完成的，没有菜单和指令。

## ✨ 它能做什么

- **📷 拍照解题**——发张题目照片，老师引导你思考，先给思路再对答案，而不是直接甩解析
- **📝 作业批改**——整页作业拍一张，逐题判分、归因错因，错题自动记入错题本
- **🔁 复习不遗忘**——错题按记忆曲线自动排期，到点主动推送复习
- **🎯 个性化出题**——按你的水平和进度找真题练（自动从网上搜题、下载配图），也可画函数图辅助讲题
- **✂️ 试卷变题库**——整张试卷自动切成一题一图（多模态视觉识别，支持歪图矫正、跨页缝合），错题好题随时组卷导出 PDF/Word
- **📅 定时上课**——约一节 1v1 小课，到点自动备课、开课提醒、课后总结布置作业
- **📊 学习周报**——每周自动总结学习情况，生成 Word 报告
- **🩺 入学诊断**——新学生先摸底测几分，定出学习起点，之后讲题、出题、排课都因人而异

## 🚀 快速开始

```bash
# ① 安装（需要 Python 3.11+）
git clone https://github.com/your-org/kurotutor.git
cd kurotutor
python -m venv .venv
.venv/Scripts/pip install -e .          # Windows；Linux/mac 用 .venv/bin/pip

# ② 初始化配置（交互式引导，填一个模型 API key 就能跑）
kuro init

# ③ 在终端里先试试（扮演学生和老师聊天，Ctrl+C 退出）
kuro serve --channel console
```

没有模型密钥也能先跑通：`kuro init` 时选择离线演示模型即可体验完整对话链路。

## 🔌 接入 QQ

1. 在 [QQ 开放平台](https://q.qq.com)免费创建一个机器人，拿到 AppID 和 Secret
2. 运行 `kuro init` 按引导填入
3. 启动：`kuro serve --channel qq`，然后在 QQ 里搜索你的机器人，私聊即可

## ⚙️ 配置

所有配置都在项目根目录的 `kuro.json`（`kuro init` 自动生成）：

| 配置 | 说明 |
|---|---|
| 模型 API key | **必填一项**。任意 OpenAI 兼容的大模型（GLM / DeepSeek / 通义等），填在 `models.llm` |
| 视觉模型 | 看图解题用。主模型本身支持图片时可以不填，自动复用 |
| 搜索 / 题库 | 可选。配了 Tavily key 搜题更稳；火花题库 key 作为搜题兜底 |
| QQ 机器人 | 接 QQ 才需要 |

> 🔒 密钥只保存在你本地的 `kuro.json`，请注意保管，不要把它提交到代码仓库或分享给他人。

## 🐳 Docker 部署

不想管 Python 环境的话，一条命令跑在 Docker 里（低配服务器、树莓派都能跑）：

```bash
cp kuro.example.json kuro.json    # 填好密钥
docker compose up -d              # 后台常驻，重启自动恢复
```

## 🧰 日常管理

管理不靠网页，全在命令行：

```bash
kuro doctor                   # 体检：配置/数据库/模型/渠道逐项诊断
kuro student list             # 看有哪些学生、学得怎么样
kuro export wrongbook <学生>  # 导出错题本
```

## 📄 许可

[MIT](LICENSE) · 学生数据只存在你自己的服务器上
