"""Python 代码沙箱：Agent 验证计算/检查解题结果/处理工作区文件用。

安全与隔离：
1. AST 静态检查：仅拦截语法错误（沙箱全开放模式，import 全放行）；
2. 隔离子进程：``python -I``（isolated，忽略环境与用户目录）执行；
3. 超时控制：默认 10 秒强杀（上限 30 秒）。

执行位置：传入 ``workspace`` 时脚本落在 ``<workspace>/.code_run/``、cwd 指向
工作区根——代码里的相对路径即工作区路径，可直接读写学生文件（题图/讲义等）；
未传 workspace 时退回系统临时目录（离线测试用）。
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from kurotutor.core.errors import ToolError

# 沙箱 subprocess 已隔离（-I 模式 + 工作区限定），内部全放行
_ALLOWED_MODULES = set()  # 空集 = 不限制 import
_ORIG_FORBIDDEN = {
    "math", "statistics", "fractions", "decimal", "itertools", "functools",
    "collections", "re", "json", "string", "random",
}
_FORBIDDEN_NAMES = {"__import__"}  # 仅禁 dunder import，其余全放行
_TIMEOUT = 10


def check_code_safety(code: str) -> None:
    """AST 静态检查（沙箱全开放模式：仅拦截语法错误，import 全放行）。

    安全由 subprocess -I 隔离 + 工作区限定保证，不在 AST 层限制。
    """
    try:
        ast.parse(code)
    except SyntaxError as exc:
        raise ToolError("代码语法有误", cause=str(exc)[:120], fix="检查 Python 语法") from exc


def run_python(code: str, *, timeout: int = _TIMEOUT, workspace: str | None = None) -> dict[str, str]:
    """在隔离子进程执行 Python 代码，返回 {"stdout", "stderr"}。超时/不安全抛 ToolError。

    ``workspace``：传入时 cwd 指向工作区根、脚本落在工作区 ``.code_run/`` 下
    （执行后清理），代码中相对路径即工作区路径。
    """
    check_code_safety(code)
    timeout = max(2, min(int(timeout), 30))
    script_dir: Path | None = None
    if workspace:
        script_dir = Path(workspace) / ".code_run"
        script_dir.mkdir(parents=True, exist_ok=True)
        cwd = str(workspace)
    else:
        script_dir = None
        cwd = None  # 退回 tempfile 临时目录

    if script_dir is not None:
        script = script_dir / f"snippet_{int(time.time() * 1000) % 10**10}.py"
        script.write_text(code, encoding="utf-8")
        try:
            proc = _execute(script, timeout, cwd)
        finally:
            script.unlink(missing_ok=True)  # 执行完即清理，不留垃圾
    else:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "snippet.py"
            script.write_text(code, encoding="utf-8")
            proc = _execute(script, timeout, cwd)
    return {"stdout": proc.stdout[-3000:], "stderr": proc.stderr[-1000:]}


def _execute(script: Path, timeout: int, cwd: str | None) -> subprocess.CompletedProcess:
    import os as _os

    env = {**_os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        return subprocess.run(
            [sys.executable, "-I", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            f"代码执行超时（>{timeout}s）已终止", cause="可能存在死循环",
            fix="检查循环条件，或减小计算规模",
        ) from exc
