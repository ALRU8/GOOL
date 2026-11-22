from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from .models import Document, Record, Section
from .pipeline import species as sp
from .pipeline.dates import day_of_year, parse_md
from .textutil import is_known, lemma, norm, tokens


def _text_cond(column, text: str):
    conds = []
    for t in tokens(text):
        n = norm(t)
        lm = lemma(t)
        parts = [column.like(f"%{n}%")]
        if lm != n:
            parts.append(column.like(f"%{lm}%"))
        if len(n) >= 8 or (len(n) >= 6 and not is_known(n)):
            parts.append(column.like(f"%{n[:-2]}%"))
        conds.append(or_(*parts))
    return and_(*conds) if conds else None


def apply_filters(stmt, p: dict[str, Any]):
    if p.get("species"):
        ids = sp.index.expand_query(p["species"])
        strict = p.get("only_species") in (True, "1", "true", 1)
        text = _text_cond(Record.species_norm if strict else Record.search_text, p["species"])
        if ids and text is not None:
            stmt = stmt.where(or_(Record.species_id.in_(ids), text))
        elif ids:
            stmt = stmt.where(Record.species_id.in_(ids))
        elif text is not None:
            stmt = stmt.where(text)
    if p.get("q"):
        c = _text_cond(Record.search_text, p["q"])
        if c is not None:
            stmt = stmt.where(c)
    if p.get("phenophase"):
        c = _text_cond(Record.event_norm, p["phenophase"])
        if c is not None:
            stmt = stmt.where(c)
    if p.get("toponym"):
        c = _text_cond(Record.toponym_norm, p["toponym"])
        if c is not None:
            stmt = stmt.where(c)
    if p.get("observer"):
        n = norm(p["observer"])
        if n:
            stmt = stmt.where(Record.observer_norm.like(f"%{n}%"))
    if p.get("group"):
        stmt = stmt.where(Record.group_name == p["group"])
    if p.get("kind"):
        stmt = stmt.where(Record.kind == p["kind"])
    if p.get("year_from"):
        stmt = stmt.where(Record.year >= int(p["year_from"]))
    if p.get("year_to"):
        stmt = stmt.where(Record.year <= int(p["year_to"]))
    if p.get("doc_id"):
        stmt = stmt.where(Record.document_id == int(p["doc_id"]))
    if p.get("min_conf") not in (None, ""):
        stmt = stmt.where(Record.confidence >= float(p["min_conf"]))
    if p.get("verified") in (True, "1", "true", 1):
        stmt = stmt.where(Record.verified.is_(True))
    if p.get("method"):
        stmt = stmt.where(Record.method == p["method"])
    if p.get("topic"):
        stmt = stmt.where(Record.topic == p["topic"])
    if p.get("unit"):
        stmt = stmt.where(Record.unit == p["unit"])
    if p.get("has_value") in (True, "1", "true", 1):
        stmt = stmt.where(Record.value.is_not(None))
    if p.get("has_note") in (True, "1", "true", 1):
        stmt = stmt.where(Record.user_note != "")
    if p.get("methodology"):
        c = _text_cond(Record.methodology_norm, p["methodology"])
        if c is not None:
            stmt = stmt.where(c)
    a, b = parse_md(p.get("md_from", "")), parse_md(p.get("md_to", ""))
    if a is not None and b is not None:
        stmt = stmt.where(Record.doy.is_not(None), (Record.doy >= a) & (Record.doy <= b) if a <= b else ((Record.doy >= a) | (Record.doy <= b)))
    elif a is not None:
        stmt = stmt.where(Record.doy >= a)
    elif b is not None:
        stmt = stmt.where(Record.doy.is_not(None), Record.doy <= b)
    return stmt


SORTS = {
    "date": Record.date, "year": Record.year, "species": Record.species_text, "conf": Record.confidence,
    "page": Record.page_no, "event": Record.event, "id": Record.id,
}


def is_species_match(r: Record, query: str, ids: set[int]) -> bool:
    if r.species_id in ids:
        return True
    blob = r.species_norm or ""
    toks = tokens(query)
    return bool(toks) and all(norm(t) in blob or lemma(t) in blob for t in toks)


def mentions_count(db: Session, p: dict[str, Any], total: int) -> int:
    if not p.get("species"):
        return 0
    if p.get("only_species") in (True, "1", "true", 1):
        wide = apply_filters(select(Record), {k: v for k, v in p.items() if k != "only_species"})
        return max(0, db.execute(select(func.count()).select_from(wide.subquery())).scalar_one() - total)
    strict = apply_filters(select(Record), {**p, "only_species": "1"})
    return max(0, total - db.execute(select(func.count()).select_from(strict.subquery())).scalar_one())


def search(db: Session, p: dict[str, Any], page: int = 1, page_size: int = 50):
    base = apply_filters(select(Record), p)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    col = SORTS.get(p.get("sort") or "date", Record.date)
    desc = (p.get("order") or "asc") == "desc"
    order = [col.desc().nulls_last() if desc else col.asc().nulls_last(), Record.id]
    if p.get("species") and p.get("only_species") not in (True, "1", "true", 1):
        ids = sp.index.expand_query(p["species"])
        if ids:
            order.insert(0, case((Record.species_id.in_(ids), 0), else_=1))
    rows = db.execute(base.order_by(*order).limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return total, rows


def iter_all(db: Session, p: dict[str, Any], limit: int = 100_000):
    order = [Record.year.asc().nulls_last(), Record.date.asc().nulls_last(), Record.id]
    if p.get("species") and p.get("only_species") not in (True, "1", "true", 1):
        ids = sp.index.expand_query(p["species"])
        if ids:
            order.insert(0, case((Record.species_id.in_(ids), 0), else_=1))
    base = apply_filters(select(Record), p).order_by(*order).limit(limit)
    yield from db.execute(base).scalars()


def doy(d: dt.date) -> int:
    return day_of_year(d)


def doy_to_text(v: float) -> str:
    d = dt.date(2001, 1, 1) + dt.timedelta(days=int(round(v)) - 1)
    return d.strftime("%d.%m")


def series_stats(points: list[dict]) -> dict:
    import statistics
    by_year: dict[int, list[int]] = {}
    for p in points:
        by_year.setdefault(p["year"], []).append(p["doy"])
    years = sorted(by_year)
    vals = [statistics.mean(by_year[y]) for y in years]
    n = len(years)
    out = {"n": n, "observations": len(points), "years": [years[0], years[-1]]}
    out["mean"] = doy_to_text(statistics.mean(vals))
    out["median"] = doy_to_text(statistics.median(vals))
    lo, hi = min(points, key=lambda p: p["doy"]), max(points, key=lambda p: p["doy"])
    out["earliest"] = {"date": doy_to_text(lo["doy"]), "year": lo["year"]}
    out["latest"] = {"date": doy_to_text(hi["doy"]), "year": hi["year"]}
    out["sd"] = round(statistics.pstdev(vals), 1) if n > 1 else 0.0
    out["trend_per_decade"] = None
    out["trend_line"] = None
    out["r2"] = None
    if n >= 8 and years[-1] - years[0] >= 8:
        my, mv = statistics.mean(years), statistics.mean(vals)
        sxx = sum((y - my) ** 2 for y in years)
        if sxx:
            slope = sum((y - my) * (v - mv) for y, v in zip(years, vals)) / sxx
            out["trend_per_decade"] = round(slope * 10, 1)
            out["trend_line"] = {"y0": years[0], "v0": mv + slope * (years[0] - my), "y1": years[-1], "v1": mv + slope * (years[-1] - my)}
            syy = sum((v - mv) ** 2 for v in vals)
            out["r2"] = round((slope * slope * sxx) / syy, 2) if syy else 0.0
    return out


def phenology_series(db: Session, p: dict[str, Any]):
    p = dict(p)
    p["kind"] = "phenology"
    stmt = (apply_filters(select(Record), p).where(Record.date.is_not(None), Record.date_precision.in_(["day", ""]))
            .order_by(Record.year, Record.date))
    series: dict[str, dict] = {}
    for r in db.execute(stmt.limit(5000)).scalars():
        phase = (r.extra or {}).get("phase") or r.event
        key = f"{r.species_id or norm(r.species_text)}|{norm(phase)}"
        label = (sp.index.meta[r.species_id][0] + ": " if r.species_id else (r.species_text + ": " if r.species_text else "")) + phase
        s = series.setdefault(key, {"event": label, "species": r.species_text, "points": []})
        s["points"].append({"year": r.date.year, "date": r.date.isoformat(), "doy": doy(r.date), "id": r.id,
                            "conf": r.confidence, "doc": r.document_id, "page": r.page_no})
    out = [s for s in series.values() if s["points"]]
    for s in out:
        s["stats"] = series_stats(s["points"])
    out.sort(key=lambda s: -len(s["points"]))
    return out[:30]


def facets(db: Session) -> dict:
    topics = db.execute(select(Record.topic, func.count()).where(Record.topic != "", Record.topic != "—").group_by(Record.topic).order_by(func.count().desc()).limit(40)).all()
    units = db.execute(select(Record.unit, func.count()).where(Record.unit != "").group_by(Record.unit).order_by(func.count().desc()).limit(40)).all()
    return {"topics": [{"value": t, "count": c} for t, c in topics], "units": [{"value": u, "count": c} for u, c in units]}


def species_at_place(db: Session, p: dict[str, Any]):
    stmt = apply_filters(select(Record), p)
    sub = stmt.subquery()
    rows = db.execute(
        select(
            sub.c.species_id, func.max(sub.c.species_text), func.max(sub.c.group_name), func.count(), func.min(sub.c.year),
            func.max(sub.c.year), func.max(sub.c.lat), func.max(sub.c.lon), func.max(sub.c.toponym_text),
        ).group_by(sub.c.species_id, sub.c.species_text).order_by(func.count().desc()).limit(1000)
    ).all()
    out = []
    for sid, name, grp, cnt, y0, y1, lat, lon, top in rows:
        if not (name or sid):
            continue
        meta = sp.index.meta.get(sid) if sid else None
        out.append({
            "species_id": sid, "name_ru": meta[0] if meta else name, "name_lat": meta[1] if meta else "",
            "group": meta[3] if meta else grp, "records": cnt, "year_from": y0, "year_to": y1, "lat": lat, "lon": lon,
        })
    merged: dict[Any, dict] = {}
    for o in out:
        k = o["species_id"] or o["name_ru"]
        if k in merged:
            m = merged[k]
            m["records"] += o["records"]
            m["year_from"] = min(filter(None, [m["year_from"], o["year_from"]]), default=None)
            m["year_to"] = max(filter(None, [m["year_to"], o["year_to"]]), default=None)
            m["lat"] = m["lat"] or o["lat"]
            m["lon"] = m["lon"] or o["lon"]
        else:
            merged[k] = o
    return sorted(merged.values(), key=lambda x: -x["records"])


def authors(db: Session, q: str):
    n = norm(q)
    if not n:
        return []
    rows = db.execute(
        select(Section, Document).join(Document, Document.id == Section.document_id)
        .where(Section.authors_norm.like(f"%{n}%")).order_by(Document.year, Section.page_start).limit(300)
    ).all()
    return [{"document_id": d.id, "year": d.year, "document": d.title, "section": s.title, "page_start": s.page_start,
             "page_end": s.page_end, "authors": s.authors} for s, d in rows]
