<div align="center">

# KuroTutor

**QQ 私聊里的 AI 家教**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-2496ED?style=flat-square&logo=docker&logoColor=white)](#docker)
[![QQ](https://img.shields.io/badge/QQ-%E5%AE%98%E6%96%B9%20SDK-12B7F5?style=flat-square&logo=tencentqq&logoColor=white)](#接入-qq)

</div>

KuroTutor 接入 QQ 私聊，做一个随叫随到的家教。学生把不会的题目拍下来发过去，它先讲思路，学生跟上了再对答案；做错的题会记入错题本，隔几天再推回来重做。整卷试卷可以切成一题一图存进题集，之后按知识点组卷导出。

## 功能

- **拍照解题**：视觉模型读题，引导式讲解，按学段（小学到大学）调整讲法与深度
- **作业批改**：整页作业逐题判分，错因归类，错题自动入库
- **错题复习**：错题按间隔重复排期，到期主动推送复测
- **出题**：根据学生薄弱点和校内进度出题，优先从网上找真题（含配图），也可以直接画函数图像辅助讲解
- **自动切题**：多模态视觉识别，整卷切成单题图片；支持透视矫正和跨页题目缝合
- **排课**：单次课和系列课，到点自动备课、开课提醒，课后自动总结并布置作业
- **学习周报**：每周汇总练习、错题、掌握度变化，可导出 Word
- **入学诊断**：新生摸底测试，判分后确定学习起点并建立画像

所有交互都在 QQ 对话里完成，没有菜单和指令，学生正常说话即可。

## 安装

需要 Python 3.11+。

```bash
git clone https://github.com/your-org/kurotutor.git
cd kurotutor
python -m venv .venv
.venv/Scripts/pip install -e .          # Windows；Linux/mac 用 .venv/bin/pip
```

初始化配置后即可在终端联调（没有模型密钥也可以选离线演示模式）：

```bash
kuro init
kuro serve --channel console
```

## 接入 QQ

1. 在 [QQ 开放平台](https://q.qq.com)创建机器人，拿到 AppID 和 Secret
2. `kuro init` 时填入，或直接编辑 `kuro.json` 的 `channel` 字段
3. 启动 `kuro serve --channel qq`，在 QQ 里私聊机器人即可

## 配置

配置在根目录的 `kuro.json`，`kuro init` 会生成模板（见 [`kuro.example.json`](kuro.example.json)）。

文本模型是唯一必填项，填任意 OpenAI 兼容服务（GLM、DeepSeek、通义等）的 `base_url`、`model` 和 `api_key` 即可。视觉模型不填时自动使用主模型（主模型需支持图片）。搜索和题库 key 可选，用于出题时从网上找真题。

密钥保存在本地 `kuro.json`，注意不要提交到代码仓库或分享给他人。

## Docker

```bash
cp kuro.example.json kuro.json    # 填好密钥
docker compose up -d
```

镜像支持 amd64 和 arm64，不依赖 GPU。数据和配置通过挂载卷持久化，容器重启后自动恢复。管理操作走 CLI：

```bash
docker compose run --rm cli kuro doctor
docker compose run --rm cli kuro student list
```

## 常用命令

```bash
kuro doctor                   # 检查配置、数据库、模型、渠道
kuro student list             # 学生列表
kuro student show <id>        # 学情详情（画像、错题、进度）
kuro export wrongbook <学生>  # 导出错题本
kuro export report <学生>     # 导出学习报告
```

## 许可

[MIT](LICENSE)。学生数据只存储在部署者自己的服务器上，提供导出和删除接口。
