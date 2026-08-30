"""OCR 服务：可配置识别链（百度 → 腾讯 → 本地）+ MinerU 文档解析。

- :func:`ocr_image_with_chain` 按配置链逐个尝试，任一成功即返回（附来源）；
- :func:`ocr_pdf_pages` 把 PDF 按页渲染后走识别链；
- :func:`mineru_parse_file` 调 MinerU 官方 API 解析复杂版面（公式/表格/双栏），
  输出 Markdown；轮询直到完成或超时。
- 所有失败都转为带原因的 CloudBackupError 风格异常（OcrError），不崩溃。
"""

from __future__ import annotations

import base64
import io
import json
import time
from pathlib import Path
from typing import Any

import httpx

from kurotutor.core import get_logger

log = get_logger("ocr")


class OcrError(Exception):
    """OCR 失败（含可操作的修复建议）。"""


# ---- 百度（通用文字识别高精度版；每月 1000 次免费） ----------------------------


def _baidu_token(cfg: Any) -> str:
    b = cfg.ocr
    r = httpx.post(
        "https://aip.baidubce.com/oauth/2.0/token",
        params={
            "grant_type": "client_credentials",
            "client_id": b.baidu_api_key,
            "client_secret": b.baidu_secret_key,
        },
        timeout=15,
    )
    if r.status_code != 200 or "access_token" not in r.json():
        raise OcrError(f"百度 OCR 鉴权失败：{r.text[:120]}（检查 ocr.baidu_api_key/secret_key）")
    return r.json()["access_token"]


def ocr_image_baidu(image: bytes, cfg: Any) -> str:
    token = _baidu_token(cfg)
    r = httpx.post(
        "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic",
        params={"access_token": token},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"image": base64.b64encode(image).decode()},
        timeout=30,
    )
    data = r.json()
    if "words_result" not in data:
        raise OcrError(f"百度 OCR 失败：{data.get('error_msg', r.text)[:120]}")
    return "\n".join(w["words"] for w in data["words_result"])


# ---- 腾讯（通用印刷体识别；每月 1000 次免费） ----------------------------------

def ocr_image_tencent(image: bytes, cfg: Any) -> str:
    import hashlib
    import hmac

    b = cfg.ocr
    host = "ocr.tencentcloudapi.com"
    service = "ocr"
    ts = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(ts))
    payload = json.dumps(
        {"ImageBase64": base64.b64encode(image).decode(), "LanguageType": "auto"},
        ensure_ascii=False,
    )
    canonical = (
        "POST\n/\n\n"
        + f"content-type:application/json; charset=utf-8\nhost:{host}\n"
        + "\ncontent-type;host\n"
        + hashlib.sha256(payload.encode()).hexdigest()
    )
    sts = (
        f"TC3-HMAC-SHA256\n{ts}\n{date}/{service}/tc3_request\n"
        + hashlib.sha256(canonical.encode()).hexdigest()
    )

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    signing = _hmac(_hmac(_hmac(f"TC3{b.tencent_secret_key}".encode(), date), service), "tc3_request")
    signature = hmac.new(signing, sts.encode(), hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": "GeneralBasicOCR",
        "X-TC-Version": "2018-11-19",
        "X-TC-Timestamp": str(ts),
        "Authorization": (
            f"TC3-HMAC-SHA256 Credential={b.tencent_secret_id}/{date}/{service}/tc3_request, "
            f"SignedHeaders=content-type;host, Signature={signature}"
        ),
    }
    r = httpx.post(f"https://{host}", content=payload.encode(), headers=headers, timeout=30)
    data = r.json()
    resp = data.get("Response", {})
    if "TextDetections" not in resp:
        raise OcrError(f"腾讯 OCR 失败：{resp.get('Error', r.text)}")
    return "\n".join(item["DetectedText"] for item in resp["TextDetections"])


# ---- 本地（RapidOCR，免费无限） ------------------------------------------------

_LOCAL_OCR = None


def ocr_image_local(image: bytes) -> str:
    import numpy as np

    global _LOCAL_OCR
    if _LOCAL_OCR is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise OcrError(
                "本地 OCR 引擎未安装（rapidocr-onnxruntime）。"
                "可 pip install rapidocr_onnxruntime，或改用百度/腾讯识别"
            ) from exc
        _LOCAL_OCR = RapidOCR()
    arr = np.frombuffer(image, dtype=np.uint8)
    result, _ = _LOCAL_OCR(arr)
    if not result:
        return ""
    return "\n".join(line[1] for line in result)


# ---- 识别链 --------------------------------------------------------------------

_PROVIDERS = {
    "baidu": lambda image, cfg: ocr_image_baidu(image, cfg),
    "tencent": lambda image, cfg: ocr_image_tencent(image, cfg),
    "local": lambda image, cfg: ocr_image_local(image),
}


def ocr_image_with_chain(image: bytes, cfg: Any) -> tuple[str, str]:
    """按配置链识别单张图片。返回 (文本, 使用的引擎)。全链失败抛 OcrError。"""
    chain = list(getattr(cfg.ocr, "chain", None) or ["baidu", "tencent", "local"])
    errors: list[str] = []
    for name in chain:
        fn = _PROVIDERS.get(name.strip().lower())
        if fn is None:
            errors.append(f"未知引擎 {name}")
            continue
        try:
            text = fn(image, cfg)
            if text.strip():
                return text, name
            errors.append(f"{name} 未识别到文字")
        except OcrError as exc:
            errors.append(f"{name}: {exc}")
            log.warning(f"ocr chain {name} failed: {exc}")
    raise OcrError("全部识别引擎都失败了：" + "；".join(errors)[:400])


def ocr_pdf_pages(pdf_path: Path, cfg: Any, *, max_pages: int = 20, dpi: int = 200) -> tuple[str, str]:
    """PDF（含扫描版）按页渲染后走识别链。返回 (全文, 引擎)。"""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    pages = min(len(doc), max_pages)
    texts: list[str] = []
    engine = ""
    for i in range(pages):
        pix = doc[i].get_pixmap(dpi=dpi)
        png = pix.tobytes("png")
        page_text, engine = ocr_image_with_chain(png, cfg)
        texts.append(f"===== 第 {i + 1} 页 =====\n{page_text}")
    return "\n\n".join(texts), engine


# ---- MinerU（复杂版面解析，输出 Markdown；官方 API 每日免费额度） ---------------


def mineru_parse_file(path: Path, cfg: Any, *, timeout_s: int = 300) -> str:
    """上传 PDF/图片给 MinerU 官方 API，轮询到完成后返回 Markdown 文本。"""
    token = (getattr(cfg.ocr, "mineru_token", "") or "").strip()
    if not token:
        raise OcrError(
            "未配置 MinerU 令牌。到 mineru.net 注册生成令牌，"
            "填入 kuro.json 的 ocr.mineru_token"
        )
    headers = {"Authorization": f"Bearer {token}"}
    raw = path.read_bytes()
    with httpx.Client(timeout=60) as client:
        apply_r = client.post(
            "https://mineru.net/api/v4/file-urls/batch",
            headers=headers,
            json={
                "enable_formula": True,
                "enable_table": True,
                "files": [{"name": path.name, "is_ocr": True}],
            },
        )
        apply_data = apply_r.json()
        if apply_r.status_code != 200 or apply_data.get("code") != 20000:
            raise OcrError(f"MinerU 任务申请失败：{str(apply_data)[:160]}")
        batch_id = apply_data["data"]["batch_id"]
        put_url = apply_data["data"]["file_urls"][0]

        put = client.put(put_url, content=raw)
        if put.status_code >= 400:
            raise OcrError(f"MinerU 文件上传失败：HTTP {put.status_code}")

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(6)
            poll = client.get(
                f"https://mineru.net/api/v4/extract-results/batch/{batch_id}", headers=headers
            )
            pdata = poll.json()
            results = (pdata.get("data") or {}).get("extract_result") or []
            if not results:
                continue
            state = results[0].get("state")
            if state == "done":
                zip_url = results[0].get("full_zip_url")
                if not zip_url:
                    raise OcrError("MinerU 完成但未返回结果包")
                zr = client.get(zip_url)
                return _markdown_from_zip(zr.content, path.stem)
            if state == "failed":
                raise OcrError(results[0].get("err_msg") or "MinerU 解析失败")
        raise OcrError("MinerU 解析超时（任务仍在后台进行，可稍后重试）")


def _markdown_from_zip(zip_bytes: bytes, stem: str) -> str:
    from zipfile import ZipFile

    with ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        target = next((n for n in names if n.endswith(".md")), None)
        if target is None:
            raise OcrError(f"MinerU 结果包里没有 Markdown（内容：{names[:8]}）")
        return zf.read(target).decode("utf-8", errors="replace")
