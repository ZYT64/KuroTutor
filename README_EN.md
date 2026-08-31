<div align="center">

# KuroTutor

**An AI tutor in your QQ private chat**

[![Release](https://img.shields.io/github/v/release/ZYT64/KuroTutor?style=flat-square&label=latest)](https://github.com/ZYT64/KuroTutor/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/ZYT64/KuroTutor/ci.yml?style=flat-square&label=CI)](https://github.com/ZYT64/KuroTutor/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-2496ED?style=flat-square&logo=docker&logoColor=white)](#docker)
[![QQ](https://img.shields.io/badge/QQ-official%20SDK-12B7F5?style=flat-square&logo=tencentqq&logoColor=white)](#qq-setup)

English | [简体中文](README.md)

</div>

KuroTutor is a self-hosted AI tutor that lives in QQ private chat, with a built-in web admin panel. Students send a photo of a problem they're stuck on; the tutor walks them through the idea step by step instead of dumping a full solution. Wrong answers go into a mistake notebook and come back a few days later for spaced review, until they're truly mastered.

## Features

- **Photo solving / homework grading**: grade a whole homework page from one photo with per-question scoring and mistake categorization; step-by-step guidance tuned to the student's grade level (elementary through college)
- **Spaced-repetition review**: mistakes are scheduled on a forgetting curve and pushed back when due, with pass-rate and mastery tracking
- **Personalized practice**: questions matched to weak points and school progress, preferring real exam questions found online (with images); repeats within 7 days are skipped; function plotting for graph-heavy topics
- **Exam paper processing**: split a full paper into single-question images (multimodal vision, perspective correction, cross-page stitching), then compose PDF/Word worksheets by knowledge point
- **Lesson scheduling**: single or series lessons with automatic preparation, start reminders, and post-class summaries with homework
- **Interactive classrooms (optional)**: powered by [OpenMAIC](https://github.com/THU-MAIC/OpenMAIC) — each lesson gets a multi-agent classroom link where AI teachers and classmates lecture, discuss, and draw on a whiteboard in real time
- **Weekly reports**: practice volume, mistakes, review outcomes, and mastery trends, exportable as Word
- **Placement test**: new students take a diagnostic to establish a starting point and profile
- **Goals & check-ins**: goal tracking and daily check-in streaks
- **Code sandbox**: isolated Python execution to verify calculations while tutoring
- **Web admin panel**: student progress and mastery trends, mistake notebook, scheduling, live config editing (masked keys), data backup and version rollback, light/dark themes
- **Encrypted cloud backup (optional)**: full-data snapshots AES-encrypted and pushed daily as separate versions to your own Gitee private repo, with one-click rollback

All tutoring happens in QQ chat; administration happens in the web panel and command line.

## Install

Python 3.11+ required.

```bash
git clone https://github.com/ZYT64/KuroTutor.git
cd KuroTutor
python -m venv .venv
.venv/Scripts/pip install -e .          # Windows; Linux/mac: .venv/bin/pip
```

Configure, then try it in the terminal first (an offline demo mode works without any API key):

```bash
kuro init
kuro serve --channel console
```

## QQ setup

Create a bot on the [QQ Open Platform](https://q.qq.com), put the AppID and Secret into the `channel` section of `kuro.json` (`kuro init` walks you through it), then start:

```bash
kuro serve --channel qq
```

Message the bot in a private chat and it just works.

## Configuration

All configuration lives in `kuro.json` at the project root; see [`kuro.example.json`](kuro.example.json) for a template. You can also edit everything from the web panel after deployment.

The text model is the only required setting — any OpenAI-compatible service (GLM, DeepSeek, Qwen, etc.) with `base_url`, `model`, and `api_key`. The vision model falls back to the main model when unset, as long as it accepts images. Embedding, search, and question-bank keys are optional: embeddings enable semantic search in the knowledge base; search and question banks source real exam questions.

API keys stay in your local `kuro.json` — don't commit it or share it.

## Docker

```bash
bash scripts/setup.sh    # build → guided key setup → start, in one command
```

Images are built for amd64 and arm64, no GPU needed. Data and configuration persist through mounted volumes and survive restarts.

### Web panel

After deployment open `http://<server-ip>:8002` (default port) and sign in with the token set in `webui.token`.

The panel covers student progress and mastery trend charts, the mistake notebook (filterable per student), lesson scheduling, **live editing of every setting** (model keys, QQ credentials, cloud backup — keys masked on display), and data backup. Light and dark themes are one click away.

To change the host port, set `KURO_PANEL_PORT` in a `.env` file next to `compose.yaml` and restart; the container-side port is `webui.port` in `kuro.json`.

### Encrypted cloud backup (optional)

Fill in your Gitee private repo, personal token, and an encryption password under Settings → Cloud backup. KuroTutor then pushes an AES-encrypted full-data snapshot (database, workspaces, knowledge base, config) daily as its own version — today never overwrites yesterday — with one-click rollback from the panel. Only ciphertext ever leaves your server, and the encryption password is the only way to decrypt it.

### Upgrade

```bash
docker compose run --rm cli kuro upgrade
```

One command pulls the latest code, rebuilds the package and image, and rolls the deployment forward. Data volumes persist across upgrades.

## Command line

For container deployments, replace `kuro` with `docker compose run --rm cli kuro`:

```bash
kuro doctor                   # check config, database, models, channels
kuro student list             # list students
kuro student show <id>        # profile, mistakes, progress
kuro export wrongbook <name>  # export the mistake notebook
kuro export report <name>     # export a learning report
kuro backup [--cloud]         # local / cloud backup
kuro restore                  # roll back to a cloud backup version
```

## License

[MIT](LICENSE). Student data stays on the deployer's own server (or in the deployer's own encrypted backup repo), with export and deletion built in.
