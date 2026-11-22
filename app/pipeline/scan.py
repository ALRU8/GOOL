from __future__ import annotations

import re

import pymupdf

from ..textutil import is_known

GRAPH_WORDS = re.compile(r"график|диаграмм|динамик|зависимост|распределен|схем|карт[аы]|гистограмм|тренд|изменени|соотношени|профил", re.I)
FIG_CAPTION = re.compile(r"(?m)^\s*((?:Рисунок|Рис\.|График|Диаграмма|Схема|Карта)\s*[\d.\-IVX]*\s*[–—.:\-]?[^\n]{0,300})")
TABLE_CAPTION = re.compile(r"(?m)^\s*(Таблица\s*[\d.\-]+[^\n]{0,300})")
_NUMLINE = re.compile(r"^[\d.,:;/%\s\-–—()*+]{1,14}$")


def find_split(page, dpi: int = 45) -> float | None:
    r = page.rect
    if r.height == 0 or r.width / r.height < 1.2:
        return None
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    w, h, s = pix.width, pix.height, pix.samples
    ink = [0] * w
    for y in range(h):
        row = s[y * w : (y + 1) * w]
        for x, v in enumerate(row):
            if v < 140:
                ink[x] += 1
    k = 2
    sm = [sum(ink[max(0, i - k) : i + k + 1]) / (min(w, i + k + 1) - max(0, i - k)) for i in range(w)]
    a, b = int(w * 0.38), int(w * 0.62)
    xm = min(range(a, b), key=lambda i: sm[i])
    left = sm[int(w * 0.05) : a]
    right = sm[b : int(w * 0.95)]
    if not left or not right:
        return None
    ml, mr = sum(left) / len(left), sum(right) / len(right)
    if ml > 0.012 * h and mr > 0.012 * h and sm[xm] <= 0.25 * min(ml, mr) and 0.40 * w < xm < 0.60 * w:
        return xm / w
    return None


def render_parts(page, dpi: int):
    frac = find_split(page)
    r = page.rect
    if frac is None:
        return [page.get_pixmap(dpi=dpi).tobytes("png")], False, None
    xm = r.x0 + r.width * frac
    left = pymupdf.Rect(r.x0, r.y0, xm, r.y1)
    right = pymupdf.Rect(xm, r.y0, r.x1, r.y1)
    return [page.get_pixmap(dpi=dpi, clip=left).tobytes("png"), page.get_pixmap(dpi=dpi, clip=right).tobytes("png")], True, frac


def render_halves_vertical(page, dpi: int, clip=None):
    r = clip or page.rect
    mid = r.y0 + r.height / 2
    top = pymupdf.Rect(r.x0, r.y0, r.x1, mid + 10)
    bot = pymupdf.Rect(r.x0, mid - 10, r.x1, r.y1)
    return [page.get_pixmap(dpi=dpi, clip=top).tobytes("png"), page.get_pixmap(dpi=dpi, clip=bot).tobytes("png")]


def text_quality(text: str) -> tuple[bool, str]:
    t = (text or "").strip()
    if len(t) < 40:
        return False, "пустой или очень короткий текст"
    words = [w.lower().replace("ё", "е") for w in re.findall(r"[А-Яа-яЁё]{4,}", t)]
    if len(words) >= 15:
        known = sum(1 for w in words if is_known(w)) / len(words)
        if known < 0.55:
            return False, f"много нераспознанных слов ({int(known * 100)}% известных)"
    junk = sum(1 for c in t if not (c.isalnum() or c.isspace() or c in ".,;:!?()-–—«»\"'%°/+*=<>[]№&|_"))
    if junk / len(t) > 0.12:
        return False, "много мусорных символов"
    return True, ""


def looks_tabular(text: str) -> bool:
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    if len(lines) < 10:
        return False
    num = sum(1 for l in lines if _NUMLINE.match(l))
    has_cap = bool(TABLE_CAPTION.search(text))
    return num >= 14 or (has_cap and num >= 6) or num / len(lines) > 0.45


def graph_captions(text: str) -> list[str]:
    return [c.strip() for c in FIG_CAPTION.findall(text or "") if GRAPH_WORDS.search(c)]


def continues_table(text: str) -> bool:
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()][:10]
    if len(lines) < 6:
        return False
    if TABLE_CAPTION.search("\n".join(lines)):
        return False
    num = sum(1 for l in lines if _NUMLINE.match(l) or re.match(r"^\d{1,3}\.?\s", l))
    return num >= 5
