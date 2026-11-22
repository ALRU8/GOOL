from __future__ import annotations

import re

from ..textutil import norm, search_blob

ELEMENTS_PROMPT = """Это скан страницы научного отчёта («Летопись природы» заповедника). Текст абзацев уже распознан отдельно, поэтому ОБЫЧНЫЙ ТЕКСТ АБЗАЦЕВ НЕ ВЫВОДИ.
Выведи только нетекстовые элементы страницы, в порядке их расположения. Если страница — разворот из двух страниц, обрабатывай левую, затем правую.

Для каждой ТАБЛИЦЫ:
[ТАБЛИЦА] Таблица N. Заголовок (как напечатано; если заголовка нет — кратко опиши содержание)
| шапка | ... |
|---|---|
| строка | ... |
Сохраняй ВСЕ строки и ВСЕ столбцы, значения ровно как напечатано (десятичные точки/запятые, прочерки, «н/д»). Объединённые ячейки шапки раскрой: к названию каждого подстолбца добавь название группы через « / » (например «п. Янискоски / высота снега»). Пустые ячейки оставь пустыми. После таблицы — строка «Примечание: …» только если оно напечатано.

Для каждого РИСУНКА, ГРАФИКА, ДИАГРАММЫ, КАРТЫ, СХЕМЫ, ФОТОГРАФИИ:
[РИСУНОК] подпись так, как напечатана (если подписи нет — «без подписи»)
Тип: график | диаграмма | карта | схема | фотография | другое
Описание: 1–3 предложения: что показано, оси и единицы измерения, основной вывод.
Данные: если значения на графике/диаграмме можно считать — markdown-таблица (подпись/год/категория | значение); если нет — «не считываются».

Не добавляй вступлений и комментариев. Нечитаемое помечай [неразборчиво]. Если на странице нет ни таблиц, ни рисунков — выведи ровно: НЕТ ЭЛЕМЕНТОВ"""

FULL_PROMPT = """Это скан страницы научного отчёта («Летопись природы» заповедника). Перепиши ВСЁ содержимое страницы дословно на русском языке, сохраняя порядок чтения.
1) Обычный текст — как есть, без пересказа и без исправления орфографии; переносы слов в концах строк склей.
2) Таблицы — в виде markdown-таблиц, перед каждой строка «[ТАБЛИЦА] Таблица N. Заголовок». Сохраняй все строки и столбцы, значения как напечатано, объединённые ячейки шапки раскрой (к названию подстолбца добавь название группы через « / »).
3) Рисунки, графики, диаграммы, карты: блок «[РИСУНОК] подпись», затем «Описание: …» и, если значения считываются, markdown-таблица с данными.
4) Если страница — разворот из двух страниц: левая, затем правая, между ними строка «--- следующая страница ---».
5) Без вступлений и комментариев. Нечитаемое помечай [неразборчиво]."""

_BLOCK = re.compile(r"(?m)^\s*\[(ТАБЛИЦА|РИСУНОК)\]\s*")
_TAB_CAP = re.compile(r"Таблица\s*[\d.\-]+", re.I)


def is_empty_answer(text: str) -> bool:
    t = (text or "").strip().strip(".").upper()
    return (not t) or "НЕТ ЭЛЕМЕНТОВ" in t


def _md_rows(lines: list[str]) -> list[list[str]]:
    rows = []
    for ln in lines:
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) or c == "" for c in cells) and any(c for c in cells):
            continue
        rows.append(cells)
    if rows:
        w = max(len(r) for r in rows)
        rows = [r + [""] * (w - len(r)) for r in rows]
    return rows


def classify(title: str, rows: list[list[str]]) -> str:
    s = norm(title + " " + " ".join(rows[0]) if rows else title)
    if re.search(r"метеорол|температур|осадк|снег|снегомер|ветер|влажност|давлен|гидро|погод|климат|облачн|уровень воды|ледов", s):
        return "weather"
    if re.search(r"вид\b|видов|дата|фенофаз|числен|особ|встреч|цвет|плод|урожа|гнезд|прилет|отлет|биотоп|маршрут|учет|учёт", s):
        return "observations"
    return "other"


def parse_blocks(text: str) -> tuple[list[dict], list[dict]]:
    tables, figures = [], []
    if is_empty_answer(text):
        return tables, figures
    marks = list(_BLOCK.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end() : end].strip()
        lines = body.split("\n")
        head = lines[0].strip() if lines else ""
        rest = lines[1:]
        if m.group(1) == "ТАБЛИЦА":
            rows = _md_rows(lines)
            title = head if not head.startswith("|") else ""
            if rows:
                tables.append({"title": title[:500], "rows": rows, "kind": classify(title, rows)})
        else:
            tables_in_fig = _md_rows(rest)
            desc = "\n".join(l for l in rest if l.strip())
            figures.append({"caption": head[:600], "description": desc[:4000], "has_data": bool(tables_in_fig)})
    return tables, figures


def table_search_text(title: str, rows: list[list[str]]) -> str:
    head = " ".join(" ".join(r) for r in rows[:3])
    body = " ".join(r[0] for r in rows[3:200] if r and r[0])
    return search_blob(title, head, body)[:6000]


def rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    out = []
    for i, r in enumerate(rows):
        r = r + [""] * (w - len(r))
        out.append("| " + " | ".join(r) + " |")
        if i == 0:
            out.append("|" + "---|" * w)
    return "\n".join(out)
