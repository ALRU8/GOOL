from __future__ import annotations

import csv
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .serialize import FLAG_TEXT, rec_dict

COLUMNS = [
    ("Совпадение", lambda d: "упоминание" if d.get("mention") else ""),
    ("Вид (как в тексте)", lambda d: d["species_text"]),
    ("Вид (справочник)", lambda d: d["species_ref"]),
    ("Латинское название", lambda d: d["species_lat"] or d["species_lat_text"]),
    ("Род", lambda d: d["genus"]),
    ("Группа", lambda d: d["group"]),
    ("Фенофаза / событие", lambda d: d["event"]),
    ("Дата (начало)", lambda d: d["date"] or d["date_raw"]),
    ("Дата (конец периода)", lambda d: d["date_end"] or ""),
    ("Точность даты", lambda d: {"day": "день", "range": "диапазон", "decade": "декада", "month": "месяц", "season": "сезон", "year": "год"}.get(d["date_precision"], "")),
    ("Дата в тексте", lambda d: d["date_raw"]),
    ("Год", lambda d: d["year"]),
    ("Топоним", lambda d: d["toponym"]),
    ("Широта", lambda d: d["lat"]),
    ("Долгота", lambda d: d["lon"]),
    ("Местообитание", lambda d: d["habitat"]),
    ("Значение", lambda d: d["value"]),
    ("Единица", lambda d: d["unit"]),
    ("Единица в тексте", lambda d: d["unit_raw"] if d["unit_raw"] != d["unit"] else ""),
    ("Методика, маршрут", lambda d: d["methodology"]),
    ("Тема раздела", lambda d: d["topic"]),
    ("Наблюдатель / автор", lambda d: d["observer"] + (" (автор раздела)" if d["observer_source"] == "section" and d["observer"] else "")),
    ("Примечание", lambda d: d["note"]),
    ("Заметка пользователя", lambda d: d["user_note"]),
    ("Источник: том", lambda d: d["doc_title"]),
    ("Источник: страница PDF", lambda d: d["page"]),
    ("Фрагмент текста", lambda d: d["fragment"]),
    ("Достоверность", lambda d: d["confidence"]),
    ("Предупреждения", lambda d: "; ".join(FLAG_TEXT.get(f, f) for f in d["flags"])),
    ("Проверено", lambda d: "да" if d["verified"] else ""),
    ("Ссылка на источник", lambda d: f"{d['_base']}/#/source/{d['document_id']}/{d['page']}?rec={d['id']}"),
]


def _rows(records, base_url: str, mention_ids=frozenset()):
    for r in records:
        d = rec_dict(r)
        d["_base"] = base_url
        d["mention"] = r.id in mention_ids
        yield [fn(d) for _, fn in COLUMNS]


def to_xlsx(records, base_url: str, mention_ids=frozenset()) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Записи"
    ws.append([c for c, _ in COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F4F2F")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    n = 0
    for row in _rows(records, base_url, mention_ids):
        ws.append(row)
        n += 1
        link = ws.cell(row=n + 1, column=len(COLUMNS))
        link.hyperlink = link.value
        link.font = Font(color="0563C1", underline="single")
    widths = [12, 24, 24, 26, 14, 18, 34, 12, 12, 12, 22, 7, 26, 9, 9, 22, 10, 12, 28, 30, 28, 10, 60, 11, 40, 10, 50]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_csv(records, base_url: str, mention_ids=frozenset()) -> bytes:
    buf = io.StringIO()
    buf.write(chr(0xFEFF))
    w = csv.writer(buf, delimiter=";")
    w.writerow([c for c, _ in COLUMNS])
    for row in _rows(records, base_url, mention_ids):
        w.writerow(row)
    return buf.getvalue().encode("utf-8")
