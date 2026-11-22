from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time

import httpx

from .config import settings
from .db import SessionLocal
from .models import LlmCache

OCR_URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"
LLM_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

_sem = threading.Semaphore(max(1, settings.workers))


class _RateLimiter:

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_at - now)
            self.next_at = max(now, self.next_at) + self.min_interval
        if delay:
            time.sleep(delay)


_ocr_limiter = _RateLimiter(1.15)
_client = httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0))


class YcError(RuntimeError):
    pass


def _headers() -> dict:
    if not settings.yc_ready:
        raise YcError("YC_API_KEY / YC_FOLDER_ID не заданы")
    return {
        "Authorization": f"Api-Key {settings.yc_api_key}",
        "x-folder-id": settings.yc_folder_id,
        "Content-Type": "application/json",
        "x-data-logging-enabled": "false",
    }


def _cache_get(key: str) -> str | None:
    with SessionLocal() as db:
        row = db.get(LlmCache, key)
        return row.response if row else None


def _cache_put(key: str, kind: str, response: str) -> None:
    with SessionLocal() as db:
        if db.get(LlmCache, key) is None:
            db.add(LlmCache(key=key, kind=kind, response=response))
            db.commit()


def _post(url: str, payload: dict, tries: int = 10, limiter: _RateLimiter | None = None) -> dict:
    delay = 2.0
    last = ""
    for attempt in range(tries):
        try:
            if limiter:
                limiter.wait()
            with _sem:
                r = _client.post(url, headers=_headers(), json=payload)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                raise YcError(last)
        except httpx.HTTPError as e:
            last = f"network: {e}"
        time.sleep(delay)
        delay = min(delay * 2, 30)
    raise YcError(last or "unknown error")


def ocr_png(png: bytes) -> tuple[str, str]:
    key = hashlib.sha256(b"ocr:" + png).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        try:
            j = json.loads(cached)
            return j["t"], j.get("m", "")
        except Exception:
            return cached, ""
    data = _post(OCR_URL, {
        "mimeType": "image/png",
        "languageCodes": ["ru", "en"],
        "model": "page",
        "content": base64.b64encode(png).decode(),
    }, limiter=_ocr_limiter)
    ta = data.get("result", {}).get("textAnnotation", {}) or {}
    text = ta.get("fullText", "") or ""
    md = ta.get("markdown", "") or ""
    _cache_put(key, "ocr", json.dumps({"t": text, "m": md}, ensure_ascii=False))
    return text, md


VISION_URL = "https://llm.api.cloud.yandex.net/v1/chat/completions"


def vision(png: bytes, prompt: str, context_png: bytes | None = None, max_tokens: int = 7000) -> tuple[str, bool]:
    model = settings.vision_model
    h = hashlib.sha256()
    for part in (model.encode(), prompt.encode(), png, context_png or b""):
        h.update(part)
    key = "v:" + h.hexdigest()[:62]
    cached = _cache_get(key)
    if cached is not None:
        try:
            j = json.loads(cached)
            return j["t"], bool(j.get("tr"))
        except Exception:
            return cached, False
    content = [{"type": "text", "text": prompt}]
    if context_png:
        content.append({"type": "text", "text": "Изображение ниже — ПРЕДЫДУЩАЯ страница (только для понимания шапки таблицы, её содержимое не выводи):"})
        content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(context_png).decode()}})
        content.append({"type": "text", "text": "Изображение ниже — ТЕКУЩАЯ страница:"})
    content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}})
    data = _post(VISION_URL, {
        "model": f"gpt://{settings.yc_folder_id}/{model}",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens, "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    })
    ch = (data.get("choices") or [{}])[0]
    text = (ch.get("message") or {}).get("content") or ""
    truncated = ch.get("finish_reason") == "length"
    _cache_put(key, "vision", json.dumps({"t": text, "tr": truncated}, ensure_ascii=False))
    return text, truncated


def llm(prompt: str, system: str, model: str | None = None, max_tokens: int = 4000) -> str:
    model = model or settings.llm_model_extract
    key = hashlib.sha256(json.dumps([model, system, prompt]).encode()).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        return cached
    data = _post(LLM_URL, {
        "modelUri": f"gpt://{settings.yc_folder_id}/{model}",
        "completionOptions": {"stream": False, "temperature": 0, "maxTokens": str(max_tokens)},
        "messages": [{"role": "system", "text": system}, {"role": "user", "text": prompt}],
    })
    alts = data.get("result", {}).get("alternatives", [])
    if not alts:
        raise YcError("empty LLM response")
    text = alts[0]["message"]["text"]
    _cache_put(key, "llm", text)
    return text


def _salvage_objects(text: str):
    start = text.find("[")
    if start < 0:
        return []
    out, depth, in_str, esc_, obj_start = [], 0, False, False, None
    for i in range(start + 1, len(text)):
        c = text[i]
        if in_str:
            if esc_:
                esc_ = False
            elif c == "\\":
                esc_ = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    out.append(json.loads(text[obj_start : i + 1]))
                except json.JSONDecodeError:
                    pass
                obj_start = None
    return out


def parse_json_block(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("["), text.find("{")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no json")
    text = text[start:]
    for end in range(len(text), 0, -1):
        if text[end - 1] in "]}":
            try:
                return json.loads(text[:end])
            except json.JSONDecodeError:
                continue
    salvaged = _salvage_objects(text)
    if salvaged:
        return salvaged
    raise ValueError("bad json")
