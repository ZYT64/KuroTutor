"""函数图像绘制：PIL + numpy 实现（零 matplotlib 依赖，树莓派可跑）。

表达式为安全子集：x 变量 + 四则/幂 + 常用函数（sin/cos/tan/sqrt/abs/log/ln/exp），
用 ast 白名单求值（拒绝属性访问/调用白名单外的名字），杜绝任意代码执行。
"""

from __future__ import annotations

import ast
import math
import re
from pathlib import Path

from kurotutor.core.errors import ToolError

_ALLOWED_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "sqrt": math.sqrt, "abs": abs, "log": math.log10, "ln": math.log,
    "exp": math.exp,
}
_ALLOWED_CONSTS = {"pi": math.pi, "e": math.e}


def _compile_expr(expr: str):
    """把表达式编译为 f(x) 闭包。白名单外成分一律拒绝。"""
    expr = expr.strip()
    expr = re.sub(r"\^", "**", expr)  # 允许 ^ 写幂
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ToolError(
            "表达式语法有误", cause=f"{expr!r}: {exc}", fix="示例：x^2 - 2*x + 1、sin(x)/x"
        ) from exc

    def _eval(node, x: float) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body, x)
        if isinstance(node, ast.BinOp):
            a, b = _eval(node.left, x), _eval(node.right, x)
            op = type(node.op)
            if op is ast.Add:
                return a + b
            if op is ast.Sub:
                return a - b
            if op is ast.Mult:
                return a * b
            if op is ast.Div:
                return a / b
            if op is ast.Pow:
                return a**b
            if op is ast.Mod:
                return a % b
            raise ToolError("表达式含不支持的运算符", cause=op.__name__, fix="支持 + - * / ^ %")
        if isinstance(node, ast.UnaryOp):
            v = _eval(node.operand, x)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Name):
            name = node.id
            if name == "x":
                return x
            if name in _ALLOWED_CONSTS:
                return _ALLOWED_CONSTS[name]
            raise ToolError("表达式含未知变量/常量", cause=name, fix="只允许变量 x 与常量 pi、e")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
                raise ToolError("表达式调用了白名单外的函数", cause=getattr(node.func, "id", "?"),
                                fix=f"允许的函数：{', '.join(_ALLOWED_FUNCS)}")
            if node.keywords:
                raise ToolError("函数调用不支持关键字参数", cause=node.func.id)
            return _ALLOWED_FUNCS[node.func.id](*[_eval(a, x) for a in node.args])
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        raise ToolError(
            "表达式含不支持的成分", cause=type(node).__name__, fix="只用 x、常数、四则/幂与白名单函数"
        )

    return lambda x: _eval(tree, x)


def plot_functions(out_path: str, expressions: list[str], *, x_min: float = -10, x_max: float = 10,
                   title: str = "") -> str:
    """画一张坐标网格图上的多条函数曲线，返回图片路径。

    断点/超界自动断开（如 1/x、tan），不画穿屏竖线。
    """
    import numpy as np
    from PIL import Image, ImageDraw

    if not expressions:
        raise ToolError("没有可绘制的表达式", fix="提供至少一个如 x^2-2*x-3 的表达式")
    fns = [(e, _compile_expr(e)) for e in expressions]

    W, H = 900, 640
    M = 46  # 边距
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    def sx(px):  # 数据 x → 像素
        return M + (px - x_min) / (x_max - x_min) * (W - 2 * M)

    y_lo, y_hi = -8.0, 8.0  # 视窗（自动微调不会做，固定窗口简单可预期）

    def sy(py):  # 数据 y → 像素
        return H / 2 - (py - 0) / (y_hi - y_lo) * (H - 2 * M) * 0.9

    # 网格与坐标轴
    for gx in range(math.ceil(x_min), math.floor(x_max) + 1):
        d.line([(sx(gx), M), (sx(gx), H - M)], fill=(235, 235, 235))
        if gx % 2 == 0:
            d.text((sx(gx) - 6, H - M + 6), str(gx), fill="gray")
    for gy in range(int(y_lo), int(y_hi) + 1):
        d.line([(M, sy(gy)), (W - M, sy(gy))], fill=(235, 235, 235))
        if gy % 2 == 0 and gy != 0:
            d.text((M - 24, sy(gy) - 6), str(gy), fill="gray")
    if x_min < 0 < x_max:
        d.line([(sx(0), M), (sx(0), H - M)], fill="black", width=2)  # y 轴
    d.line([(M, sy(0)), (W - M, sy(0))], fill="black", width=2)  # x 轴

    colors = [(200, 30, 60), (20, 90, 200), (20, 140, 60), (220, 130, 0)]
    legend_y = M + 6
    xs = np.linspace(x_min, x_max, 1200)
    for i, (expr, fn) in enumerate(fns):
        color = colors[i % len(colors)]
        prev = None
        with _safe_span():
            for px in xs:
                try:
                    py = float(fn(float(px)))
                except Exception:
                    prev = None
                    continue
                if not (y_lo * 1.5 <= py <= y_hi * 1.5) or not math.isfinite(py):
                    prev = None
                    continue
                point = (sx(float(px)), sy(py))
                if prev is not None:
                    d.line([prev, point], fill=color, width=3)
                prev = point
        d.rectangle([W - 250, legend_y, W - 236, legend_y + 6], fill=color)
        d.text((W - 230, legend_y - 3), f"y = {expr}", fill="black")
        legend_y += 20

    if title:
        d.text((M, 10), title, fill="black")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    return out_path


class _safe_span:
    """预留：后续若需要抑制 numpy 运行时警告可扩展。"""

    def __enter__(self):
        import warnings

        self._ctx = warnings.catch_warnings()
        self._ctx.__enter__()
        warnings.simplefilter("ignore", RuntimeWarning)
        return self

    def __exit__(self, *a):
        return self._ctx.__exit__(*a)
