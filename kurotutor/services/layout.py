"""版面分析 + 题目裁剪（题集→题库）。

把一整页题目图切成「一道题一张图」。流程：:
    版面识别(返回每行文字 + 坐标) → 题目分组(剔除步骤/答案) → 按组裁图
- :class:`RapidOCRLayoutProvider`：**默认**，免费、本地、无限、纯 CPU（ONNX），零配额零 API 费。
- :class:`BaiduOCRLayoutProvider`：云端百度通用文字识别（每日约 500 次免费），
  或专用切题 paper_cut（一次性 1000 次）。
- :class:`TesseractLayoutProvider`：本地轻量（可选）。
Vendor 由 ``models.layout`` 决定（provider=rapidocr / baidu / tesseract）。
"""

from __future__ import annotations

import base64
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from kurotutor.config.schema import ModelSpec
from kurotutor.core import ProviderError, get_logger, log_event

log = get_logger("layout")

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=20.0, pool=10.0)


@dataclass
class TextLine:
    """一行 OCR 结果：文字 + 像素包围盒 [left, top, right, bottom]。"""

    text: str
    bbox: tuple[int, int, int, int]  # (left, top, right, bottom)


class LayoutProvider(ABC):
    @abstractmethod
    async def layout(self, image_path: str) -> list[TextLine]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class BaiduOCRLayoutProvider(LayoutProvider):
    """百度通用文字识别（含位置）。需 api_key(API Key) + client_secret(Secret Key)，30 天 token。"""

    def __init__(self, spec: ModelSpec):
        self._api_key = spec.api_key or ""
        self._secret = (spec.model_dump().get("client_secret") or spec.model_dump().get("secret_key")) or ""
        if not self._api_key or not self._secret:
            raise ProviderError(
                "百度 OCR 未配置", fix="models.layout 填 api_key(API Key) 与 client_secret(Secret Key)"
            )
        self._token: str | None = None
        self._model = spec.model or "general"
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)

    async def _get_token(self) -> str:
        if self._token:
            return self._token
        resp = await self._client.get(
            "https://aip.baidubce.com/oauth/2.0/token",
            params={
                "grant_type": "client_credentials",
                "client_id": self._api_key,
                "client_secret": self._secret,
            },
        )
        if resp.status_code != 200 or "access_token" not in resp.json():
            raise ProviderError(
                "百度 OCR 鉴权失败",
                cause=f"HTTP {resp.status_code}: {resp.text[:200]}",
                fix="检查 API Key/Secret Key",
            )
        self._token = resp.json()["access_token"]
        return self._token

    async def layout(self, image_path: str) -> list[TextLine]:
        token = await self._get_token()
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")
        # 按模型选端点：accurate≈高精度(含位置)，general≈标准(含位置)；两者都返回 location
        endpoint = "accurate" if "accurate" in self._model.lower() else "general"
        resp = await self._client.post(
            f"https://aip.baidubce.com/rest/2.0/ocr/v1/{endpoint}",
            params={"access_token": token},
            data={"image": b64, "language_type": "CHN_ENG"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise ProviderError(
                "百度 OCR 失败", cause=f"HTTP {resp.status_code}: {resp.text[:200]}", fix="检查图片大小/网络"
            )
        data = resp.json()
        # 百度 OCR 在 HTTP 200 里也可能返回业务错误码（token 失效/额度超限/图片超限等）
        if data.get("error_code"):
            raise ProviderError(
                "百度 OCR 返回错误",
                cause=f"{data.get('error_code')}: {data.get('error_msg')}",
                fix="检查 API/Secret Key 是否正确、是否超免费额度、图片是否合规",
            )
        lines: list[TextLine] = []
        for w in data.get("words_result", []):
            loc = w.get("location") or {}
            left, top = int(loc.get("left", 0)), int(loc.get("top", 0))
            right, bottom = left + int(loc.get("width", 0)), top + int(loc.get("height", 0))
            if w.get("words"):
                lines.append(TextLine(w["words"], (left, top, right, bottom)))
        return lines

    async def aclose(self) -> None:
        await self._client.aclose()


class TesseractLayoutProvider(LayoutProvider):
    """本地轻量 Tesseract（树莓派可跑），可选；需 tesseract 二进制 + 中文语言包。"""

    def __init__(self, spec: ModelSpec):
        try:
            import cv2  # noqa: F401
            import pytesseract  # noqa: F401
        except ImportError as exc:
            raise ProviderError(
                "Tesseract 未安装", fix="pip install pytesseract opencv-python 并安装 tesseract"
            ) from exc
        self._lang = spec.model_dump().get("lang") or "chi_sim+eng"

    async def layout(self, image_path: str) -> list[TextLine]:

        import cv2
        import pytesseract

        img = cv2.imread(image_path)
        if img is None:
            raise ProviderError("无法读取图片", cause=image_path, fix="检查图片路径")
        data = await __import__("asyncio").to_thread(_tess_data, pytesseract, img, self._lang)
        lines: list[TextLine] = []
        for item in data:
            x, y, w, h = int(item["left"]), int(item["top"]), int(item["width"]), int(item["height"])
            text = item["text"].strip()
            if text:
                lines.append(TextLine(text, (x, y, x + w, y + h)))
        return lines


def _tess_data(pytesseract, img, lang) -> list[dict]:
    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
    return [
        {
            "left": data["left"][i],
            "top": data["top"][i],
            "width": data["width"][i],
            "height": data["height"][i],
            "text": data["text"][i],
        }
        for i in range(len(data["text"]))
    ]


# ---- 题目分组（纯逻辑，可测） -------------------------------------------------

# 解答/步骤行的常见开头：这些行属于「答案/解析」，不属于题目内容，裁图时跳过
_ANSWER_PREFIX = (
    "步骤",
    "答案",
    "解析",
    "解法",
    "思路",
    "解：",
    "解:",
    "答：",
    "答:",
    "证明",
    "可知",
    "因此",
    "所以",
    "综上",
    "代入",
    "解得",
    "原式",
    "即 ",
    "注",
    "证明:",
)


def _is_answer_line(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    return any(s.startswith(p) for p in _ANSWER_PREFIX)


def _gap_threshold(lines: list[TextLine], gap_ratio: float) -> int:
    gaps = [abs(lines[i + 1].bbox[1] - lines[i].bbox[3]) for i in range(len(lines) - 1)]
    if not gaps:
        return 12
    srt = sorted(gaps)
    mid = len(srt) // 2
    med = (srt[mid - 1] + srt[mid]) / 2 if len(srt) % 2 == 0 else srt[mid]
    # 上限封顶：题库切题只要题目本体，超过该间距的独立块（答案/提示/页尾）一律不并入
    return min(max(med * gap_ratio, 12), 100)


def group_lines_into_questions(lines: list[TextLine], *, gap_ratio: float = 1.5) -> list[list[TextLine]]:
    """把 OCR 行聚成「题」，**只保留题目本体**：
    - 剔除解答/步骤行（``步骤/答案/解析/解：`` 等开头）；
    - 距当前题「太远」且非题号的行视为独立块丢弃（不放答案/提示/页尾入题）。
    """
    content = [ln for ln in lines if not _is_answer_line(ln.text)]
    if not content:
        return []
    threshold = _gap_threshold(content, gap_ratio)
    # 无题号：纯按垂直间距聚类
    if not any(_is_question_start(ln.text) for ln in content):
        groups: list[list[TextLine]] = []
        current: list[TextLine] = [content[0]]
        for i in range(1, len(content)):
            if content[i].bbox[1] - content[i - 1].bbox[3] > threshold:
                groups.append(current)
                current = [content[i]]
            else:
                current.append(content[i])
        groups.append(current)
        return groups
    # 有题号：题号锚定；非题号行若距当前题太远 → 丢弃
    groups = []
    current: list[TextLine] = []
    for ln in content:
        if _is_question_start(ln.text):
            if current:
                groups.append(current)
            current = [ln]
        elif current and (ln.bbox[1] - current[-1].bbox[3]) <= threshold:
            current.append(ln)
        # else：距当前题太远且非题号 → 视为独立块（答案/提示/页尾），丢弃
    if current:
        groups.append(current)
    return groups


def _is_question_start(text: str) -> bool:
    t = text or ""
    if re.match(r"^\s*[一二三四五六七八九十]+\s*[、.．]", t):
        return True
    return bool(re.match(r"^\s*\d{1,3}\s*[、.．]", t))


def _is_section_header(text: str) -> bool:
    """大题标题（如「三、实验探究题」「二、填空题」）：中文数字 + 顿号 + 短文字 + 「题」结尾。
    这类行不是题目，切割时应并入下一题顶部，不单独成块。"""
    t = (text or "").strip()
    return bool(re.match(r"^[一二三四五六七八九十]{1,3}\s*、\s*.{0,8}题[:：]?$", t))


def plan_question_spans(
    lines: list[TextLine], *, gap: int = 12
) -> tuple[tuple[int, int] | None, list[tuple[int, int]]]:
    """规划一页的切割区间（纯逻辑，可测）。

    返回 ``(残句span, 题目span列表)``，span 为 (top, bottom) 像素：
    - 残句：首个题号之前的内容（上一页题目的延续/跨页头部），有实质内容才返回；
    - 题目：从每个题号行到下一锚点；大题标题（``_is_section_header``）是试卷结构，
      不进任何题目图（前题 bottom 到标题顶、后题从题号行起，标题落在间隙中被丢弃）。
    """
    nums = sorted((ln for ln in lines if _is_question_start(ln.text)), key=lambda x: x.bbox[1])
    if not nums:
        return None, []

    residual: tuple[int, int] | None = None
    first_top = nums[0].bbox[1]
    pre = [ln for ln in lines if ln.bbox[3] <= first_top - 8]
    if pre:
        top = max(0, min(ln.bbox[1] for ln in pre) - 4)
        bottom = max(ln.bbox[3] for ln in pre) + 4
        if bottom - top >= 14:
            residual = (top, bottom)

    content_bottom = max(ln.bbox[3] for ln in lines)
    spans: list[tuple[int, int]] = []
    for k, ln in enumerate(nums):
        if _is_section_header(ln.text):
            continue  # 大题标题是试卷结构，不进任何题目图（落在切割间隙中被丢弃）
        top = max(0, ln.bbox[1] - 6)
        nxt = next((n for n in nums[k + 1 :]), None)
        bottom = (nxt.bbox[1] - gap) if nxt is not None else content_bottom + 6
        if bottom > top:
            spans.append((top, bottom))
    return residual, spans


# ---- 裁剪 ---------------------------------------------------------------------


def crop_questions(image_path: str, groups: list[list[TextLine]], out_dir: str) -> list[str]:
    """按组坐标裁图，返回落盘路径列表。groups 为分组后的行。"""
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for i, group in enumerate(groups, 1):
        left = max(0, min(g.bbox[0] for g in group))
        top = max(0, min(g.bbox[1] for g in group))
        right = min(w, max(g.bbox[2] for g in group))
        bottom = min(h, max(g.bbox[3] for g in group))
        if right <= left + 4 or bottom <= top + 4:
            continue
        img.crop((left, top, right, bottom)).save(out / f"q{i}.png")
        paths.append(str(out / f"q{i}.png"))
    return paths


def cut_blocks_by_ink(
    image_path: str, out_dir: str, *, pad: int = 6, blank_row_scale: float = 0.003
) -> list[str]:
    """按「墨迹投影」把一页切成内容块（题目区域），**图/公式跟着块进来**。

    不依赖 OCR：把图片二值化 → 逐行统计墨迹像素数 → 空行（墨迹极少）作为分块边界
    → 相邻非空行聚成内容块 → 每块裁图（含图）。免费、本地、快。
    """
    import numpy as np
    from PIL import Image as PILImage

    img = PILImage.open(image_path).convert("L")
    arr = np.array(img)
    h, w = arr.shape
    ink = arr < 160
    row_density = ink.sum(axis=1)
    blank = row_density < max(2, w * blank_row_scale)

    # 找内容块：连续非空行；空行间隙 > 一定行数则切块
    content_rows = np.where(~blank)[0]
    if len(content_rows) == 0:
        return []
    blocks: list[tuple[int, int]] = []
    start = content_rows[0]
    prev = content_rows[0]
    gap_rows = max(10, int(h * 0.03))  # 宽容：把题内空隙（如图与题文字）并入同一块
    for r in content_rows[1:]:
        if r - prev > gap_rows:
            blocks.append((start, prev))
            start = r
        prev = r
    blocks.append((start, prev))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for i, (top, bottom) in enumerate(blocks, 1):
        t = max(0, top - pad)
        b = min(h, bottom + pad)
        if b - t < 8:
            continue
        img.crop((0, t, w, b)).save(out / f"q{i}.png")
        paths.append(str(out / f"q{i}.png"))
    return paths


def stitch_crops(image_paths: list[str], out_path: str, *, gap: int = 0) -> str:
    """把多张题图按顺序垂直拼接为一张（跨页题缝合用）。

    宽度对齐到最宽一张（窄图左侧对齐、白底补边），返回输出路径。
    """
    imgs = [Image.open(p).convert("RGB") for p in image_paths]
    if not imgs:
        raise ProviderError("没有可拼接的图片", fix="确认传入的裁片路径均存在")
    w = max(im.width for im in imgs)
    total_h = sum(im.height for im in imgs) + gap * (len(imgs) - 1)
    canvas = Image.new("RGB", (w, total_h), "white")
    y = 0
    for im in imgs:
        canvas.paste(im, (0, y))
        y += im.height + gap
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return out_path


def pdf_to_page_images(pdf_path: str, out_dir: str, *, dpi: int = 200) -> list[str]:
    """把 PDF 每页渲染为 PNG（题集文档入口），返回按页序排列的图片路径列表。"""
    try:
        import pymupdf
    except ImportError as exc:
        raise ProviderError(
            "PDF 解析库未安装", cause="缺少 pymupdf", fix="pip install pymupdf"
        ) from exc
    doc = pymupdf.open(pdf_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    try:
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(dpi=dpi)
            p = out / f"page_{i:02d}.png"
            pix.save(str(p))
            paths.append(str(p))
    finally:
        doc.close()
    return paths


def office_to_pdf(path: str, out_dir: str) -> str:
    """把 Word/PPT 等办公文档经 LibreOffice 无头模式转为 PDF，返回 PDF 路径。

    需要系统安装 LibreOffice（soffice）；未安装时抛出含修复建议的错误。
    """
    import shutil as _shutil
    import subprocess

    soffice = _shutil.which("soffice") or _shutil.which("soffice.exe")
    if not soffice:
        raise ProviderError(
            "Word/PPT 转换需要 LibreOffice",
            cause="未在 PATH 中找到 soffice",
            fix="安装 LibreOffice（apt install libreoffice / 官网下载）后重试；或先手动另存为 PDF",
        )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out), str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    pdf = out / (Path(path).stem + ".pdf")
    if proc.returncode != 0 or not pdf.exists():
        raise ProviderError(
            "LibreOffice 转换失败", cause=proc.stderr[:200] or proc.stdout[:200], fix="检查文档是否加密/损坏"
        )
    return str(pdf)


async def cut_by_question_numbers(image_path: str, out_dir: str, spec: ModelSpec) -> list[str]:
    """按题号锚定切块（含图）：从每题题号裁到下一题题号，整块保留（图/公式一起）。

    用 RapidOCR 找题号行做锚点；文字识别交给视觉模型（本函数只做区域切分）。
    额外处理：首个题号之前的跨页残句单独切为 ``q0_residual.png``（置于首位）；
    大题标题（如「三、实验探究题」）是试卷结构，不进任何题目图。
    """
    try:
        provider = build_layout_provider(spec)
        lines = await provider.layout(image_path)
    except Exception:
        return []
    if not any(_is_question_start(ln.text) for ln in lines):
        return cut_blocks_by_ink(image_path, out_dir)  # 无题号 → 墨迹切块兜底
    residual, spans = plan_question_spans(lines)
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    if residual is not None:
        top, bottom = residual
        img.crop((0, top, w, bottom)).save(out / "q0_residual.png")
        paths.append(str(out / "q0_residual.png"))
    for k, (top, bottom) in enumerate(spans, 1):
        if bottom <= top:
            continue
        img.crop((0, top, w, min(h, bottom))).save(out / f"q{k}.png")
        paths.append(str(out / f"q{k}.png"))
    return paths


async def question_split_cut(spec: ModelSpec, image_path: str, out_dir: str) -> list[str]:
    """用百度「试卷切题识别」paper_cut_edu 直接按题裁图（专用切题 API）。

    返回每题裁剪图路径；识别不到/失败返回空列表（由调用方回退）。
    请求里 ``only_split=true`` 仅返回题目检测框，快且省。
    """
    api_key = spec.api_key or ""
    secret = (spec.model_dump().get("client_secret") or spec.model_dump().get("secret_key")) or ""
    if not api_key or not secret:
        return []
    client = httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        token = await _baidu_token(client, api_key, secret)
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")
        resp = await client.post(
            "https://aip.baidubce.com/rest/2.0/ocr/v1/paper_cut_edu",
            params={"access_token": token},
            data={"image": b64, "language_type": "CHN_ENG", "only_split": "true"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if data.get("error_code"):
            log_event(
                log,
                "paper_cut error",
                level="warning",
                error=f"{data.get('error_code')}: {data.get('error_msg')}",
            )
            return []
    except Exception:
        return []
    finally:
        await client.aclose()

    results = data.get("qus_result") or []
    if not results:
        return []
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for i, q in enumerate(results, 1):
        box = _corners_to_bbox(q.get("qus_location"), w, h)
        if box is None:
            continue
        left, top, right, bottom = box
        if right <= left or bottom <= top:
            continue
        img.crop((left, top, right, bottom)).save(out / f"q{i}.png")
        paths.append(str(out / f"q{i}.png"))
    return paths


def _corners_to_bbox(corners: Any, w: int, h: int) -> tuple[int, int, int, int] | None:
    """四角点 [[x0,y0],[x1,y1],[x2,y2],[x3,y3]]（左上起顺时针）→ (left, top, right, bottom) 裁剪框。"""
    if not isinstance(corners, (list, tuple)) or len(corners) < 4:
        return None
    pts = [(float(c[0]), float(c[1])) for c in corners if isinstance(c, (list, tuple)) and len(c) >= 2]
    if len(pts) < 4:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    left = max(0, int(min(xs)))
    top = max(0, int(min(ys)))
    right = min(w, int(max(xs)))
    bottom = min(h, int(max(ys)))
    return (left, top, right, bottom)


async def _baidu_token(client: httpx.AsyncClient, api_key: str, secret: str) -> str:
    resp = await client.get(
        "https://aip.baidubce.com/oauth/2.0/token",
        params={"grant_type": "client_credentials", "client_id": api_key, "client_secret": secret},
    )
    if resp.status_code != 200 or "access_token" not in resp.json():
        raise ProviderError("百度 OCR 鉴权失败", cause=f"HTTP {resp.status_code}", fix="检查 API/Secret Key")
    return resp.json()["access_token"]


class RapidOCRLayoutProvider(LayoutProvider):
    """RapidOCR（ONNX 版 PaddleOCR）——免费、本地、无限、纯 CPU，树莓派可跑。

    返回每行文字 + 坐标，供 :func:`group_lines_into_questions` 分组切题。零配额、零 API 费。
    """

    def __init__(self, spec: ModelSpec):
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError as exc:
            raise ProviderError(
                "RapidOCR 未安装", cause="缺少 rapidocr_onnxruntime", fix="pip install rapidocr_onnxruntime"
            ) from exc
        self._ocr = None  # 懒加载（首次调用时才建，避免阻塞事件循环）

    async def layout(self, image_path: str) -> list[TextLine]:
        import asyncio

        return await asyncio.to_thread(self._recognize, image_path)

    def _recognize(self, image_path: str) -> list[TextLine]:
        from rapidocr_onnxruntime import RapidOCR

        if self._ocr is None:
            self._ocr = RapidOCR()
        res = self._ocr(image_path)
        items = res[0] if isinstance(res, tuple) else res
        lines: list[TextLine] = []
        for box, text, _score in items:
            t = (text or "").strip()
            if not t:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
            lines.append(TextLine(t, bbox))
        return lines


_PROVIDERS: dict[str, type[LayoutProvider]] = {
    "baidu": BaiduOCRLayoutProvider,
    "baidu-ocr": BaiduOCRLayoutProvider,
    "rapidocr": RapidOCRLayoutProvider,
    "rapidocr_onnxruntime": RapidOCRLayoutProvider,
    "ocr": RapidOCRLayoutProvider,  # 默认给免费、无配额的 RapidOCR
    "tesseract": TesseractLayoutProvider,
}


def build_layout_provider(spec: ModelSpec) -> LayoutProvider:
    cls = _PROVIDERS.get(spec.provider.lower())
    if cls is None:
        raise ProviderError(f"未知的版面分析 Provider：{spec.provider}", fix="用 baidu / tesseract")
    return cls(spec)


def layout_text(groups: list[list[TextLine]]) -> list[str]:
    return [" ".join(g.text for g in group) for group in groups]
