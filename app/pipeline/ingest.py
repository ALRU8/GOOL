from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pymupdf
from sqlalchemy import delete, func, select

from .. import yc
from ..config import settings
from ..db import SessionLocal
from ..models import DocTable, Document, Figure, Page, Record, Section, now
from ..storage import storage
from ..textutil import norm, search_blob
from . import species as sp
from . import toponyms as tp
from .build import dedupe, finalize_species, make_record
from .calendar import looks_like_calendar, parse_calendar_page
from .elements import ELEMENTS_PROMPT, FULL_PROMPT, classify, is_empty_answer, parse_blocks, table_search_text
from .llm_extract import extract_chunk, load_examples, pick_examples, worth_llm
from .units import topic_of
from .pdftext import chunk_rich, extract_page, geometry_tables
from .scan import FIG_CAPTION, TABLE_CAPTION, continues_table, graph_captions, looks_tabular, render_parts, text_quality
from .sections import SectionTracker, authors_of_text, printed_page_no

log = logging.getLogger("ingest")
OCR_MIN_CHARS = 80
MARK = "=== ТАБЛИЦЫ И РИСУНКИ (ИИ) ==="
PAGE_SEP = "\n\n--- следующая страница ---\n\n"
METEO = re.compile(r"погод|климат|метео|осадк|температур|гидро|ветер")


def _note_key(r) -> list:
    return [norm(r.fragment or "")[:80], norm(r.event or "")[:25], norm(r.species_text or "")[:20]]


def stash_user_notes(db, doc_id: int, page_no: int | None = None) -> int:
    stmt = select(Record).where(Record.document_id == doc_id, Record.user_note != "")
    if page_no:
        stmt = stmt.where(Record.page_no == page_no)
    rows = db.execute(stmt).scalars().all()
    doc = db.get(Document, doc_id)
    merged = list(doc.stash or [])
    seen = {(s["page"], tuple(s["key"])) for s in merged}
    for r in rows:
        k = (r.page_no, tuple(_note_key(r)))
        if k not in seen:
            merged.append({"page": r.page_no, "key": _note_key(r), "note": r.user_note, "at": r.user_note_at.isoformat() if r.user_note_at else None})
            seen.add(k)
    doc.stash = merged
    db.commit()
    return len(rows)


def restore_user_notes(db, doc_id: int, page_no: int | None = None) -> tuple[int, int]:
    from datetime import datetime

    from rapidfuzz import fuzz

    doc = db.get(Document, doc_id)
    stash = [s for s in (doc.stash or []) if not page_no or s.get("page") == page_no]
    if not stash:
        return 0, 0
    stmt = select(Record).where(Record.document_id == doc_id)
    if page_no:
        stmt = stmt.where(Record.page_no == page_no)
    recs = db.execute(stmt).scalars().all()
    done, lost = 0, 0
    for s in stash:
        same = [r for r in recs if r.page_no == s["page"] and not r.user_note]
        hit = next((r for r in same if _note_key(r) == s["key"]), None)
        if hit is None and s["key"][0]:
            best = max(same, key=lambda r: fuzz.partial_ratio(s["key"][0], norm(r.fragment or "")[:200]), default=None)
            if best is not None and fuzz.partial_ratio(s["key"][0], norm(best.fragment or "")[:200]) >= 85:
                hit = best
        if hit is None:
            lost += 1
            continue
        hit.user_note = s["note"]
        hit.user_note_at = datetime.fromisoformat(s["at"]) if s.get("at") else None
        from .build import refresh_search
        refresh_search(hit)
        done += 1
    doc.stash = [s for s in (doc.stash or []) if page_no and s.get("page") != page_no] if page_no else None
    if lost:
        log.warning("document %s: %s user notes could not be re-attached", doc_id, lost)
    db.commit()
    return done, lost


def detect_year_range(filename: str) -> int | None:
    m = re.search(r"\((\d{4})\s*[-–]\s*(\d{4})\s*год", filename)
    return int(m.group(2)) if m else None


def detect_year_volume(filename: str, first_text: str) -> tuple[int | None, int | None]:
    year = vol = None
    m = re.search(r"\((\d{4})(?:\s*-\s*\d{4})?\s*год", filename)
    if m:
        year = int(m.group(1))
    m = re.match(r"\s*(\d{1,3})[._\s]", filename)
    if m:
        vol = int(m.group(1))
    if year is None:
        m = re.search(r"\b(19[89]\d|20[0-3]\d)\b", filename) or re.search(r"\b(19[89]\d|20[0-3]\d)\s*(?:г\.|год)", first_text)
        if m:
            year = int(m.group(1))
    return year, vol


def _set(doc_id: int, **kw):
    with SessionLocal() as db:
        d = db.get(Document, doc_id)
        for k, v in kw.items():
            setattr(d, k, v)
        db.commit()


def validate_pdf(path: Path) -> tuple[bool, str, int]:
    try:
        if path.stat().st_size == 0:
            return False, "Файл пустой (0 байт)", 0
        doc = pymupdf.open(path)
        n = doc.page_count
        doc.close()
        if n == 0:
            return False, "В PDF нет страниц", 0
        return True, "", n
    except Exception as e:
        return False, f"Файл повреждён или не является PDF ({str(e)[:80]})", 0


def ocr_available() -> bool:
    return settings.ocr_enabled and settings.yc_ready


def ai_available() -> bool:
    return settings.vision_enabled and settings.yc_ready


def vision_page(pngs: list[bytes], mode: str, ctx_png: bytes | None = None) -> tuple[str, bool]:
    prompt = FULL_PROMPT if mode == "full" else ELEMENTS_PROMPT
    parts, trunc = [], False
    for i, png in enumerate(pngs):
        t, tr = yc.vision(png, prompt, ctx_png if i == 0 else None, max_tokens=12000)
        trunc = trunc or tr
        if mode == "elements" and is_empty_answer(t):
            continue
        parts.append(t.strip())
    return (PAGE_SEP if mode == "full" else "\n\n").join(parts), trunc


def apply_ai_result(item: dict, mode: str, answer: str, trunc: bool) -> None:
    if mode == "full":
        item["text"] = answer
        item["source"] = "vision"
        tables, figures = parse_blocks(answer)
    else:
        item["elements"] = answer
        tables, figures = parse_blocks(answer)
    item["tables"] = [dict(t, source="vision") for t in tables]
    item["figures"] = [dict(f, source="vision") for f in figures]
    item["ai"] = True
    item["trunc"] = trunc


def add_caption_figures(item: dict) -> None:
    have = {norm(f["caption"])[:30] for f in item["figures"]}
    for cap in FIG_CAPTION.findall(item["text"] or ""):
        cap = re.sub(r"\s+", " ", cap).strip()
        if len(cap) >= 8 and norm(cap)[:30] not in have:
            item["figures"].append({"caption": cap[:600], "description": "", "source": "caption", "has_data": False})
            have.add(norm(cap)[:30])


def rich_text(item: dict) -> str:
    if item["source"] == "vision":
        return item["text"]
    base = item["text"]
    if item.get("elements"):
        return base + "\n\n" + MARK + "\n" + item["elements"]
    if item["source"] == "text" and item.get("rich_geo"):
        return item["rich_geo"]
    return base


def store_page_objects(db, doc_id: int, item: dict) -> None:
    for t in item["tables"]:
        rows = t["rows"]
        db.add(DocTable(document_id=doc_id, page_no=item["no"], title=t.get("title", "")[:500], kind=t.get("kind") or classify(t.get("title", ""), rows),
                        source=t.get("source", "vision"), n_rows=len(rows), n_cols=max((len(r) for r in rows), default=0), rows=rows,
                        search_text=table_search_text(t.get("title", ""), rows)))
    for f in item["figures"]:
        db.add(Figure(document_id=doc_id, page_no=item["no"], caption=f.get("caption", "")[:600], description=f.get("description", ""),
                      source=f.get("source", "vision"), search_text=search_blob(f.get("caption", ""), f.get("description", ""))[:4000]))


def process_document(doc_id: int, page_limit: int | None = None) -> None:
    try:
        with SessionLocal() as db:
            kind = db.get(Document, doc_id).kind or "pdf"
        if kind == "pdf":
            _process(doc_id, page_limit)
        else:
            _process_office(doc_id, kind)
    except Exception as e:
        log.exception("document %s failed", doc_id)
        _set(doc_id, status="error", error=f"{type(e).__name__}: {e}"[:500], stage="")


def _process(doc_id: int, page_limit: int | None):
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        key, filename = doc.storage_key, doc.filename
    path = storage.local_path(key)
    ok, msg, n = validate_pdf(path)
    if not ok:
        _set(doc_id, status="error", error=msg, stage="")
        return
    if page_limit:
        n = min(n, page_limit)
    _set(doc_id, status="processing", stage="чтение страниц", pages_total=n, pages_done=0, pages_ocr=0, pages_vision=0, error="", records_count=0)

    with SessionLocal() as db:
        stash_user_notes(db, doc_id)
        for model in (Record, Section, DocTable, Figure, Page):
            db.execute(delete(model).where(model.document_id == doc_id))
        db.commit()

    pdf = pymupdf.open(path)
    pool = ThreadPoolExecutor(max_workers=settings.workers)
    pages: list[dict] = []
    ocr_count = ai_count = 0
    try:
        for i in range(n):
            pg = pdf[i]
            layer, rich_geo = extract_page(pg)
            has_layer = len(layer.strip()) >= OCR_MIN_CHARS
            item = {"no": i + 1, "layer": layer, "has_layer": has_layer, "text": layer, "rich_geo": rich_geo if rich_geo != layer else "",
                    "source": "text", "note": "", "pngs": None, "spread": False, "ocr_futs": None, "tables": [], "figures": [],
                    "ai": False, "elements": "", "halves": [], "status": "new", "trunc": False}
            if (not has_layer) or settings.force_ocr:
                if ocr_available():
                    pngs, spread, _ = render_parts(pg, settings.scan_dpi)
                    item.update(pngs=pngs, spread=spread, source="ocr", ocr_futs=[pool.submit(yc.ocr_png, p) for p in pngs])
                elif not has_layer:
                    item.update(source="empty", note="нет текстового слоя, OCR недоступен")
            pages.append(item)
            if (i + 1) % 20 == 0:
                _set(doc_id, pages_done=i + 1)

        _set(doc_id, stage="распознавание текста (OCR)", pages_done=0)
        done = 0
        for item in pages:
            if item["ocr_futs"]:
                try:
                    texts = [f.result()[0].strip() for f in item["ocr_futs"]]
                    item["halves"] = texts
                    item["text"] = PAGE_SEP.join(texts) if len(texts) > 1 else texts[0]
                    ocr_count += 1
                except Exception as e:
                    item["ocr_error"] = True
                    item["note"] = f"OCR: {str(e)[:150]}"
                    item["source"] = "text" if item["has_layer"] else "error"
                    item["text"] = item["layer"] if item["has_layer"] else ""
                done += 1
                if done % 5 == 0:
                    _set(doc_id, pages_done=done, pages_ocr=ocr_count)
        _set(doc_id, pages_ocr=ocr_count)

        jobs = {}
        if ai_available():
            _set(doc_id, stage="ИИ: таблицы, графики, рисунки", pages_done=0)
            budget = settings.vision_max_pages
            for idx, item in enumerate(pages):
                text = item["text"]
                mode = None
                if item.get("ocr_error") or item["source"] in ("error", "empty") or (item["source"] == "ocr" and not text_quality(text)[0]):
                    mode = "full"
                else:
                    tab = bool(item["rich_geo"]) or bool(TABLE_CAPTION.search(text)) or (item["source"] == "ocr" and looks_tabular(text))
                    fig = bool(graph_captions(text))
                    if tab or fig:
                        mode = "elements"
                if not mode:
                    continue
                if len(jobs) >= budget:
                    item["note"] = (item["note"] + " ИИ-зрение не выполнено: лимит страниц документа").strip()
                    item["ai_skipped"] = True
                    continue
                pg = pdf[idx]
                pngs = item["pngs"] or render_parts(pg, settings.scan_dpi)[0]
                item["pngs"] = pngs
                ctx = None
                if mode == "elements" and idx > 0 and continues_table(text):
                    prev = pages[idx - 1]
                    ctx = (prev["pngs"] or render_parts(pdf[idx - 1], settings.scan_dpi)[0])[-1]
                jobs[idx] = (mode, pool.submit(vision_page, pngs, mode, ctx))
            done = 0
            for idx, (mode, fut) in jobs.items():
                item = pages[idx]
                try:
                    answer, trunc = fut.result()
                    apply_ai_result(item, mode, answer, trunc)
                    ai_count += 1
                except Exception as e:
                    item["status"] = "ai_error"
                    item["note"] = (item["note"] + f" ИИ-зрение: {str(e)[:150]}").strip()
                done += 1
                if done % 3 == 0:
                    _set(doc_id, pages_done=done, pages_total=len(jobs), pages_vision=ai_count)
        for idx, item in enumerate(pages):
            if not item["tables"] and item["rich_geo"] and item["source"] == "text":
                for t in geometry_tables(pdf[idx]):
                    item["tables"].append({"title": "", "rows": t["rows"], "kind": classify("", t["rows"]), "source": "geometry"})
            add_caption_figures(item)
            item["pngs"] = None
    finally:
        pool.shutdown(wait=True)
        pdf.close()

    _finish(doc_id, filename, pages, n, ocr_count, ai_count)



def _finish(doc_id: int, filename: str, pages: list[dict], n: int, ocr_count: int, ai_count: int) -> None:
    first_text = "\n".join(p["text"] for p in pages[:3])
    year, vol = detect_year_volume(filename, first_text)
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc.year is None:
            doc.year = year
        if doc.volume is None:
            doc.volume = vol
        if doc.year_to is None:
            doc.year_to = detect_year_range(filename)
        if not doc.title:
            doc.title = (f"Летопись природы, {doc.year}–{doc.year_to} гг." if doc.year_to else f"Летопись природы, {doc.year} г.") if doc.year else filename
        doc.pages_ocr, doc.pages_vision, doc.pages_total = ocr_count, ai_count, n
        db.commit()

    _set(doc_id, stage="определение разделов", pages_done=0, pages_total=n)
    tracker = SectionTracker()
    for p in pages:
        p["section"] = tracker.feed(p["no"], p["text"])
        p["printed"] = "–".join(x for x in (printed_page_no(h) for h in p["halves"]) if x) if p["halves"] else printed_page_no(p["text"])
    spans = tracker.spans
    for s in spans:
        if s["end"] is None:
            s["end"] = n
    sec_rows, sec_authors = [], {}
    for s in spans:
        txt = "\n".join(p["text"] for p in pages[s["start"] - 1 : s["end"]])
        authors = [] if any(k in norm(s["title"]) for k in ("литератур", "содержан")) else authors_of_text(txt)
        sec_authors[s["title"]] = authors
        sec_rows.append(Section(document_id=doc_id, title=s["title"], page_start=s["start"], page_end=s["end"],
                                authors=authors, authors_norm=norm(" ".join(authors))))
    with SessionLocal() as db:
        db.add_all([Page(document_id=doc_id, page_no=p["no"], printed_no=p["printed"][:20], text=p["text"].replace("\x00", ""),
                         rich=rich_text(p).replace("\x00", ""), source=p["source"], section=p["section"][:300], note=p["note"][:300],
                         status=p["status"], spread=p["spread"], ai_vision=p["ai"]) for p in pages])
        db.add_all(sec_rows)
        for p in pages:
            store_page_objects(db, doc_id, p)
        db.commit()

    _set(doc_id, stage="извлечение записей", pages_done=0)
    with SessionLocal() as db:
        sp.index.load(db)
        tp.index.load(db)
    total_records, errors, llm_pages = _extract_records(doc_id, pages, sec_authors)
    if settings.verify_enabled and settings.llm_enabled and settings.yc_ready:
        from .verify import verify_document
        _set(doc_id, stage="проверка записей")
        try:
            verify_document(doc_id)
        except Exception as e:
            log.warning("verify failed for %s: %s", doc_id, e)
    with SessionLocal() as db:
        restore_user_notes(db, doc_id)
        compute_coverage(db, doc_id)
        from ..geocode import catalog_from_records
        catalog_from_records(db)
    notes = []
    if llm_pages:
        notes.append(f"Страниц на LLM: {llm_pages}, ошибок LLM: {errors}")
    if ai_count:
        notes.append(f"ИИ-зрение: страниц {ai_count}")
    if errors > 0:
        notes.append("часть страниц не обработана: откройте «Покрытие» и нажмите «Распознать ИИ»")
    if not settings.yc_ready:
        notes = ["Yandex Cloud не настроен: извлечение выполнено только правилами"]
    with SessionLocal() as db:
        total_records = db.scalar(select(func.count(Record.id)).where(Record.document_id == doc_id)) or 0
    _set(doc_id, status="done", stage="", error="; ".join(notes), records_count=total_records, finished_at=now(), pages_total=n, pages_done=n)


def _process_office(doc_id: int, kind: str) -> None:
    from . import office

    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        key, filename = doc.storage_key, doc.filename
    path = storage.local_path(key)
    ok, msg, n = office.validate(path, kind)
    if not ok:
        _set(doc_id, status="error", error=msg, stage="")
        return
    _set(doc_id, status="processing", stage="чтение файла", pages_total=n, pages_done=0, pages_ocr=0, pages_vision=0, error="", records_count=0)
    with SessionLocal() as db:
        stash_user_notes(db, doc_id)
        for model in (Record, Section, DocTable, Figure, Page):
            db.execute(delete(model).where(model.document_id == doc_id))
        db.commit()
    pages = []
    for i, pg in enumerate(office.read_pages(path, kind)):
        tables = [{"title": t["title"], "rows": t["rows"], "kind": classify(t["title"], t["rows"]), "source": "file"} for t in pg["tables"]]
        pages.append({"no": i + 1, "layer": pg["text"], "has_layer": True, "text": pg["text"], "rich_geo": pg["rich"] if pg["rich"] != pg["text"] else "",
                      "source": "text", "note": "", "pngs": None, "spread": False, "ocr_futs": None, "tables": tables, "figures": [],
                      "ai": False, "elements": "", "halves": [], "status": "new", "trunc": False})
    _finish(doc_id, filename, pages, len(pages), 0, 0)


def authors_for(sec_authors: dict, section_title: str) -> list[str]:
    return sec_authors.get(section_title, [])


def _extract_records(doc_id: int, pages: list[dict], sec_authors: dict) -> tuple[int, int, int]:
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        total, errors = 0, 0
        cal_state, in_calendar = ("", ""), False
        cal_records: list[tuple[int, dict]] = []
        calendar_pages: set[int] = set()
        if doc.year:
            for p in pages:
                low = norm(p["layer"]) + norm(p["section"])
                if not p["has_layer"] or p["source"] == "vision":
                    continue
                if not in_calendar and "календарь природы" not in low:
                    continue
                if looks_like_calendar(p["layer"]):
                    recs, s1, s2, header = parse_calendar_page(p["layer"], doc.year, *cal_state)
                    if len(recs) >= 5 and (header or in_calendar):
                        in_calendar = True
                        cal_state = (s1, s2)
                        calendar_pages.add(p["no"])
                        cal_records += [(p["no"], r) for r in recs]
                        continue
                if in_calendar:
                    break
        for pno, r in cal_records:
            pg = pages[pno - 1]
            raw = {"kind": "phenology", "event": r["event"], "date": r["date_raw"], "fragment": r["fragment"], "extra": r["extra"]}
            rec = make_record(raw, doc, pno, pg["layer"], pg["section"], authors_for(sec_authors, pg["section"]), "rule", page_rich=pg["layer"])
            if rec is None:
                continue
            rec.flags = list(dict.fromkeys(rec.flags + r["flags"]))
            if not r["extra"].get("observed_in_year", True):
                rec.flags.append("not_observed_in_year")
            finalize_species(db, rec, "")
            db.add(rec)
            total += 1
        db.commit()
        _set(doc_id, records_count=total)

        jobs = []
        if settings.llm_enabled and settings.yc_ready:
            for i, p in enumerate(pages):
                if p["no"] in calendar_pages or p["source"] in ("empty", "error") or not p["text"].strip():
                    continue
                rich = rich_text(p)
                if worth_llm(rich, bool(sp.index.find_in_text(rich))):
                    jobs.append(i)

        examples = load_examples(db)

        def run(i: int):
            p = pages[i]
            prev_tail = pages[i - 1]["text"][-1500:] if i > 0 and continues_table(p["text"]) else (pages[i - 1]["text"][-500:] if i > 0 else "")
            ex = pick_examples(examples, topic_of(p["section"]))
            out = []
            for ch in chunk_rich(rich_text(p)):
                out += extract_chunk(doc.year, p["section"], p["no"], ch, prev_tail, examples=ex)
            return i, out

        done = 0
        failed_pages: list[int] = []
        with ThreadPoolExecutor(max_workers=settings.workers) as ex:
            futs = {ex.submit(run, i): i for i in jobs}
            for fut in as_completed(futs):
                i = futs[fut]
                p = pages[i]
                done += 1
                try:
                    _, raws = fut.result()
                except Exception as e:
                    errors += 1
                    failed_pages.append(p["no"])
                    pg = db.execute(select(Page).where(Page.document_id == doc_id, Page.page_no == p["no"])).scalar_one()
                    pg.status, pg.note = "llm_error", f"LLM: {str(e)[:200]}"
                    db.commit()
                    continue
                recs = [make_record(raw, doc, p["no"], p["text"], p["section"], authors_for(sec_authors, p["section"]), "llm", page_rich=rich_text(p)) for raw in raws]
                for rec in dedupe([r for r in recs if r is not None]):
                    finalize_species(db, rec, "")
                    db.add(rec)
                    total += 1
                db.commit()
                if done % 3 == 0 or done == len(jobs):
                    _set(doc_id, pages_done=done, pages_total=len(jobs), records_count=total)
    if failed_pages and ai_available():
        _set(doc_id, stage="ИИ перечитывает проблемные страницы")
        for pno in failed_pages:
            try:
                reanalyze_page_with_ai(doc_id, pno)
                errors -= 1
            except Exception as e:
                log.warning("page %s ai retry failed: %s", pno, e)
    return total, errors, len(jobs)


def compute_coverage(db, doc_id: int) -> None:
    pages = db.execute(select(Page).where(Page.document_id == doc_id)).scalars().all()

    def counts(model):
        return dict(db.execute(select(model.page_no, func.count()).where(model.document_id == doc_id).group_by(model.page_no)).all())

    rc, tc, fc = counts(Record), counts(DocTable), counts(Figure)
    for p in pages:
        p.n_records, p.n_tables, p.n_figures = rc.get(p.page_no, 0), tc.get(p.page_no, 0), fc.get(p.page_no, 0)
        if p.status in ("llm_error", "ai_error") or p.source in ("error", "empty"):
            cov = "failed"
        elif p.n_records:
            cov = "records"
        elif p.n_tables:
            cov = "tables"
        elif p.n_figures:
            cov = "figures"
        elif METEO.search(norm(p.section)):
            cov = "meteo"
        elif len((p.text or "").strip()) < 40:
            cov = "blank"
        else:
            cov = "text"
        p.coverage = cov
    db.commit()


def reanalyze_page_with_ai(doc_id: int, page_no: int) -> dict:
    if not ai_available():
        raise RuntimeError("ИИ-зрение недоступно: не настроен Yandex Cloud или VISION_ENABLED=0")
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        pg = db.execute(select(Page).where(Page.document_id == doc_id, Page.page_no == page_no)).scalar_one_or_none()
        if not doc or not pg:
            raise LookupError("Страница не найдена")
        key, year = doc.storage_key, doc.year
        section = pg.section
        authors = db.execute(select(Section.authors).where(Section.document_id == doc_id, Section.title == section)).scalar() or []
    pdf = pymupdf.open(storage.local_path(key))
    try:
        pngs, spread, _ = render_parts(pdf[page_no - 1], settings.scan_dpi)
    finally:
        pdf.close()
    answer, trunc = vision_page(pngs, "full")
    item = {"no": page_no, "text": answer, "source": "vision", "elements": "", "tables": [], "figures": [], "ai": True, "trunc": trunc}
    apply_ai_result(item, "full", answer, trunc)
    add_caption_figures(item)
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        sp.index.load(db)
        tp.index.load(db)
        stash_user_notes(db, doc_id, page_no)
        for model in (Record, DocTable, Figure):
            db.execute(delete(model).where(model.document_id == doc_id, model.page_no == page_no))
        pg = db.execute(select(Page).where(Page.document_id == doc_id, Page.page_no == page_no)).scalar_one()
        pg.text, pg.rich, pg.source, pg.ai_vision, pg.status, pg.note, pg.spread = answer, answer, "vision", True, "new", "", spread
        store_page_objects(db, doc_id, item)
        n_new = 0
        if settings.llm_enabled and settings.yc_ready and worth_llm(answer, bool(sp.index.find_in_text(answer))):
            raws = []
            for ch in chunk_rich(answer):
                raws += extract_chunk(year, section, page_no, ch, "")
            recs = [make_record(raw, doc, page_no, answer, section, authors, "llm", page_rich=answer) for raw in raws]
            for rec in dedupe([r for r in recs if r is not None]):
                finalize_species(db, rec, "")
                db.add(rec)
                n_new += 1
        db.commit()
        restore_user_notes(db, doc_id, page_no)
        doc.records_count = db.scalar(select(func.count(Record.id)).where(Record.document_id == doc_id))
        db.commit()
        compute_coverage(db, doc_id)
    return {"records": n_new, "tables": len(item["tables"]), "figures": len(item["figures"]), "truncated": trunc}

