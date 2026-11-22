from __future__ import annotations

import calendar
import datetime as dt
import re

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7,
    "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_MONTH_STEMS = [
    (r"январ", 1), (r"феврал", 2), (r"март", 3), (r"апрел", 4), (r"ма[йяе](?![а-я])", 5), (r"июн", 6), (r"июл", 7),
    (r"август", 8), (r"сентябр", 9), (r"октябр", 10), (r"ноябр", 11), (r"декабр", 12),
]
_MONTH_RX = "(?:" + "|".join(p for p, _ in _MONTH_STEMS) + ")"
_NUM = re.compile(r"(?<!\d)(\d{1,2})[.,](\d{1,2})(?:[.,](\d{4}|\d{2}))?(?!\d)")
_NUM_RANGE = re.compile(r"(?<!\d)(\d{1,2})[.,](\d{1,2})(?:[.,](\d{4}|\d{2}))?\s*[-–—]\s*(\d{1,2})[.,](\d{1,2})(?:[.,](\d{4}|\d{2}))?(?!\d)")
_TXT_RANGE = re.compile(r"(?<!\d)(\d{1,2})\s*[-–—]\s*(\d{1,2})\s+(" + "|".join(MONTHS) + r")", re.I)
_TXT = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4})\s*г)?", re.I)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_PART = re.compile(r"(начал\w*|конец|конц\w*|середин\w*|перв\w+\s+декад\w*|втор\w+\s+декад\w*|трет\w+\s+декад\w*)", re.I)
_SEASONS = [(r"зим", 12, 2), (r"весн", 3, 5), (r"лет[ао]м?|лета|лето", 6, 8), (r"осен", 9, 11)]


def _year(y, default):
    if not y:
        return default
    n = int(y)
    if n < 100:
        n += 2000 if n <= 40 else 1900
    return n


def _mk(y, m, d):
    if y is None:
        return None
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def _last_day(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def _part_days(word: str, y: int, m: int) -> tuple[int, int]:
    w = word.lower()
    last = _last_day(y, m)
    if w.startswith("начал") or w.startswith("перв"):
        return 1, 10
    if w.startswith("серед") or w.startswith("втор"):
        return 11, 20
    return 21, last


def parse_fuzzy(raw: str, default_year: int | None = None) -> dict:
    s = (raw or "").strip()
    res = {"date": None, "date_end": None, "precision": "", "flags": []}
    if not s or re.fullmatch(r"[-—–]+", s):
        return res
    low = s.lower().replace("ё", "е")

    m = _NUM_RANGE.search(low)
    if m:
        y1 = _year(m.group(3), default_year)
        y2 = _year(m.group(6), y1)
        if m.group(3) and m.group(6) and y1 != y2:
            res["flags"].append("multi_year_dates")
            return res
        d1 = _mk(y1, int(m.group(2)), int(m.group(1)))
        d2 = _mk(y2, int(m.group(5)), int(m.group(4)))
        if d1 and d2:
            if d2 < d1 and not m.group(6):
                d2 = _mk(d2.year + 1, d2.month, d2.day)
            res.update(date=d1, date_end=d2, precision="range")
            if not (m.group(3) or m.group(6)):
                res["flags"].append("year_inferred")
            return res
    m = _NUM.search(low)
    if m:
        dd, mm, yy = int(m.group(1)), int(m.group(2)), m.group(3)
        d = _mk(_year(yy, default_year), mm, dd)
        if not yy:
            res["flags"].append("year_inferred")
        if d is None:
            res["flags"].append("bad_date")
            return res
        res.update(date=d, precision="day")
        return res
    m = _TXT_RANGE.search(low)
    if m:
        mm = MONTHS[m.group(3)]
        y = _year(_YEAR.search(low).group(1) if _YEAR.search(low) else None, default_year)
        d1, d2 = _mk(y, mm, int(m.group(1))), _mk(y, mm, int(m.group(2)))
        if not _YEAR.search(low):
            res["flags"].append("year_inferred")
        if d1 and d2:
            res.update(date=d1, date_end=d2, precision="range")
            return res
    m = _TXT.search(low)
    if m:
        mm = MONTHS[m.group(2)]
        yy = m.group(3)
        d = _mk(_year(yy, default_year), mm, int(m.group(1)))
        if not yy:
            res["flags"].append("year_inferred")
        if d is None:
            res["flags"].append("bad_date")
            return res
        res.update(date=d, precision="day")
        return res

    ym = _YEAR.search(low)
    y = int(ym.group(1)) if ym else default_year
    if not ym:
        res["flags"].append("year_inferred")

    mm = None
    for pat, num in _MONTH_STEMS:
        if re.search(pat, low):
            mm = num
            break
    if mm and y:
        parts = _PART.findall(low)
        if parts:
            ranges = [_part_days(p, y, mm) for p in parts]
            d1 = _mk(y, mm, min(a for a, _ in ranges))
            d2 = _mk(y, mm, max(b for _, b in ranges))
            res.update(date=d1, date_end=d2, precision="decade" if len(parts) == 1 else "range")
        else:
            res.update(date=_mk(y, mm, 1), date_end=_mk(y, mm, _last_day(y, mm)), precision="month")
        return res
    yr = re.search(r"(?<!\d)((?:19|20)\d{2})\s*[-–—]\s*((?:19|20)\d{2})(?!\d)", low)
    if yr and yr.group(1) != yr.group(2):
        res.update(date=_mk(int(yr.group(1)), 1, 1), date_end=_mk(int(yr.group(2)), 12, 31), precision="range")
        res["flags"] = [f for f in res["flags"] if f != "year_inferred"] + ["multi_year_dates"]
        return res
    if y:
        for pat, a, b in _SEASONS:
            if re.search(r"(?<![а-я])(?:" + pat + r")", low):
                y2 = y + 1 if a > b else y
                res.update(date=_mk(y, a, 1), date_end=_mk(y2, b, _last_day(y2, b)), precision="season")
                return res
    if ym:
        res.update(date=_mk(y, 1, 1), date_end=_mk(y, 12, 31), precision="year")
        return res
    res["flags"].append("unparsed_date")
    return res


def parse_date(raw: str, default_year: int | None = None):
    r = parse_fuzzy(raw, default_year)
    return r["date"], r["flags"]


def day_of_year(d: dt.date | None) -> int | None:
    if d is None:
        return None
    return dt.date(2001, d.month, min(d.day, 28) if d.month == 2 else d.day).timetuple().tm_yday


def parse_md(s: str) -> int | None:
    m = re.fullmatch(r"\s*(\d{1,2})[.,/-](\d{1,2})\s*", s or "")
    if not m:
        return None
    try:
        return day_of_year(dt.date(2001, int(m.group(2)), int(m.group(1))))
    except ValueError:
        return None
