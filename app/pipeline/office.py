from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

PAGE_CHARS = 4200
SHEET_CHUNK = 120

SUPPORTED = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".csv": "csv"}


def kind_of(filename: str) -> str | None:
    return SUPPORTED.get(Path(filename).suffix.lower())


def rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    out = []
    for i, r in enumerate(rows):
        r = [c.replace("|", "/").replace("\n", " ") for c in r] + [""] * (w - len(r))
        out.append("| " + " | ".join(r) + " |")
        if i == 0:
            out.append("|" + "---|" * w)
    return "\n".join(out)


def validate(path: Path, kind: str) -> tuple[bool, str, int]:
    try:
        if path.stat().st_size == 0:
            return False, "Файл пустой (0 байт)", 0
        if kind in ("docx", "xlsx") and not zipfile.is_zipfile(path):
            return False, "Файл повреждён или это не документ нужного формата", 0
        pages = read_pages(path, kind)
        if not pages:
            return False, "В файле не найдено текста или таблиц", 0
        return True, "", len(pages)
    except zipfile.BadZipFile:
        return False, "Файл повреждён или это не документ нужного формата", 0
    except Exception as e:
        return False, f"Не удалось прочитать файл ({str(e)[:80]})", 0


def read_pages(path: Path, kind: str) -> list[dict]:
    if kind == "docx":
        return _docx_pages(path)
    if kind == "xlsx":
        return _xlsx_pages(path)
    if kind == "csv":
        return _csv_pages(path)
    raise ValueError("unsupported kind")


def _docx_pages(path: Path) -> list[dict]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(str(path))
    pages: list[dict] = []
    cur = {"text": [], "rich": [], "tables": [], "chars": 0}
    h1 = h2 = 0

    def flush():
        nonlocal cur
        if cur["chars"] > 0 or cur["tables"]:
            pages.append({"text": "\n".join(cur["text"]).strip(), "rich": "\n\n".join(cur["rich"]).strip(), "tables": cur["tables"]})
        cur = {"text": [], "rich": [], "tables": [], "chars": 0}

    last_caption = ""
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            t = p.text.strip()
            style = (p.style.name if p.style is not None else "") or ""
            m = re.search(r"(?:heading|заголовок)\s*(\d)", style, re.I)
            if t and m:
                lvl = int(m.group(1))
                if not re.match(r"^\d+(\.\d+)*\.?\s", t):
                    if lvl == 1:
                        h1 += 1; h2 = 0
                        t = f"{h1}. {t}"
                    elif lvl == 2:
                        h2 += 1
                        t = f"{max(h1, 1)}.{h2} {t}"
            if t:
                if cur["chars"] + len(t) > PAGE_CHARS and cur["chars"] > 0:
                    flush()
                cur["text"].append(t)
                cur["rich"].append(t)
                cur["chars"] += len(t) + 1
                if re.match(r"^Таблица\s*[\d.\-]+", t):
                    last_caption = t
            if 'w:type="page"' in child.xml or "lastRenderedPageBreak" in child.xml:
                flush()
        elif tag == "tbl":
            tb = Table(child, d)
            rows = [[c.text.strip() for c in r.cells] for r in tb.rows]
            rows = [r for r in rows if any(r)]
            if not rows:
                continue
            md = rows_to_markdown(rows)
            if cur["chars"] + len(md) > PAGE_CHARS and cur["chars"] > 0:
                flush()
            cur["tables"].append({"title": last_caption[:500], "rows": rows})
            cur["rich"].append(md)
            cur["text"].append("\n".join(" ".join(r) for r in rows[:30]))
            cur["chars"] += len(md)
            last_caption = ""
    flush()
    return pages


def _sheet_to_pages(title: str, rows: list[list[str]]) -> list[dict]:
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    while width > 1 and not any((r[width - 1] if len(r) >= width else "").strip() for r in rows):
        width -= 1
    rows = [(r + [""] * width)[:width] for r in rows]
    head, body = rows[0], rows[1:]
    pages = []
    chunks = [body[i : i + SHEET_CHUNK] for i in range(0, max(len(body), 1), SHEET_CHUNK)] or [[]]
    for ci, ch in enumerate(chunks):
        md = rows_to_markdown([head] + ch)
        label = f"Лист: {title}" + (f" (строки {ci * SHEET_CHUNK + 2}-{ci * SHEET_CHUNK + len(ch) + 1})" if len(chunks) > 1 else "")
        pages.append({"text": label + "\n" + md, "rich": label + "\n\n" + md, "tables": [{"title": title, "rows": rows}] if ci == 0 else []})
    return pages


def _xlsx_pages(path: Path) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    pages = []
    for ws in wb.worksheets:
        rows = [["" if v is None else (str(v) if not hasattr(v, "date") else v.strftime("%d.%m.%Y")) for v in r] for r in ws.iter_rows(values_only=True)]
        pages += _sheet_to_pages(ws.title, rows)
    wb.close()
    return pages


def _csv_pages(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), dialect)]
    return _sheet_to_pages(path.stem, rows)
