from __future__ import annotations

import re
from functools import lru_cache

from ..textutil import _morph

ROMAN = r"(?:I{1,3}|IV|VI{0,3}|IX|XI{0,3}|XIV|XV|XVI{0,3})"
HEADING = re.compile(
    r"^\s*(?:(\d{1,2}(?:\.\d{1,2}){0,2})\.?|(" + ROMAN + r")\.)\s+([А-ЯЁA-Z«\"][^\n]{4,150})$"
)
STOP_SECTIONS = ("список литературы", "литература", "содержание", "оглавление")
_SURN = r"[А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]{2,})?"
NAME1 = re.compile(r"(" + _SURN + r")\s+([А-ЯЁ])\.\s?([А-ЯЁ])\.")
NAME2 = re.compile(r"(?<![А-ЯЁа-яё])([А-ЯЁ])\.\s?([А-ЯЁ])\.\s+(" + _SURN + r")")
BAD_SURN = {"Рисунок", "Таблица", "Приложение", "Раздел", "Гора", "Река", "Озеро", "Остров", "Пос"}
ROLE_LINE = re.compile(r"автор|исполнител|составител|наблюдател|подготовил|выполнил|руководител|ответственн|сотрудник", re.I)


def printed_page_no(text: str) -> str:
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    for cand in lines[:2] + lines[-2:]:
        if re.fullmatch(r"\d{1,4}", cand):
            return cand
    return ""


def find_headings(text: str) -> list[str]:
    out = []
    for ln in (text or "").split("\n"):
        ln = ln.strip()
        if len(ln) > 160 or len(ln) < 6:
            continue
        m = HEADING.match(ln)
        if m:
            num = m.group(1) or m.group(2)
            title = m.group(3).strip()
            letters = re.sub(r"[^А-Яа-яЁёA-Za-z]", "", title)
            if len(letters) < 5 or title.count(" ") > 25:
                continue
            if re.search(r"\d+[.,]\d+\s*(км|м|%|°)", title):
                continue
            if title.rstrip()[-1:] in ".,;" and len(title.split()) >= 4:
                continue
            out.append(f"{num}. {title}")
    return out


@lru_cache(maxsize=50_000)
def is_surname(w: str) -> bool:
    if w in BAD_SURN:
        return False
    parses = _morph.parse(w)
    if any("Surn" in p.tag for p in parses):
        return True
    return not parses[0].is_known


@lru_cache(maxsize=50_000)
def canon_surname(w: str) -> str:
    try:
        parses = [p for p in _morph.parse(w) if "Surn" in p.tag]
        if not parses or any("nomn" in p.tag for p in parses):
            return w
        fem = w.lower().endswith(("ой", "ей", "ую"))
        pick = next((p for p in parses if (p.tag.gender == "femn") == fem), parses[0])
        infl = pick.inflect({"nomn"})
        if infl:
            return "-".join(x.capitalize() for x in infl.word.split("-"))
    except Exception:
        pass
    return w


def names_in(text: str) -> list[str]:
    found = []
    for m in NAME1.finditer(text or ""):
        if is_surname(m.group(1)):
            found.append(f"{canon_surname(m.group(1))} {m.group(2)}. {m.group(3)}.")
    for m in NAME2.finditer(text or ""):
        if is_surname(m.group(3)):
            found.append(f"{canon_surname(m.group(3))} {m.group(1)}. {m.group(2)}.")
    seen, out = set(), []
    for n in found:
        k = n.lower().replace("ё", "е")
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out


def authors_of_text(text: str, head_chars: int = 1500) -> list[str]:
    res = list(names_in(text[:head_chars]))
    for ln in text.split("\n"):
        if ROLE_LINE.search(ln):
            res += names_in(ln)
    seen, out = set(), []
    for n in res:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def norm_observer(s: str) -> str:
    names = names_in(s)
    return "; ".join(names) if names else re.sub(r"\s+", " ", (s or "").strip())


class SectionTracker:

    def __init__(self):
        self.top = ""
        self.sub = ""
        self.top_n = 0
        self.spans: list[dict] = []

    def current(self) -> str:
        return self.sub or self.top

    def feed(self, page_no: int, text: str) -> str:
        head_lines = (text or "").split("\n")[:6]
        first = self.current()
        heads = find_headings(text)
        low = (text or "").lower()
        if len(heads) > 8 or ("содержание" in low[:400] and len(heads) > 3) or ("оглавление" in low[:400] and len(heads) > 3):
            return first
        for h in heads:
            num, _, title = h.partition(". ")
            level = num.count(".") + 1 if re.fullmatch(r"\d+(\.\d+)*", num) else 1
            roman = not re.fullmatch(r"\d+(\.\d+)*", num)
            if roman:
                self._open(h, 1, page_no)
            elif level == 1:
                n = int(num)
                if self.top_n < n <= self.top_n + 3 or (not self.top_n and n <= 3):
                    self.top_n = n
                    self._open(h, 1, page_no)
            elif level >= 2:
                a = int(num.split(".")[0])
                if self.top_n <= a <= self.top_n + 2:
                    if a != self.top_n:
                        self.top_n, self.top = a, ""
                    self._open(h, 2, page_no)
            if any(h.split(". ", 1)[-1][:25] in ln for ln in head_lines):
                first = self.current()
        return first

    def _open(self, title: str, level: int, page_no: int):
        if level == 1:
            self.top, self.sub = title, ""
        else:
            self.sub = title
        for s in self.spans:
            if s["end"] is None and s["level"] >= level:
                s["end"] = page_no
        self.spans.append({"title": title, "level": level, "start": page_no, "end": None})
