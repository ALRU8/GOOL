import math
from typing import Any

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.orm import Session

from .models import Record
from .pipeline import species as sp
from .search import apply_filters

RECENCY = [1.0, 0.6, 0.4]
QUERY_KEYS = ("species", "toponym", "phenophase", "observer", "q")


def _title(s: str) -> str:
    return (s[:1].upper() + s[1:]) if s else s


def _key(params: dict[str, str]) -> tuple:
    return tuple(sorted((k, v.strip().lower()) for k, v in params.items() if v))


def _clean(params: dict[str, Any]) -> dict[str, str]:
    return {k: str(v).strip() for k, v in params.items() if k in QUERY_KEYS and str(v).strip()}


def _add(out: dict, params: dict[str, str], label: str, reason: str, weight: float, count: int) -> None:
    k = _key(params)
    score = weight * math.log(2 + count)
    if k not in out or out[k]["score"] < score:
        out[k] = {"label": label, "params": params, "reason": reason, "score": score}


def for_species(db: Session, query: str, out: dict) -> None:
    ids = list(sp.index.expand_query(query))
    if not ids:
        return
    own = {sp.index.meta[i][0] for i in ids if i in sp.index.meta}
    pages = select(Record.document_id.label("d"), Record.page_no.label("p")).where(Record.species_id.in_(ids)).distinct().subquery()
    rows = db.execute(
        select(Record.species_id, func.count(distinct(Record.id)))
        .join(pages, and_(Record.document_id == pages.c.d, Record.page_no == pages.c.p))
        .where(Record.species_id.is_not(None), Record.species_id.not_in(ids))
        .group_by(Record.species_id).order_by(func.count(distinct(Record.id)).desc()).limit(8)
    ).all()
    for sid, n in rows:
        name = sp.index.meta[sid][0]
        _add(out, {"species": name}, _title(name), f"встречается на тех же страницах, что «{query}»", 2.0, n)
    genera = {sp.index.meta[i][2].lower() for i in ids if i in sp.index.meta and sp.index.meta[i][2]}
    sibling = [sid for g in genera for sid in sp.index.genus_ids.get(g, []) if sid not in ids]
    if sibling:
        rows = db.execute(select(Record.species_id, func.count()).where(Record.species_id.in_(sibling)).group_by(Record.species_id)).all()
        for sid, n in rows:
            name = sp.index.meta[sid][0]
            _add(out, {"species": name}, _title(name), f"того же рода: {sp.index.meta[sid][2]}", 3.0, n)
    groups = {sp.index.meta[i][3] for i in ids if i in sp.index.meta and sp.index.meta[i][3]}
    for grp in groups:
        rows = db.execute(
            select(Record.species_id, func.count()).where(Record.group_name == grp, Record.species_id.is_not(None), Record.species_id.not_in(ids))
            .group_by(Record.species_id).order_by(func.count().desc()).limit(4)
        ).all()
        for sid, n in rows:
            name = sp.index.meta[sid][0]
            if name not in own:
                _add(out, {"species": name}, _title(name), f"та же группа: {grp}", 1.0, n)
    rows = db.execute(
        select(Record.event, func.count()).where(Record.species_id.in_(ids), Record.kind == "phenology", Record.event != "")
        .group_by(Record.event).order_by(func.count().desc()).limit(3)
    ).all()
    base = sp.index.meta[ids[0]][0]
    for ev, n in rows:
        _add(out, {"species": base, "phenophase": ev}, f"{_title(base)}, {ev}", "фенология этого вида по годам", 1.5, n)
    rows = db.execute(
        select(Record.toponym_text, func.count()).where(Record.species_id.in_(ids), Record.toponym_text != "", Record.toponym_id.is_not(None), Record.lat.is_not(None))
        .group_by(Record.toponym_text).order_by(func.count().desc()).limit(3)
    ).all()
    for place, n in rows:
        _add(out, {"toponym": place}, _title(place), f"где отмечен «{query}»", 1.5, n)


def for_toponym(db: Session, query: str, out: dict) -> None:
    stmt = apply_filters(select(Record.species_id, func.count()).where(Record.species_id.is_not(None)), {"toponym": query})
    for sid, n in db.execute(stmt.group_by(Record.species_id).order_by(func.count().desc()).limit(5)).all():
        name = sp.index.meta[sid][0]
        _add(out, {"species": name}, _title(name), f"встречается в месте «{query}»", 2.0, n)
    stmt = apply_filters(select(Record.event, func.count()).where(Record.kind == "phenology"), {"toponym": query})
    for ev, n in db.execute(stmt.group_by(Record.event).order_by(func.count().desc()).limit(2)).all():
        _add(out, {"phenophase": ev}, _title(ev), f"фенология в месте «{query}»", 1.0, n)


def for_phenophase(db: Session, query: str, out: dict) -> None:
    stmt = apply_filters(select(Record.species_id, func.count()).where(Record.species_id.is_not(None), Record.kind == "phenology"), {"phenophase": query})
    rows = db.execute(stmt.group_by(Record.species_id).order_by(func.count().desc()).limit(5)).all()
    ids = [sid for sid, _ in rows]
    for sid, n in rows:
        name = sp.index.meta[sid][0]
        _add(out, {"species": name, "phenophase": query}, f"{_title(name)}, {query}", f"тоже есть фенофаза «{query}»", 2.0, n)
    if ids:
        others = db.execute(
            select(Record.event, func.count()).where(Record.species_id.in_(ids), Record.kind == "phenology", Record.event != "")
            .group_by(Record.event).order_by(func.count().desc()).limit(6)
        ).all()
        for ev, n in others:
            if ev.lower() != query.lower():
                _add(out, {"phenophase": ev}, _title(ev), "другая фенофаза у тех же видов", 1.5, n)


def for_observer(db: Session, query: str, out: dict) -> None:
    stmt = apply_filters(select(Record.species_id, func.count()).where(Record.species_id.is_not(None)), {"observer": query})
    for sid, n in db.execute(stmt.group_by(Record.species_id).order_by(func.count().desc()).limit(5)).all():
        name = sp.index.meta[sid][0]
        _add(out, {"species": name}, _title(name), f"в записях автора «{query}»", 2.0, n)


def recommend(db: Session, history: list[dict[str, Any]], limit: int = 6) -> dict:
    seen = {_key(_clean(h)) for h in history}
    out: dict = {}
    for idx, raw in enumerate(history[: len(RECENCY)]):
        h = _clean(raw)
        local: dict = {}
        if h.get("species"):
            for_species(db, h["species"], local)
        if h.get("toponym"):
            for_toponym(db, h["toponym"], local)
        if h.get("phenophase") and not h.get("species"):
            for_phenophase(db, h["phenophase"], local)
        if h.get("observer"):
            for_observer(db, h["observer"], local)
        if h.get("q") and not h.get("species"):
            for_species(db, h["q"], local)
        for k, item in local.items():
            item["score"] *= RECENCY[idx]
            if k not in out or out[k]["score"] < item["score"]:
                out[k] = item
    items = sorted((v for k, v in out.items() if k not in seen), key=lambda v: -v["score"])
    picked, used = [], {}
    for it in items:
        kind = "species" if "species" in it["params"] and "phenophase" not in it["params"] else ("phenology" if "phenophase" in it["params"] else "place")
        if used.get(kind, 0) >= 3:
            continue
        used[kind] = used.get(kind, 0) + 1
        picked.append({"label": it["label"], "params": it["params"], "reason": it["reason"]})
        if len(picked) >= limit:
            break
    return {"items": picked}
