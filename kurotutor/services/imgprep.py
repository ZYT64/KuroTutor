"""图像预处理：切题与解题入口的鲁棒性层。

处理学生实拍图片的三类问题（产品要求）：
1. **歪斜**：投影剖面法估计倾斜角（纯 numpy，快、无需 OCR），超阈值即旋转矫正；
2. **拍得不正/带背景**：cv2 找页面最大四边形做透视矫正（保守策略：找不到合理页面就不动）；
3. **字迹乱/对比差**：低对比度时自动增强；手写内容交给视觉模型按提示词规则处理。

所有函数失败时返回原图路径（不抛出），保证「预处理永远不阻塞主流程」。
"""

from __future__ import annotations

from pathlib import Path

from kurotutor.core import get_logger, log_event

log = get_logger("imgprep")

_MAX_SIDE = 1800  # 预处理产物上限边长（控制后续 OCR/视觉的体积）


def _load(path: str):
    from PIL import Image

    return Image.open(path).convert("RGB")


def _save(img, path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")
    return path


def _otsu_threshold(gray) -> int:
    """一维 Otsu 二值化阈值（numpy 实现，无外部依赖）。"""
    import numpy as np

    hist, _ = np.histogram(gray, bins=256, range=(0, 256))
    total = hist.sum()
    if total == 0:
        return 128
    levels = np.arange(256)
    w0 = np.cumsum(hist)
    w1 = total - w0
    sum_all = np.dot(levels, hist)
    sum0 = np.cumsum(levels * hist)
    mean0 = np.divide(sum0, w0, out=np.zeros_like(sum0, dtype=float), where=w0 > 0)
    mean1 = np.divide((sum_all - sum0), w1, out=np.zeros_like(sum0, dtype=float), where=w1 > 0)
    variance = w0 * w1 * (mean0 - mean1) ** 2
    return int(levels[int(np.argmax(variance))])


def estimate_skew_angle(img, *, max_abs: float = 7.0) -> float:
    """投影剖面法估计文本行倾斜角（度）。画面近水平时返回 0 附近的小值。"""
    import numpy as np
    from PIL import Image as PILImage

    w0 = 600
    ratio = w0 / img.width
    small = img.convert("L").resize((w0, max(1, int(img.height * ratio))))
    arr = np.asarray(small, dtype=np.uint8)
    th = _otsu_threshold(arr)
    ink = (arr < th).astype(np.float32)

    # 裁掉边缘 8%，避免旋转补边影响投影
    h, w = ink.shape
    ink = ink[int(h * 0.08) : int(h * 0.92), int(w * 0.08) : int(w * 0.92)]
    if ink.sum() < 50:  # 近乎空白，无文本信号
        return 0.0

    def score(angle: float) -> float:
        rotated = PILImage.fromarray((ink * 255).astype(np.uint8)).rotate(
            angle, resample=PILImage.BILINEAR, fillcolor=0
        )
        proj = (np.asarray(rotated) > 0).sum(axis=1).astype(np.float64)
        return float(proj.var())

    best, best_s = 0.0, score(0.0)
    for a in range(-int(max_abs), int(max_abs) + 1):
        if a == 0:
            continue
        s = score(float(a))
        if s > best_s:
            best, best_s = float(a), s
    # 细化 ±1°、步长 0.25
    for fine in [best + d / 4 for d in range(-4, 5) if d != 0]:
        s = score(fine)
        if s > best_s:
            best, best_s = fine, s
    if abs(best) < 0.3:
        return 0.0
    return best


def deskew(img) -> tuple[object, float]:
    """按估计角旋转矫正。返回 (图像, 实际旋转角)。"""
    angle = estimate_skew_angle(img)
    if angle == 0.0:
        return img, 0.0
    from PIL import Image as PILImage

    return img.rotate(angle, resample=PILImage.BICUBIC, fillcolor=(255, 255, 255)), angle


def crop_page_border(img):
    """cv2 找页面最大四边形做透视矫正；找不到合理页面（面积 50%~99%）时原样返回。"""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return img
    try:
        arr = np.asarray(img.convert("RGB"))
        h, w = arr.shape[:2]
        small = cv2.resize(arr, (800, int(800 * h / w)))
        sh, sw = small.shape[:2]
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return img
        page = max(contours, key=cv2.contourArea)
        area_ratio = cv2.contourArea(page) / (sw * sh)
        if not (0.5 <= area_ratio <= 0.995):
            return img
        quad = cv2.approxPolyDP(page, 0.02 * cv2.arcLength(page, True), True)
        if len(quad) != 4:
            return img
        pts = quad.reshape(4, 2).astype(np.float32)
        # 排序：tl tr br bl
        s = pts.sum(axis=1)
        diff = np.diff(pts, axis=1).ravel()
        corners = [pts[np.argmin(s)], pts[np.argmin(diff)], pts[np.argmax(s)], pts[np.argmax(diff)]]
        src = np.array(corners, dtype=np.float32)
        w_dst, h_dst = int(max(np.linalg.norm(src[0] - src[1]), np.linalg.norm(src[3] - src[2]))), int(
            max(np.linalg.norm(src[0] - src[3]), np.linalg.norm(src[1] - src[2]))
        )
        if w_dst < 100 or h_dst < 100:
            return img
        dst = np.array([[0, 0], [w_dst, 0], [w_dst, h_dst], [0, h_dst]], dtype=np.float32)
        warped = cv2.warpPerspective(small, cv2.getPerspectiveTransform(src, dst), (w_dst, h_dst))
        from PIL import Image as PILImage

        return PILImage.fromarray(warped)
    except Exception as exc:
        log_event(log, "page border crop skipped", level="warning", error=repr(exc))
        return img


def _needs_contrast_boost(img) -> bool:
    import numpy as np

    arr = np.asarray(img.convert("L"), dtype=np.float32)
    return float(arr.std()) < 42  # 低对比（灰蒙蒙/曝光不足）才增强


def _downscale(img):
    if max(img.size) <= _MAX_SIDE:
        return img
    ratio = _MAX_SIDE / max(img.size)
    return img.resize((int(img.width * ratio), int(img.height * ratio)))


def preprocess_image(path: str, out_dir: str) -> str:
    """入口：边界透视矫正 → 倾斜矫正 → 低对比增强 → 限边长。返回处理后路径（失败返回原路径）。"""
    src = Path(path)
    if not src.exists():
        return path
    try:
        img = _load(path)
        img = crop_page_border(img)
        img, angle = deskew(img)
        if _needs_contrast_boost(img):
            from PIL import ImageOps

            img = ImageOps.autocontrast(img, cutoff=1)
        img = _downscale(img)
        out = str(Path(out_dir) / f"{src.stem}_prep.png")
        _save(img, out)
        if angle:
            log_event(log, "image deskewed", angle=round(angle, 2))
        return out
    except Exception as exc:
        log_event(log, "preprocess failed, use original", level="warning", error=repr(exc))
        return path


# ---- 切题产物完整性检测 ------------------------------------------------------

# 句子/小题自然结束符：以这些结尾视为完整
_ENDINGS = tuple("。？！；：)）】」"")]、,，")


def assess_text_completeness(text: str) -> str | None:
    """按 OCR 文本判断题目是否疑似被截断。返回问题说明或 None。

    规则（保守，只提示不武断）：末行末尾既无结束符也无填空下划线 → 疑似不完整。
    """
    t = (text or "").strip()
    if not t:
        return None
    if t.rstrip().endswith(("___", "＿＿", "__")):
        return None
    last = t[-1]
    if last in _ENDINGS or last.isdigit() or last.isalpha():
        return None
    return "末行结尾不完整"
