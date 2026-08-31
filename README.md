<div align="center">

# KuroTutor

**QQ 私聊里的 AI 家教**

[![Release](https://img.shields.io/github/v/release/ZYT64/KuroTutor?style=flat-square&label=%E6%9C%80%E6%96%B0%E7%89%88%E6%9C%AC)](https://github.com/ZYT64/KuroTutor/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/ZYT64/KuroTutor/ci.yml?style=flat-square&label=CI)](https://github.com/ZYT64/KuroTutor/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-2496ED?style=flat-square&logo=docker&logoColor=white)](#docker-部署)
[![QQ](https://img.shields.io/badge/QQ-%E5%AE%98%E6%96%B9%20SDK-12B7F5?style=flat-square&logo=tencentqq&logoColor=white)](#接入-qq)

[English](README_EN.md) | 简体中文

</div>

KuroTutor 是一个部署在自己服务器上的 AI 家教机器人，接入 QQ 私聊，自带网页管理面板。学生把不会的题目拍下来发过去，它读题之后先给思路、逐步引导，而不是直接甩答案；做错的题会记进错题本，按遗忘曲线排期，过几天再推回来重做，直到真正掌握。

## 功能

- **拍照解题 / 作业批改**：整页作业拍一张，逐题判分、错因归类，错题自动入库；单题讲解先给思路再给答案，按学段（小学到大学）调整讲法
- **错题复习**：错题按遗忘曲线排期，到期主动推送复测，支持查看复习通过率与掌握度趋势
- **个性化出题**：根据学生薄弱点和校内进度出题，优先从网上搜真题（带配图），搜不到再智能生成；7 天内出过的题自动去重；讲函数时直接画图
- **自动切题**：整卷试卷切成一题一图（多模态视觉识别，支持歪图矫正、跨页缝合），按知识点组卷导出 PDF / Word
- **定时上课**：单次课或系列课，到点自动备课、提醒上课，课后自动总结并布置作业
- **互动课堂（可选）**：接入 [OpenMAIC](https://github.com/THU-MAIC/OpenMAIC)，开课时自动生成多智能体互动课堂链接，AI 老师与 AI 同学实时讲课、讨论、板书、出题
- **学习周报**：每周汇总练习量、错题、复习效果与掌握度变化，可导出 Word
- **入学诊断**：新学生先做摸底测试，判分后确定学习起点并建立画像
- **目标与打卡**：学习目标管理、每日打卡激励
- **代码沙箱**：隔离子进程执行 Python，讲理科题时验算计算结果
- **网页管理面板**：浏览器查看学生学情、掌握度趋势、错题本、排课与配置，支持在线修改配置、数据备份与按版本回滚
- **加密云备份（可选）**：全量数据加密后自动推送到你自己的 Gitee 私有仓库，每天一个独立版本，一键回滚

所有教学交互都在 QQ 对话里完成；管理操作在网页面板和命令行中完成。

## 安装

需要 Python 3.11+。

```bash
git clone https://github.com/ZYT64/KuroTutor.git
cd KuroTutor
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

配置都在根目录的 `kuro.json`，模板见 [`kuro.example.json`](kuro.example.json)，也可以部署后在网页面板里直接改。

文本模型是唯一必填项，任何 OpenAI 兼容服务（GLM、DeepSeek、通义等）填上 `base_url`、`model`、`api_key` 就能用。视觉模型不填时自动复用主模型（主模型需支持图片输入）。嵌入模型、搜索、题库的 key 可选：嵌入用于知识库语义检索，搜索与题库用于出题时找真题。

密钥只保存在本地 `kuro.json`，不要提交到代码仓库或发给别人。

## Docker 部署

```bash
bash scripts/setup.sh    # 一条命令：构建 → 交互式引导填密钥 → 启动
```

镜像支持 amd64 和 arm64，不需要 GPU，数据和配置通过挂载卷持久化，重启自动恢复。

### 管理面板

部署后浏览器打开 `http://<服务器IP>:8002`（默认端口），用 `kuro.json` 里 `webui.token` 设置的口令登录。

面板能力：学生学情与掌握度趋势图表、错题本（按学生筛选）、排课表、**全部配置在线修改**（模型密钥 / QQ 凭据 / 云备份等，密钥自动脱敏显示）、数据备份。浅色 / 深色主题一键切换。

自定义端口：在 compose 目录建 `.env` 文件写 `KURO_PANEL_PORT=你的端口`，重启生效；容器内端口由 `kuro.json` 的 `webui.port` 控制。

### 云备份（可选）

在面板「设置 → 云备份（Gitee）」里填入你的 Gitee 私有仓库、私人令牌和加密口令即可开启：每天自动把全量数据（数据库 / 工作区 / 知识库 / 配置）AES 加密后推送一个独立版本——今天的备份不会吞掉昨天的，面板上一键回滚到任意版本。上传的永远是密文，加密口令是唯一解密凭据。

### 升级

```bash
docker compose run --rm cli kuro upgrade
```

一条命令完成：拉取最新代码 → 重建程序包 → 重建镜像 → 滚动重启。数据卷持久化，升级不丢数据。

## 命令行管理

容器部署时把 `kuro` 换成 `docker compose run --rm cli kuro`：

```bash
kuro doctor                   # 检查配置、数据库、模型、渠道
kuro student list             # 学生列表
kuro student show <id>        # 学情详情：画像、错题、进度
kuro export wrongbook <学生>  # 导出错题本
kuro export report <学生>     # 导出学习报告
kuro backup [--cloud]         # 本地备份 / 云端备份
kuro restore                  # 从云备份按版本回滚
```

## 许可

[MIT](LICENSE)。学生数据只存在部署者自己的服务器上（开云备份时也是你自己的加密仓库），提供导出与删除接口。
