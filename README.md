<div align="center">

# KuroTutor

**QQ 私聊里的 AI 家教**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/ZYT64/KuroTutor/ci.yml?style=flat-square&label=CI)](https://github.com/ZYT64/KuroTutor/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-2496ED?style=flat-square&logo=docker&logoColor=white)](#docker)
[![QQ](https://img.shields.io/badge/QQ-%E5%AE%98%E6%96%B9%20SDK-12B7F5?style=flat-square&logo=tencentqq&logoColor=white)](#接入-qq)

[English](README_EN.md) | 简体中文

</div>

KuroTutor 是一个部署在自己服务器上的 AI 家教机器人，接入 QQ 私聊。学生把不会的题目拍下来发过去，它读题之后先给思路、逐步引导，而不是直接甩答案；做错的题会记进错题本，按遗忘曲线排期，过几天再推回来重做，直到真正掌握。

除了答疑，它还管这些事：

- 整页作业拍一张，逐题判分，错因归类，错题自动入库
- 根据学生的薄弱点和校内进度出题，优先从网上搜真题；讲函数时可以直接画图
- 整卷试卷自动切成一题一图（多模态视觉识别，支持歪图矫正、跨页缝合），按知识点组卷导出 PDF 或 Word
- 约课：单次课或系列课，到点自动备课、提醒上课，课后总结并布置作业
- 每周出一份学习周报，练习量、错题、掌握度变化都在里面，可导出 Word
- 新学生先做一次摸底测试，确定学习起点，之后的讲解和出题都从这里出发

答疑之外的管理操作走命令行，没有 WebUI。

## 安装

需要 Python 3.11+。

```bash
git clone https://github.com/ZYT64/KuroTutor.git
cd kurotutor
python -m venv .venv
.venv/Scripts/pip install -e .          # Windows；Linux/mac 用 .venv/bin/pip
```

初始化配置后可以在终端里先联调（没有模型密钥时选离线演示模式也能跑通）：

```bash
kuro init
kuro serve --channel console
```

## 接入 QQ

在 [QQ 开放平台](https://q.qq.com)创建机器人，把 AppID 和 Secret 填进 `kuro.json` 的 `channel` 字段（`kuro init` 会引导你填），然后启动：

```bash
kuro serve --channel qq
```

之后在 QQ 里私聊机器人就能用。

## 配置

配置都在根目录的 `kuro.json`，模板见 [`kuro.example.json`](kuro.example.json)。

文本模型是唯一必填项，任何 OpenAI 兼容服务（GLM、DeepSeek、通义等）填上 `base_url`、`model`、`api_key` 就能用。视觉模型不填时自动复用主模型，前提是主模型支持图片输入。搜索和题库的 key 可选，用于出题时找真题。

密钥只保存在本地 `kuro.json`，不要提交到代码仓库或发给别人。

## Docker

```bash
bash scripts/setup.sh    # 一条命令：构建 → 交互式引导填密钥 → 启动
```

镜像支持 amd64 和 arm64，不需要 GPU，数据和配置通过挂载卷持久化，重启自动恢复。

部署后自带一个**网页管理面板**（只读）：浏览器打开 `http://<服务器IP>:8001`，用 `kuro.json` 里 `webui.token` 设置的口令登录，可以看学生学情、错题本、排课情况、脱敏后的配置，也能一键备份数据。

日常管理（CLI）：

```bash
kuro doctor                   # 检查配置、数据库、模型、渠道
kuro student list             # 学生列表
kuro student show <id>        # 学情详情：画像、错题、进度
kuro export wrongbook <学生>  # 导出错题本
kuro export report <学生>     # 导出学习报告
```

容器部署时把 `kuro` 换成 `docker compose run --rm cli kuro` 即可。

## 许可

[MIT](LICENSE)
