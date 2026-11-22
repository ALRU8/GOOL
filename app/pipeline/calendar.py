from __future__ import annotations

import re

from .dates import parse_date

DATE = re.compile(r"^\d{1,2}\.\d{2}(\.\d{2,4})?$")
DASH = re.compile(r"^[—–\-]+$")
SEASONS = {"ФЕНОЛОГИЧЕСКАЯ ЗИМА", "ФЕНОЛОГИЧЕСКАЯ ВЕСНА", "ФЕНОЛОГИЧЕСКОЕ ЛЕТО", "ФЕНОЛОГИЧЕСКАЯ ОСЕНЬ"}
SUBSEASONS = {
    "предзимье", "глубокая зима", "предвесенье (снежная весна)", "первовесенье", "пестрая весна",
    "оживление весны (голая весна)", "зеленая весна", "предлетье", "раннее лето", "разгар лета", "позднее лето",
    "золотая осень", "глубокая осень", "ранняя осень", "первоосенье", "полное лето", "перволетье", "спад лета",
}
HEADER_WORDS = {
    "основные сезонные процессы,", "их индикаторы,", "температурные характеристики", "дата", "наступле-", "ния",
    "средняя", "многол.", "самая", "ранняя", "поздняя", "число", "лет", "наблю-", "дений",
}
_EMPTY = {"—", "-", "–"}


def _is_date(s: str) -> bool:
    return bool(DATE.match(s)) or bool(DASH.match(s))


def looks_like_calendar(text: str) -> bool:
    return sum(1 for l in text.split("\n") if re.fullmatch(r"\d{1,2}\.\d{2}(?:\.\d{2})?", l.strip())) >= 12


def _join(parts: list[str]) -> str:
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        out = (out[:-1] + p) if out.endswith("-") and not out.endswith(" -") else (out + " " + p).strip()
    return re.sub(r"\s+", " ", out)


def parse_calendar_page(text: str, year: int, season: str = "", subseason: str = ""):
    lines = [l.strip() for l in text.split("\n")]
    n = len(lines)
    i = 0
    header_found = False
    for j in range(5, min(n, 60)):
        if lines[j] == "6" and lines[j - 5 : j] == ["1", "2", "3", "4", "5"]:
            i = j + 1
            header_found = True
            break
    else:
        if lines and re.fullmatch(r"\d{1,4}", lines[0] or ""):
            i = 1
    recs, buf = [], []
    while i < n:
        l = lines[i]
        if not l:
            i += 1
            continue
        if l in SEASONS:
            season, buf = l.capitalize(), []
        elif l.lower() in SUBSEASONS and not buf:
            subseason = l
        elif l.lower() in HEADER_WORDS and not buf:
            pass
        elif _is_date(l) and buf:
            cells, j = [], i
            while j < n and len(cells) < 5:
                t = lines[j]
                if t == "":
                    j += 1
                    continue
                if len(cells) < 4 and _is_date(t):
                    cells.append(t)
                elif len(cells) == 4 and re.fullmatch(r"\d{1,2}", t):
                    cells.append(t)
                    j += 1
                    break
                else:
                    break
                j += 1
            name = _join(buf)
            if len(cells) >= 3 and len(re.findall(r"[А-Яа-яЁё]", name)) >= 6:
                recs.append(_make_record(name, cells, year, season, subseason))
            buf = []
            i = j
            continue
        elif re.fullmatch(r"\d{1,4}", l) and not buf:
            pass
        else:
            buf.append(l)
        i += 1
    return recs, season, subseason, header_found


def _make_record(name: str, cells: list[str], year: int, season: str, subseason: str) -> dict:
    cells = cells + [""] * (5 - len(cells))
    d_raw, mean_raw, min_raw, max_raw, yrs = cells[:5]
    flags = []
    d, fl = parse_date(d_raw, year)
    flags += [f for f in fl if f != "year_inferred"]
    if d is None and d_raw not in _EMPTY:
        flags.append("bad_date")
    mn, _ = parse_date(min_raw, None)
    mx, _ = parse_date(max_raw, None)
    if d_raw in _EMPTY:
        season = subseason = ""
    extra = {
        "season": season, "subseason": subseason, "observed_in_year": d_raw not in _EMPTY,
        "mean_date": "" if mean_raw in _EMPTY else mean_raw,
        "earliest": mn.isoformat() if mn else "", "latest": mx.isoformat() if mx else "",
        "years_observed": int(yrs) if yrs.isdigit() else None,
    }
    return {"event": name, "date": d, "date_raw": d_raw, "extra": extra, "flags": flags,
            "fragment": f"{name} | " + " | ".join(c for c in cells if c)}
