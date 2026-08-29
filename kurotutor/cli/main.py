"""KuroTutor CLI 入口。

所有管理操作都走命令行（架构红线：无 WebUI）。命令树见项目 CLAUDE.md 第 4.8 节。
风格统一见 :mod:`kurotutor.cli.ui`。
"""

from __future__ import annotations

import contextlib
import sys

import typer

from kurotutor import __version__
from kurotutor.cli import (
    agent,
    init,
    kb,
    student,
    ui,
)
from kurotutor.cli import (
    config as config_cmd,
)
from kurotutor.cli import (
    doctor as doctor_cmd,
)
from kurotutor.cli import (
    export as export_cmd,
)
from kurotutor.cli import (
    schedule as schedule_cmd,
)
from kurotutor.cli import (
    serve as serve_cmd,
)
from kurotutor.cli import (
    upgrade as upgrade_cmd,
)

# 保证 Windows 控制台按 UTF-8 渲染中文（避免 GBK 下乱码），并让管道输入按 UTF-8 解码
with contextlib.suppress(AttributeError, ValueError):
    for _stream in (sys.stdout, sys.stderr, sys.stdin):
        _stream.reconfigure(encoding="utf-8")

app = typer.Typer(
    name="kuro",
    help="KuroTutor —— QQ 私聊里的 24 小时 AI 私人老师（全科、从零自研、开源）。",
    no_args_is_help=True,
    add_completion=False,
)

# 注册子命令组
app.add_typer(init.app, name="init", help="交互式初始化配置")
app.add_typer(config_cmd.app, name="config", help="配置管理")
app.add_typer(agent.app, name="agent", help="Agent 工具/技能查看")
app.add_typer(student.app, name="student", help="学生管理（学情查看/合规删除）")
app.add_typer(kb.app, name="kb", help="知识库管理")
app.add_typer(export_cmd.app, name="export", help="数据导出（错题本/学习报告）")
app.add_typer(schedule_cmd.app, name="schedule", help="定时任务管理")

# 单一功能命令（非命令组）
app.command("serve", help="启动服务")(serve_cmd.serve_command)
app.command("doctor", help="健康检查")(doctor_cmd.doctor_command)
app.command("upgrade", help="更新到最新版本（git 拉取 + 容器重建重启）")(upgrade_cmd.upgrade_command)


@app.callback()
def _root() -> None:
    """根级无操作回调（占位，保证 help 正常）。"""


@app.command("version")
def version_command() -> None:
    """显示版本。"""
    ui.heading(f"KuroTutor v{__version__}")
    ui.info("QQ 私聊里的 24 小时 AI 私人老师 · 全科 · 从零自研 · MIT 开源")


@app.command("help")
def help_command() -> None:
    """显示帮助。"""
    typer.echo(app.get_help())


if __name__ == "__main__":
    app()
