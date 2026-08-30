<div align="center">

# KuroTutor

**An AI tutor in your QQ private chat**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Release](https://img.shields.io/github/v/release/ZYT64/KuroTutor?style=flat-square&label=latest)](https://github.com/ZYT64/KuroTutor/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/ZYT64/KuroTutor/ci.yml?style=flat-square&label=CI)](https://github.com/ZYT64/KuroTutor/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-2496ED?style=flat-square&logo=docker&logoColor=white)](#docker)
[![QQ](https://img.shields.io/badge/QQ-official%20SDK-12B7F5?style=flat-square&logo=tencentqq&logoColor=white)](#qq-setup)

English | [简体中文](README.md)

</div>

KuroTutor is a self-hosted AI tutor that lives in QQ private chat. Students send a photo of a problem they're stuck on; the tutor walks them through the idea step by step instead of dumping a full solution. Wrong answers go into a mistake notebook and come back a few days later for spaced review — until they're mastered.

Beyond Q&A, it grades whole homework pages, generates practice questions from real exam sources, turns exam papers into per-question images, schedules one-on-one lessons, and sends weekly progress reports. Day-to-day administration happens over the command line and a built-in web panel.

## Features

- Grade whole homework pages from a single photo: per-question scoring with mistake categorization; mistakes are filed automatically
- Spaced-repetition review: mistakes are scheduled on a forgetting curve and pushed back when due
- Practice questions matched to the student's weak points and school progress, preferring real exam questions found online; function plotting for graph-heavy topics
- Exam paper processing: split a full paper into single-question images (multimodal vision, with perspective correction and cross-page stitching), then compose PDF/Word worksheets by knowledge point
- Lesson scheduling: single or series lessons with automatic preparation, start reminders, and post-class summaries with homework
- Weekly reports: practice volume, mistakes, and mastery trends, exportable as Word
- Placement test for new students to establish a starting point
- Optional interactive classrooms powered by [OpenMAIC](https://github.com/THU-MAIC/OpenMAIC): AI teachers and classmates lecture, discuss, and draw on a whiteboard in real time
- Web admin panel: student progress, mistake notebook, scheduling, live config editing (masked keys), light/dark themes
- Optional encrypted cloud backup to your own Gitee private repo: full-data snapshots as daily versions with one-click rollback

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

All configuration lives in `kuro.json` at the project root; see [`kuro.example.json`](kuro.example.json) for a template.

The text model is the only required setting — any OpenAI-compatible service (GLM, DeepSeek, Qwen, etc.) with `base_url`, `model`, and `api_key`. The vision model falls back to the main model when unset, as long as it accepts images. Search and question-bank keys are optional and used for sourcing real exam questions.

API keys stay in your local `kuro.json` — don't commit it or share it.

## Docker

```bash
bash scripts/setup.sh    # build → guided key setup → start, in one command
```

Images are built for amd64 and arm64, no GPU needed. Data and configuration persist through mounted volumes and survive restarts.

### Web panel

After deployment open `http://<server-ip>:8002` and sign in with the token set in `webui.token`. To change the host port, set `KURO_PANEL_PORT` in a `.env` file next to `compose.yaml`.

### Encrypted cloud backup (optional)

Fill in your Gitee private repo, personal token, and an encryption password under Settings → Cloud backup in the panel. KuroTutor then pushes an encrypted full-data snapshot daily as its own version — today never overwrites yesterday — with one-click rollback from the panel. Only ciphertext ever leaves your server.

Day-to-day administration:

```bash
kuro doctor                   # check config, database, models, channels
kuro student list             # list students
kuro student show <id>        # profile, mistakes, progress
kuro export wrongbook <name>  # export the mistake notebook
kuro backup                   # back up all data into a single archive
```

For container deployments, replace `kuro` with `docker compose run --rm cli kuro`.

## Feedback

Questions, suggestions, partnerships: [kurotutor@tinkmail.me](mailto:kurotutor@tinkmail.me), or open an [issue](https://github.com/ZYT64/KuroTutor/issues).

## License

[MIT](LICENSE). Student data stays on the deployer's own server, with export and deletion built in.
