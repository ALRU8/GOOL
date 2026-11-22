from __future__ import annotations

import re

import pymupdf


def _clean(s: str) -> str:
    s = (s or "").replace("\xad", "")
    s = re.sub(r"-\n(?=[а-яё])", "", s)
    return s


def _cell(page, bb) -> str:
    r = pymupdf.Rect(bb) + (-0.5, -0.5, 0.5, 0.5)
    t = _clean(page.get_text("text", clip=r))
    return re.sub(r"\s+", " ", t).strip().replace("|", "/")


def _table_matrix(page, table):
    rows = []
    for row in table.rows:
        cells = []
        for c in row.cells:
            cells.append((c, _cell(page, c)) if c else (None, ""))
        rows.append(cells)
    if not rows:
        return []
    ncol = max(len(r) for r in rows)
    for r in rows:
        r.extend([(None, "")] * (ncol - len(r)))
    keep = [j for j in range(ncol) if any(r[j][1] for r in rows)]
    mat = [[r[j][1] for j in keep] for r in rows]
    mat = [r for r in mat if any(r)]
    return mat


def _to_markdown(mat: list[list[str]]) -> str:
    if not mat:
        return ""
    w = max(len(r) for r in mat)
    out = []
    for i, r in enumerate(mat):
        r = r + [""] * (w - len(r))
        out.append("| " + " | ".join(r) + " |")
        if i == 0:
            out.append("|" + "---|" * w)
    return "\n".join(out)


def extract_page(page) -> tuple[str, str]:
    plain = _clean(page.get_text("text"))
    try:
        tabs = page.find_tables().tables
    except Exception:
        tabs = []
    big = [t for t in tabs if t.row_count >= 2 and t.col_count >= 2]
    if not big:
        return plain, plain
    items = []
    boxes = [pymupdf.Rect(t.bbox) for t in big]
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, txt = b[:5]
        if b[6] != 0:
            continue
        center = pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2)
        if any(center in bx for bx in boxes):
            continue
        items.append((y0, _clean(txt).strip()))
    for t in big:
        try:
            md = _to_markdown(_table_matrix(page, t))
        except Exception:
            md = ""
        if md:
            items.append((t.bbox[1], md))
    items.sort(key=lambda x: x[0])
    return plain, "\n\n".join(i[1] for i in items if i[1])


def chunk_rich(rich: str, limit: int = 5500) -> list[str]:
    parts = re.split(r"\n{2,}", rich)
    chunks, cur = [], ""
    def flush():
        nonlocal cur
        if cur.strip():
            chunks.append(cur.strip())
        cur = ""
    for p in parts:
        if len(p) > limit and p.lstrip().startswith("|"):
            lines = p.split("\n")
            head = "\n".join(lines[:2])
            body = lines[2:]
            flush()
            buf = head
            for ln in body:
                if len(buf) + len(ln) > limit:
                    chunks.append(buf)
                    buf = head
                buf += "\n" + ln
            if buf != head:
                chunks.append(buf)
            continue
        while len(p) > limit:
            cut = p.rfind(". ", 0, limit)
            cut = cut + 1 if cut > 500 else limit
            flush() if len(cur) + cut > limit else None
            cur += p[:cut] + "\n\n"
            flush()
            p = p[cut:]
        if len(cur) + len(p) > limit:
            flush()
        cur += p + "\n\n"
    flush()
    return chunks or [rich]


def geometry_tables(page) -> list[dict]:
    out = []
    try:
        tabs = page.find_tables().tables
    except Exception:
        return out
    for t in tabs:
        if t.row_count < 2 or t.col_count < 2:
            continue
        try:
            mat = _table_matrix(page, t)
        except Exception:
            continue
        if len(mat) >= 2:
            out.append({"title": "", "rows": mat})
    return out


def has_big_image(page, min_share: float = 0.12) -> bool:
    try:
        area = page.rect.width * page.rect.height
        for info in page.get_image_info():
            b = pymupdf.Rect(info["bbox"])
            if area and (b.width * b.height) / area >= min_share:
                return True
    except Exception:
        pass
    return False
