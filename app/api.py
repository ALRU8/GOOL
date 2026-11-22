from __future__ import annotations

import hashlib
import io
import re
import tempfile
from pathlib import Path

import pymupdf
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import export as exp
from . import search as srch
from .config import settings
from .db import get_db
from .models import now, DocTable, Document, Figure, Page, Record, SavedQuery, Species, SpeciesSynonym, Toponym
from .pipeline import species as sp
from .pipeline import toponyms as tp
from .pipeline.build import refresh_search
from .pipeline.dates import parse_fuzzy
from .pipeline import office
from .pipeline.ingest import ai_available, detect_year_volume, reanalyze_page_with_ai, validate_pdf
from .serialize import doc_dict, rec_dict
from .storage import storage
from .textutil import norm

router = APIRouter(prefix="/api")
FILTER_KEYS = ["species", "q", "phenophase", "toponym", "observer", "group", "kind", "year_from", "year_to", "doc_id",
               "min_conf", "verified", "method", "sort", "order", "topic", "unit", "has_value", "methodology", "md_from", "md_to", "has_note", "only_species"]


def _filters(request: Request) -> dict:
    return {k: v for k, v in request.query_params.items() if k in FILTER_KEYS and v != ""}


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/config")
def config():
    return {"yandex_configured": settings.yc_ready, "ocr_enabled": settings.ocr_enabled and settings.yc_ready,
            "llm_enabled": settings.llm_enabled and settings.yc_ready, "model": settings.llm_model_extract,
            "storage": settings.storage_backend, "library": settings.library_dir.exists()}


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return {
        "documents": db.scalar(select(func.count(Document.id))),
        "documents_done": db.scalar(select(func.count(Document.id)).where(Document.status == "done")),
        "records": db.scalar(select(func.count(Record.id))),
        "verified": db.scalar(select(func.count(Record.id)).where(Record.verified.is_(True))),
        "species": db.scalar(select(func.count(Species.id))),
        "years": [r[0] for r in db.execute(select(Record.year).where(Record.year.is_not(None)).distinct().order_by(Record.year))],
        "groups": [r[0] for r in db.execute(select(Record.group_name).where(Record.group_name != "").distinct().order_by(Record.group_name))],
        "by_kind": dict(db.execute(select(Record.kind, func.count()).group_by(Record.kind)).all()),
        **srch.facets(db),
    }


def _register(db: Session, src: Path, filename: str, year: int | None) -> Document:
    h = hashlib.sha256()
    with open(src, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    existing = db.execute(select(Document).where(Document.sha256 == digest)).scalars().first()
    if existing:
        return existing
    y, vol = detect_year_volume(filename, "")
    kind = office.kind_of(filename)
    ext = Path(filename).suffix.lower() if kind else ".bin"
    doc = Document(filename=filename, year=year or y, volume=vol, sha256=digest, storage_key=f"docs/{digest[:16]}{ext}", kind=kind or "pdf",
                   title=(f"Летопись природы, {year or y} г." if (year or y) else filename) if kind in (None, "pdf") else filename)
    if kind is None:
        old = Path(filename).suffix.lower()
        doc.status = "error"
        doc.error = ("Старый формат " + old + " не поддерживается: сохраните файл как .docx или .xlsx" if old in (".doc", ".xls")
                     else "Неподдерживаемый формат: принимаются PDF, Word (.docx), Excel (.xlsx) и CSV")
    else:
        ok, msg, n = validate_pdf(src) if kind == "pdf" else office.validate(src, kind)
        if not ok:
            doc.status, doc.error = "error", msg
        else:
            doc.pages_total = n
    if doc.status != "error":
        storage.put_file(doc.storage_key, src)
    db.add(doc)
    db.commit()
    return doc


@router.get("/documents")
def documents(db: Session = Depends(get_db)):
    rows = db.execute(select(Document).order_by(Document.year.nulls_last(), Document.id)).scalars().all()
    return [doc_dict(d) for d in rows]


@router.post("/documents")
async def upload(files: list[UploadFile] = File(...), year: int | None = Form(None), db: Session = Depends(get_db)):
    out = []
    limit = settings.max_upload_mb * 1024 * 1024
    for uf in files:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            size = 0
            while chunk := await uf.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    break
                tmp.write(chunk)
        path = Path(tmp.name)
        try:
            if size > limit:
                out.append({"filename": uf.filename, "status": "error", "error": f"Файл больше {settings.max_upload_mb} МБ"})
                continue
            out.append(doc_dict(_register(db, path, uf.filename or "document.pdf", year if len(files) == 1 else None)))
        finally:
            path.unlink(missing_ok=True)
    return out


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Документ не найден")
    if d.status == "processing":
        raise HTTPException(409, "Документ обрабатывается, дождитесь окончания")
    key = d.storage_key
    db.execute(delete(Record).where(Record.document_id == doc_id))
    db.delete(d)
    db.commit()
    if not db.scalar(select(func.count(Document.id)).where(Document.storage_key == key)):
        try:
            storage.delete(key)
        except Exception:
            pass
    return {"ok": True}


@router.post("/documents/{doc_id}/reprocess")
def reprocess(doc_id: int, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Документ не найден")
    if d.status == "processing":
        raise HTTPException(409, "Документ уже обрабатывается")
    p = storage.local_path(d.storage_key)
    ok, msg, _ = validate_pdf(p) if (d.kind or "pdf") == "pdf" else office.validate(p, d.kind)
    if not ok:
        d.status, d.error = "error", msg
    else:
        d.status, d.error, d.stage = "queued", "", ""
    db.commit()
    return doc_dict(d)


@router.get("/documents/{doc_id}/file")
def document_file(doc_id: int, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404)
    mt = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv"}.get(d.kind or "pdf", "application/octet-stream")
    return FileResponse(storage.local_path(d.storage_key), media_type=mt, filename=d.filename,
                        content_disposition_type="inline" if (d.kind or "pdf") == "pdf" else "attachment")


@router.get("/documents/{doc_id}/pages/{page_no}")
def page_info(doc_id: int, page_no: int, db: Session = Depends(get_db)):
    pg = db.execute(select(Page).where(Page.document_id == doc_id, Page.page_no == page_no)).scalar_one_or_none()
    d = db.get(Document, doc_id)
    if not pg or not d:
        raise HTTPException(404, "Страница не найдена")
    recs = db.execute(select(Record).where(Record.document_id == doc_id, Record.page_no == page_no).order_by(Record.id)).scalars().all()
    tabs = db.execute(select(DocTable).where(DocTable.document_id == doc_id, DocTable.page_no == page_no).order_by(DocTable.id)).scalars().all()
    figs = db.execute(select(Figure).where(Figure.document_id == doc_id, Figure.page_no == page_no).order_by(Figure.id)).scalars().all()
    return {"document": doc_dict(d), "page": page_no, "printed_no": pg.printed_no, "text": pg.text, "rich": pg.rich, "source": pg.source,
            "section": pg.section, "note": pg.note, "pages_total": d.pages_total, "coverage": pg.coverage, "ai_vision": pg.ai_vision,
            "spread": pg.spread, "ai_available": ai_available(), "records": [rec_dict(r, d) for r in recs],
            "tables": [table_dict(t, d, rows=True) for t in tabs], "figures": [figure_dict(f, d) for f in figs]}


def _highlight_rects(page, fragment: str):
    words = re.findall(r"\S+", fragment or "")
    rects = []
    for i in range(0, len(words), 4):
        piece = " ".join(words[i : i + 4])
        if len(piece) < 4:
            continue
        try:
            rects += page.search_for(piece)
        except Exception:
            pass
    return rects


@router.get("/documents/{doc_id}/pages/{page_no}/image")
def page_image(doc_id: int, page_no: int, hl: str = "", scale: float = 1.6, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404)
    if (d.kind or "pdf") != "pdf":
        raise HTTPException(404, "Для этого формата нет изображения страницы: текст смотрите в режиме «Показать текст»")
    pdf = pymupdf.open(storage.local_path(d.storage_key))
    try:
        if not (1 <= page_no <= pdf.page_count):
            raise HTTPException(404, "Нет такой страницы")
        page = pdf[page_no - 1]
        shape = page.new_shape()
        for r in _highlight_rects(page, hl):
            shape.draw_rect(r)
        shape.finish(color=None, fill=(1, 0.85, 0.1), fill_opacity=0.45)
        shape.commit()
        s = min(max(scale, 0.5), 3)
        png = page.get_pixmap(matrix=pymupdf.Matrix(s, s)).tobytes("png")
    finally:
        pdf.close()
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.get("/library")
def library(db: Session = Depends(get_db)):
    root = settings.library_dir
    if not root.exists():
        return []
    imported = {d.filename for d in db.execute(select(Document)).scalars()}
    items = []
    for p in sorted(root.glob("*.pdf"), key=lambda x: (int(re.match(r"(\d+)", x.name).group(1)) if re.match(r"(\d+)", x.name) else 999, x.name)):
        size = p.stat().st_size
        if size == 0:
            continue
        y, vol = detect_year_volume(p.name, "")
        items.append({"filename": p.name, "size_mb": round(size / 1e6, 1), "year": y, "volume": vol,
                      "imported": p.name in imported, "broken": size == 0})
    return items


class ImportBody(BaseModel):
    files: list[str]


@router.post("/library/import")
def library_import(body: ImportBody, db: Session = Depends(get_db)):
    out = []
    root = settings.library_dir.resolve()
    for name in body.files:
        p = (root / name).resolve()
        if root not in p.parents or not p.is_file():
            out.append({"filename": name, "status": "error", "error": "Файл не найден в папке библиотеки"})
            continue
        out.append(doc_dict(_register(db, p, p.name, None)))
    return out


@router.get("/records")
def records(request: Request, page: int = 1, page_size: int = Query(50, le=200), db: Session = Depends(get_db)):
    f = _filters(request)
    total, rows = srch.search(db, f, page, page_size)
    docs = {d.id: d for d in db.execute(select(Document).where(Document.id.in_({r.document_id for r in rows}))).scalars()} if rows else {}
    out = {"total": total, "page": page, "page_size": page_size, "items": [rec_dict(r, docs.get(r.document_id)) for r in rows]}
    if f.get("observer"):
        out["sections"] = srch.authors(db, f["observer"])
    if f.get("species"):
        ids = sp.index.expand_query(f["species"])
        for item, r in zip(out["items"], rows):
            item["mention"] = not srch.is_species_match(r, f["species"], ids)
        out["mentions_total"] = srch.mentions_count(db, f, total)
    return out


@router.get("/records/export")
def records_export(request: Request, format: str = "xlsx", db: Session = Depends(get_db)):
    f = _filters(request)
    base = str(request.base_url).rstrip("/")
    recs = list(srch.iter_all(db, f))
    for r in recs:
        _ = r.document
    mention_ids: set[int] = set()
    if f.get("species"):
        ids = sp.index.expand_query(f["species"])
        mention_ids = {r.id for r in recs if not srch.is_species_match(r, f["species"], ids)}
    if format == "csv":
        data, mt, ext = exp.to_csv(recs, base, mention_ids), "text/csv; charset=utf-8", "csv"
    else:
        data, mt, ext = exp.to_xlsx(recs, base, mention_ids), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    return Response(data, media_type=mt, headers={"Content-Disposition": f"attachment; filename=gool.{ext}"})


class RecommendBody(BaseModel):
    history: list[dict[str, str]] = []


@router.post("/recommendations")
def recommendations(body: RecommendBody, db: Session = Depends(get_db)):
    from .recommend import recommend
    return recommend(db, body.history[:5])


@router.get("/records/{rec_id}")
def record(rec_id: int, db: Session = Depends(get_db)):
    r = db.get(Record, rec_id)
    if not r:
        raise HTTPException(404, "Запись не найдена")
    return rec_dict(r)


class RecordPatch(BaseModel):
    species_text: str | None = None
    species_lat_text: str | None = None
    event: str | None = None
    date: str | None = None
    toponym: str | None = None
    habitat: str | None = None
    value: float | None = None
    unit: str | None = None
    observer: str | None = None
    note: str | None = None
    group: str | None = None
    verified: bool | None = None
    user_note: str | None = None


@router.patch("/records/{rec_id}")
def patch_record(rec_id: int, body: RecordPatch, db: Session = Depends(get_db)):
    r = db.get(Record, rec_id)
    if not r:
        raise HTTPException(404, "Запись не найдена")
    data = body.model_dump(exclude_unset=True)
    if "species_text" in data or "species_lat_text" in data:
        r.species_text = data.get("species_text", r.species_text) or ""
        r.species_lat_text = data.get("species_lat_text", r.species_lat_text) or ""
        sid, _ = sp.index.resolve(r.species_text, r.species_lat_text)
        r.species_id = sid
        if sid:
            r.group_name = sp.index.meta[sid][3] or r.group_name
    if "event" in data:
        r.event = data["event"] or ""
    if "date" in data:
        r.date_raw = data["date"] or ""
        pf = parse_fuzzy(r.date_raw, r.year)
        r.date, r.date_end, r.date_precision = pf["date"], pf["date_end"], pf["precision"]
        if r.date:
            r.year = r.date.year
    if "toponym" in data:
        r.toponym_text = data["toponym"] or ""
        r.toponym_id = tp.index.resolve(r.toponym_text) if r.toponym_text else None
        if r.toponym_id:
            _, r.lat, r.lon = tp.index.meta[r.toponym_id]
    for k in ("habitat", "unit", "note"):
        if k in data:
            setattr(r, k, data[k] or "")
    if "value" in data:
        r.value = data["value"]
        r.value_raw = "" if data["value"] is None else str(data["value"])
    if "observer" in data:
        r.observer, r.observer_source = data["observer"] or "", ("text" if data["observer"] else "")
    if "group" in data:
        r.group_name = data["group"] or ""
    if "user_note" in data:
        r.user_note = (data["user_note"] or "").strip()[:4000]
        r.user_note_at = now() if r.user_note else None
    edited = bool(set(data) - {"verified", "user_note"})
    if edited:
        r.method = "manual"
        r.verified = data.get("verified", True)
        r.confidence = max(r.confidence, 0.95)
        r.flags = []
    elif "verified" in data:
        r.verified = bool(data["verified"])
    refresh_search(r)
    db.commit()
    return rec_dict(r)


@router.delete("/records/{rec_id}")
def delete_record(rec_id: int, db: Session = Depends(get_db)):
    r = db.get(Record, rec_id)
    if not r:
        raise HTTPException(404)
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.get("/phenology")
def phenology(request: Request, db: Session = Depends(get_db)):
    return {"series": srch.phenology_series(db, _filters(request))}


@router.get("/places/species")
def places_species(request: Request, db: Session = Depends(get_db)):
    f = _filters(request)
    top = None
    if f.get("toponym"):
        tid = tp.index.resolve(f["toponym"])
        if tid:
            name, lat, lon = tp.index.meta[tid]
            top = {"id": tid, "name": name, "lat": lat, "lon": lon}
    return {"toponym": top, "species": srch.species_at_place(db, f)}


@router.get("/authors")
def authors(q: str, db: Session = Depends(get_db)):
    return srch.authors(db, q)


@router.get("/suggest/{what}")
def suggest(what: str, q: str = "", db: Session = Depends(get_db)):
    n = norm(q)
    if len(n) < 2:
        return []
    if what == "species":
        out = []
        for i in list(sp.index.expand_query(q))[:15]:
            ru, lat, _g, grp = sp.index.meta[i]
            out.append({"value": ru, "label": f"{ru} ({lat})" if lat else ru, "group": grp})
        return out
    if what == "toponyms":
        rows = db.execute(select(Toponym).where(Toponym.norm.like(f"%{n}%")).order_by(Toponym.name).limit(15)).scalars()
        return [{"value": t.name, "label": t.name + (f" ({t.lat}, {t.lon})" if t.lat is not None else "")} for t in rows]
    if what == "phenophases":
        rows = db.execute(select(Record.event, func.count()).where(Record.event_norm.like(f"%{n}%"), Record.kind == "phenology")
                          .group_by(Record.event).order_by(func.count().desc()).limit(15)).all()
        return [{"value": e, "label": e} for e, _ in rows]
    if what == "observers":
        rows = db.execute(select(Record.observer).where(Record.observer_norm.like(f"%{n}%")).distinct().limit(15)).all()
        return [{"value": o, "label": o} for (o,) in rows if o]
    raise HTTPException(404)


@router.get("/species")
def species_list(q: str = "", db: Session = Depends(get_db)):
    stmt = select(Species).order_by(Species.group_name, Species.name_ru).limit(1000)
    if q:
        stmt = select(Species).where(Species.id.in_(sp.index.expand_query(q))).order_by(Species.name_ru)
    return [{"id": s.id, "name_ru": s.name_ru, "name_lat": s.name_lat, "genus": s.genus, "group": s.group_name,
             "source": s.source, "synonyms": [x.synonym for x in s.synonyms]} for s in db.execute(stmt).scalars()]


class SpeciesBody(BaseModel):
    name_ru: str
    name_lat: str = ""
    group: str = ""
    synonyms: list[str] = []


@router.post("/species")
def species_add(body: SpeciesBody, db: Session = Depends(get_db)):
    lat = body.name_lat.strip()
    s = Species(name_ru=body.name_ru.strip().lower(), name_lat=lat, genus=lat.split()[0] if lat else "", group_name=body.group, source="user")
    for syn in body.synonyms:
        if syn.strip():
            s.synonyms.append(SpeciesSynonym(synonym=syn.strip(), norm=norm(syn)))
    db.add(s)
    db.commit()
    sp.index.load(db)
    return {"id": s.id}


@router.get("/toponyms")
def toponyms(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Record.toponym_id, func.count()).where(Record.toponym_id.is_not(None)).group_by(Record.toponym_id)).all())
    from .geocode import NOT_FOUND, looks_like_place
    out = [{"id": t.id, "name": t.name, "lat": t.lat, "lon": t.lon, "kind": "" if t.kind == NOT_FOUND else t.kind, "aliases": t.aliases, "source": t.source,
            "records": counts.get(t.id, 0), "searchable": t.lat is None and looks_like_place(t.name), "osm_not_found": t.kind == NOT_FOUND}
           for t in db.execute(select(Toponym)).scalars()]
    out.sort(key=lambda x: (-x["records"], x["name"]))
    return out


class GeocodeBody(BaseModel):
    accept_external: bool = False
    limit: int = 20
    retry_missing: bool = False


@router.post("/toponyms/geocode")
def toponyms_geocode(body: GeocodeBody, db: Session = Depends(get_db)):
    from .geocode import geocode_missing
    if not body.accept_external:
        raise HTTPException(400, "Нужно подтверждение: названия мест будут отправлены в сервис OpenStreetMap Nominatim")
    return geocode_missing(db, limit=max(1, min(body.limit, 40)), retry_missing=body.retry_missing)


@router.post("/toponyms/from-documents")
def toponyms_from_documents(db: Session = Depends(get_db)):
    from .geocode import catalog_from_records
    return {"updated": catalog_from_records(db)}


class ToponymBody(BaseModel):
    name: str
    lat: float | None = None
    lon: float | None = None
    aliases: list[str] = []


def _push_coords(db: Session):
    for t in db.execute(select(Toponym).where(Toponym.lat.is_not(None))).scalars():
        db.execute(Record.__table__.update().where(Record.toponym_id == t.id).values(lat=t.lat, lon=t.lon))
    db.commit()


@router.post("/toponyms")
def toponym_add(body: ToponymBody, db: Session = Depends(get_db)):
    t = tp.upsert_toponym(db, body.name)
    t.lat, t.lon, t.aliases, t.source = body.lat, body.lon, body.aliases, "user"
    db.commit()
    tp.index.load(db)
    _push_coords(db)
    return {"id": t.id}


@router.post("/toponyms/import")
async def toponyms_import(file: UploadFile = File(...), db: Session = Depends(get_db)):
    import csv
    text = (await file.read()).decode("utf-8-sig")
    delim = ";" if ";" in text.split("\n")[0] else ","
    n = 0
    for row in csv.DictReader(io.StringIO(text), delimiter=delim):
        name = (row.get("name") or row.get("название") or "").strip()
        if not name:
            continue
        t = tp.upsert_toponym(db, name)
        lat, lon = row.get("lat") or row.get("широта"), row.get("lon") or row.get("долгота")
        if lat and lon:
            t.lat, t.lon = float(lat.replace(",", ".")), float(lon.replace(",", "."))
        if row.get("aliases"):
            t.aliases = [a.strip() for a in row["aliases"].split("|") if a.strip()]
        t.source = "user"
        n += 1
    db.commit()
    tp.index.load(db)
    _push_coords(db)
    return {"imported": n}


class SavedBody(BaseModel):
    name: str
    params: dict


@router.get("/saved-queries")
def saved_list(db: Session = Depends(get_db)):
    return [{"id": s.id, "name": s.name, "params": s.params, "created_at": s.created_at.isoformat()}
            for s in db.execute(select(SavedQuery).order_by(SavedQuery.id.desc())).scalars()]


@router.post("/saved-queries")
def saved_add(body: SavedBody, db: Session = Depends(get_db)):
    s = SavedQuery(name=body.name.strip() or "Без названия", params=body.params)
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.delete("/saved-queries/{sid}")
def saved_delete(sid: int, db: Session = Depends(get_db)):
    s = db.get(SavedQuery, sid)
    if s:
        db.delete(s)
        db.commit()
    return {"ok": True}


def table_dict(t: DocTable, d: Document | None = None, rows: bool = False) -> dict:
    out = {"id": t.id, "document_id": t.document_id, "doc_title": d.title if d else "", "doc_year": d.year if d else None, "page": t.page_no,
           "title": t.title, "kind": t.kind, "source": t.source, "n_rows": t.n_rows, "n_cols": t.n_cols}
    if rows:
        out["rows"] = t.rows
    return out


def figure_dict(f: Figure, d: Document | None = None) -> dict:
    return {"id": f.id, "document_id": f.document_id, "doc_title": d.title if d else "", "doc_year": d.year if d else None, "page": f.page_no,
            "caption": f.caption, "description": f.description, "source": f.source}


@router.get("/documents/{doc_id}/coverage")
def coverage(doc_id: int, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Документ не найден")
    pages = db.execute(select(Page).where(Page.document_id == doc_id).order_by(Page.page_no)).scalars().all()
    items = [{"page": p.page_no, "printed": p.printed_no, "coverage": p.coverage or "text", "records": p.n_records, "tables": p.n_tables,
              "figures": p.n_figures, "source": p.source, "ai": p.ai_vision, "spread": p.spread, "note": p.note, "section": p.section}
             for p in pages]
    totals: dict[str, int] = {}
    for it in items:
        totals[it["coverage"]] = totals.get(it["coverage"], 0) + 1
    return {"document": doc_dict(d), "pages": items, "totals": totals,
            "tables": sum(i["tables"] for i in items), "figures": sum(i["figures"] for i in items),
            "records": sum(i["records"] for i in items), "ai_pages": sum(1 for i in items if i["ai"]),
            "ocr_pages": sum(1 for i in items if i["source"] in ("ocr", "vision")), "ai_available": ai_available()}


@router.get("/tables")
def tables_list(q: str = "", doc_id: int | None = None, kind: str = "", year: int | None = None, db: Session = Depends(get_db)):
    from .search import _text_cond
    stmt = select(DocTable, Document).join(Document, Document.id == DocTable.document_id)
    if q:
        c = _text_cond(DocTable.search_text, q)
        if c is not None:
            stmt = stmt.where(c)
    if doc_id:
        stmt = stmt.where(DocTable.document_id == doc_id)
    if kind:
        stmt = stmt.where(DocTable.kind == kind)
    if year:
        stmt = stmt.where(Document.year == year)
    rows = db.execute(stmt.order_by(Document.year, DocTable.page_no, DocTable.id).limit(500)).all()
    return [table_dict(t, d) for t, d in rows]


@router.get("/tables/{table_id}")
def table_get(table_id: int, db: Session = Depends(get_db)):
    t = db.get(DocTable, table_id)
    if not t:
        raise HTTPException(404, "Таблица не найдена")
    return table_dict(t, db.get(Document, t.document_id), rows=True)


@router.get("/tables/{table_id}/export")
def table_export(table_id: int, db: Session = Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    t = db.get(DocTable, table_id)
    if not t:
        raise HTTPException(404, "Таблица не найдена")
    d = db.get(Document, t.document_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Таблица"[:31]
    ws.append([t.title or "Таблица", f"{d.title}, стр. PDF {t.page_no}"])
    ws["A1"].font = Font(bold=True)
    for i, row in enumerate(t.rows):
        ws.append(row)
        if i == 0:
            for c in ws[ws.max_row]:
                c.font = Font(bold=True)
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=table_{table_id}.xlsx"})


@router.get("/figures")
def figures_list(q: str = "", doc_id: int | None = None, db: Session = Depends(get_db)):
    from .search import _text_cond
    stmt = select(Figure, Document).join(Document, Document.id == Figure.document_id)
    if q:
        c = _text_cond(Figure.search_text, q)
        if c is not None:
            stmt = stmt.where(c)
    if doc_id:
        stmt = stmt.where(Figure.document_id == doc_id)
    rows = db.execute(stmt.order_by(Document.year, Figure.page_no, Figure.id).limit(500)).all()
    return [figure_dict(f, d) for f, d in rows]


@router.post("/documents/{doc_id}/pages/{page_no}/ai")
def page_ai(doc_id: int, page_no: int):
    from .db import SessionLocal
    with SessionLocal() as db:
        d = db.get(Document, doc_id)
        if d and (d.kind or "pdf") != "pdf":
            raise HTTPException(400, "ИИ-зрение работает со страницами PDF; для Word и таблиц текст читается напрямую")
    try:
        return reanalyze_page_with_ai(doc_id, page_no)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"Ошибка ИИ-зрения: {str(e)[:200]}")


class GbifBody(BaseModel):
    accept_external: bool = False
    limit: int = 40
    retry_missing: bool = False


@router.post("/species/gbif")
def species_gbif(body: GbifBody, db: Session = Depends(get_db)):
    from .taxonomy import refine_species
    if not body.accept_external:
        raise HTTPException(400, "Нужно подтверждение: латинские названия видов будут отправлены в сервис GBIF")
    return refine_species(db, limit=max(1, min(body.limit, 100)), retry_missing=body.retry_missing)


@router.post("/documents/{doc_id}/ai-check")
def document_ai_check(doc_id: int, db: Session = Depends(get_db)):
    import threading

    from .pipeline.verify import verify_document
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Документ не найден")
    if d.status == "processing":
        raise HTTPException(409, "Документ обрабатывается")
    if not (settings.yc_ready and settings.llm_enabled):
        raise HTTPException(503, "Yandex Cloud не настроен")

    def job():
        from .db import SessionLocal
        with SessionLocal() as s:
            doc = s.get(Document, doc_id)
            doc.stage = "проверка записей"
            s.commit()
        try:
            verify_document(doc_id)
        finally:
            with SessionLocal() as s:
                doc = s.get(Document, doc_id)
                doc.stage = ""
                s.commit()

    threading.Thread(target=job, daemon=True, name=f"ai-check-{doc_id}").start()
    return {"started": True}
