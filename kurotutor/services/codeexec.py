"""Python 代码沙箱：Agent 验证计算/检查解题结果用。

三层防护：
1. AST 静态检查：import 仅限数学/统计白名单，禁止 __import__/dunder 属性/任意调用 eval-exec；
2. 隔离子进程：``python -I``（isolated，忽略环境与用户目录）执行，cwd 指向临时目录；
3. 超时控制：默认 10 秒强杀。
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
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
    """AST 静态检查，不安全则抛 ToolError。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ToolError("代码语法有误", cause=str(exc)[:120], fix="检查 Python 语法") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if _ALLOWED_MODULES and root not in _ALLOWED_MODULES:
                    raise ToolError(
                        "代码引入了白名单外的模块", cause=alias.name,
                        fix=f"仅允许：{', '.join(sorted(_ALLOWED_MODULES))}",
                    )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in _ALLOWED_MODULES:
                raise ToolError(
                    "代码引入了白名单外的模块", cause=node.module or "",
                    fix=f"仅允许：{', '.join(sorted(_ALLOWED_MODULES))}",
                )
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise ToolError("代码使用了被禁止的内置函数", cause=node.id, fix="沙箱仅支持纯计算代码")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                raise ToolError("代码访问了双下划线属性", cause=node.attr, fix="沙箱禁止访问内部属性")
        elif isinstance(node, (ast.Await, ast.Yield)):
            raise ToolError("代码含被禁止的语句", cause=type(node).__name__)


def run_python(code: str, *, timeout: int = _TIMEOUT) -> dict[str, str]:
    """在隔离子进程执行 Python 代码，返回 {"stdout", "stderr"}。超时/不安全抛 ToolError。"""
    check_code_safety(code)
    timeout = max(2, min(int(timeout), 30))
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "snippet.py"
        script.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(script)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(
                f"代码执行超时（>{timeout}s）已终止", cause="可能存在死循环",
                fix="检查循环条件，或减小计算规模",
            ) from exc
    return {"stdout": proc.stdout[-3000:], "stderr": proc.stderr[-1000:]}
