from __future__ import annotations

import re
import threading
import time

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Record, Toponym
from .pipeline import toponyms as tp

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "gool/1.0 (research tool for nature reserve archives)"
VIEWBOX = "27.0,71.0,36.0,67.0"
NOT_FOUND = "osm:не найдено"
_lock = threading.Lock()
_last = [0.0]


def looks_like_place(name: str) -> bool:
    n = (name or "").strip()
    if not n or len(n) > 60 or re.search(r"\d", n):
        return False
    return any(re.fullmatch(r"[А-ЯЁ][а-яё\-]{3,}", w) for w in re.findall(r"[А-Яа-яЁё\-]+", n))


def _http_get(params: dict) -> list:
    with _lock:
        wait = 1.1 - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
    r = httpx.get(NOMINATIM, params=params, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return r.json()


def geocode_one(name: str) -> tuple[float, float] | None:
    res = _http_get({"q": name, "format": "jsonv2", "limit": 1, "accept-language": "ru", "viewbox": VIEWBOX, "bounded": 1})
    if res:
        return float(res[0]["lat"]), float(res[0]["lon"])
    return None


def _push_coords(db: Session, t: Toponym) -> None:
    db.execute(Record.__table__.update().where(Record.toponym_id == t.id).values(lat=t.lat, lon=t.lon))


def geocode_missing(db: Session, limit: int = 20, retry_missing: bool = False) -> dict:
    counts = dict(db.execute(select(Record.toponym_id, func.count()).where(Record.toponym_id.is_not(None)).group_by(Record.toponym_id)).all())
    cands = [t for t in db.execute(select(Toponym).where(Toponym.lat.is_(None))).scalars()
             if looks_like_place(t.name) and (retry_missing or t.kind != NOT_FOUND)]
    cands.sort(key=lambda t: -counts.get(t.id, 0))
    found, missing, errors = [], [], 0
    for t in cands[:limit]:
        try:
            xy = geocode_one(t.name)
        except Exception:
            errors += 1
            continue
        if xy:
            t.lat, t.lon, t.source = xy[0], xy[1], "osm"
            if t.kind == NOT_FOUND:
                t.kind = ""
            _push_coords(db, t)
            found.append(t.name)
        else:
            t.kind = NOT_FOUND
            missing.append(t.name)
        db.commit()
    tp.index.load(db)
    return {"checked": min(limit, len(cands)), "found": found, "not_found": missing, "errors": errors, "remaining": max(0, len(cands) - limit)}


def catalog_from_records(db: Session) -> int:
    rows = db.execute(select(Record.toponym_id, func.avg(Record.lat), func.avg(Record.lon)).where(
        Record.toponym_id.is_not(None), Record.lat.is_not(None)).group_by(Record.toponym_id)).all()
    n = 0
    for tid, la, lo in rows:
        t = db.get(Toponym, tid)
        if t and t.lat is None and la is not None:
            t.lat, t.lon, t.source = float(la), float(lo), "doc"
            n += 1
    if n:
        db.commit()
        tp.index.load(db)
    return n
