"""CLI 共享 UI 助手工夫：统一的 Rich 输出风格。

约定：所有命令输出走本模块，保证全 CLI 风格一致、层级清晰、反馈完整。
- 成功用绿色 ✓，失败用红色 ✗，提醒用黄色 !，信息用青色 ℹ。
- 用 Panel/Table 呈现结构化信息，避免裸字符串。
"""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def ok(msg: str) -> None:
    console.print(f"[green]✓[/green] {msg}")


def err(msg: str) -> None:
    console.print(f"[red]✗[/red] {msg}")


def warn(msg: str) -> None:
    console.print(f"[yellow]![/yellow] {msg}")


def info(msg: str) -> None:
    console.print(f"[cyan]ℹ[/cyan] {msg}")


def heading(text: str) -> None:
    console.print(Panel(text, style="bold cyan", border_style="cyan"))


def subheading(text: str) -> None:
    console.print(f"\n[bold]{text}[/bold]\n")


def kv_table(title: str, rows: list[tuple[str, str]], *, caption: str | None = None) -> None:
    """两列键值表（用于展示配置/详情）。值非空用淡色，空值用暗红提示。"""
    table = Table(title=title, box=box.ROUNDED, show_header=False, expand=False)
    table.add_column("项", style="bold dim", no_wrap=True)
    table.add_column("值", style="white")
    for key, value in rows:
        style = "dim" if not value else ""
        table.add_row(key, value or "[dim]（空）[/dim]", style=style)
    console.print(table)
    if caption:
        console.print(f"[dim]{caption}[/dim]")


def list_table(title: str, header: list[str], rows: list[list[str]]) -> None:
    """通用列表表格。无数据时给出空状态引导。"""
    if not rows:
        warn("暂无数据。")
        return
    table = Table(title=title, box=box.ROUNDED, header_style="bold cyan")
    for col in header:
        table.add_column(col)
    for row in rows:
        table.add_row(*row)
    console.print(table)
