from __future__ import annotations

import re

from rapidfuzz import fuzz

from ..models import Document, Record
from ..textutil import norm, search_blob
from . import species as sp
from . import toponyms as tp
from .dates import day_of_year, parse_fuzzy
from .phases import canon_phases
from .sections import norm_observer
from .units import normalize_unit, topic_of

KINDS = {"phenology", "count", "event"}


def _num(v):
    if v is None or v == "":
        return None, ""
    if isinstance(v, (int, float)):
        return float(v), str(v)
    s = str(v).strip().replace(",", ".").replace(" ", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return (float(m.group(0)) if m else None), str(v)


def _flat(s: str) -> str:
    s = re.sub(r"[|\-–—]+", " ", s.lower().replace("ё", "е"))
    return re.sub(r"\s+", " ", s).strip()


def fragment_in_text(fragment: str, page_text: str) -> bool:
    if not fragment:
        return False
    a = _flat(re.sub(r"[-–]\s*\n\s*", "", fragment))
    b = _flat(re.sub(r"[-–]\s*\n\s*", "", page_text))
    if a in b:
        return True
    return fuzz.partial_ratio(a[:200], b) >= 90


def find_fragment_fallback(page_text: str, needles: list[str]) -> str:
    sents = re.split(r"(?<=[.!?])\s+|\n{2,}", page_text)
    best, score = "", 0
    for s in sents:
        sc = sum(1 for n in needles if n and n.lower() in s.lower())
        if sc > score and len(s) < 600:
            best, score = s.strip(), sc
    return re.sub(r"\s+", " ", best)[:300]


def make_record(raw: dict, doc: Document, page_no: int, page_text: str, section: str, section_authors: list[str],
                method: str, group_hint: str = "", page_rich: str = "") -> Record | None:
    raw = {k: (v.replace("\x00", "") if isinstance(v, str) else v) for k, v in raw.items()}
    page_text = (page_text or "").replace("\x00", "")
    kind = raw.get("kind") if raw.get("kind") in KINDS else "event"
    ru = str(raw.get("species_ru") or "").strip()[:300]
    lat = str(raw.get("species_lat") or "").strip()[:200]
    event = str(raw.get("event") or "").strip()[:500]
    if not (ru or lat or event):
        return None
    flags: list[str] = []
    conf = 0.9 if method == "rule" else 0.68

    sid, how = sp.index.resolve(ru, lat)
    group = ""
    if sid:
        meta = sp.index.meta[sid]
        group = meta[3]
        if how == "fuzzy":
            flags.append("species_fuzzy"); conf -= 0.1
        elif method == "llm":
            conf += 0.08
    else:
        if not ru and event:
            hits = sp.index.find_in_text(event)
            if hits:
                sid = hits[0][0]
                group = sp.index.meta[sid][3]
                ru = ru or hits[0][1]
        if not sid and lat and sp.latin_norm(lat) and " " in sp.latin_norm(lat):
            group = group_hint or sp.group_from_section(section)
            flags.append("species_new")
        elif not sid and ru and re.search(r"[A-Za-z]{3}", lat or ""):
            group = group_hint or sp.group_from_section(section)
            flags.append("species_new")
        elif not sid and (ru or lat):
            flags.append("species_unresolved"); conf -= 0.15
    if not group:
        group = group_hint or sp.group_from_section(section)

    date_raw = str(raw.get("date") or "").strip()[:100]
    pd = parse_fuzzy(date_raw, doc.year) if date_raw else {"date": None, "date_end": None, "precision": "", "flags": []}
    d, d_end, precision = pd["date"], pd["date_end"], pd["precision"]
    for f in pd["flags"]:
        if f == "year_inferred":
            flags.append("year_inferred"); conf -= 0.05
        else:
            flags.append(f); conf -= 0.2
    if precision in ("season", "year", "month"):
        conf -= 0.05
    y_lo, y_hi = doc.year, (getattr(doc, "year_to", None) or doc.year)
    if d and y_lo and not (y_lo - 1 <= d.year <= y_hi + 1) and method != "rule":
        flags.append("date_year_differs"); conf -= 0.2
    year = d.year if d else doc.year
    if method == "llm" and precision == "day" and "year_inferred" not in pd["flags"]:
        conf += 0.08

    if raw.get("ambiguous") in (True, "true", "True", 1):
        flags.append("ambiguous_columns"); conf -= 0.25

    value, value_raw = _num(raw.get("value"))
    unit_raw = str(raw.get("unit") or "").strip()[:80]
    unit = normalize_unit(unit_raw)[:80]
    if value is not None and not unit:
        flags.append("no_unit"); conf -= 0.05
    elif value is not None and unit and method == "llm":
        conf += 0.03

    top_text = str(raw.get("toponym") or "").strip()[:300]
    tid, lat_c, lon_c = None, None, None
    if top_text:
        tid = tp.index.resolve(top_text)
        if tid:
            _, lat_c, lon_c = tp.index.meta[tid]
            conf += 0.03 if method == "llm" else 0
        elif len(top_text) <= 80:
            flags.append("toponym_new")
        else:
            flags.append("toponym_not_in_catalog")
    frag = str(raw.get("fragment") or "").strip()
    try:
        rl, rn = float(str(raw.get("lat")).replace(",", ".")), float(str(raw.get("lon")).replace(",", "."))
        if 55 <= rl <= 75 and 15 <= rn <= 45:
            lat_c, lon_c = rl, rn
    except (TypeError, ValueError):
        pass
    if frag:
        c = tp.extract_coords(frag)
        if c:
            lat_c, lon_c = c

    if method == "llm":
        if fragment_in_text(frag, page_text + "\n" + page_rich):
            conf += 0.10
        else:
            flags.append("fragment_not_found"); conf -= 0.25
            fb = find_fragment_fallback(page_text, [ru, lat, date_raw, event.split()[0] if event else ""])
            frag = fb or frag
    if not frag:
        flags.append("no_fragment"); conf -= 0.1

    obs = norm_observer(str(raw.get("observer") or ""))
    obs_src = "text" if obs else ""
    if not obs and section_authors:
        obs = "; ".join(section_authors[:3])
        obs_src = "section"

    r = Record(
        document_id=doc.id, page_no=page_no, year=year, kind=kind,
        species_text=ru, species_lat_text=lat, species_id=sid, group_name=group,
        event=event[:500], date=d, date_end=d_end, date_precision=precision, date_raw=date_raw[:100], doy=day_of_year(d),
        topic=topic_of(section), methodology=str(raw.get("methodology") or "").strip()[:300],
        toponym_text=top_text[:300], toponym_id=tid, lat=lat_c, lon=lon_c,
        habitat=str(raw.get("habitat") or "")[:300], value=value, value_raw=value_raw[:100], unit=unit[:80], unit_raw=unit_raw,
        observer=obs[:300], observer_source=obs_src, note=str(raw.get("note") or ""),
        fragment=frag[:1500], section=section[:300], extra=raw.get("extra") or {},
        confidence=max(0.05, min(0.97, round(conf, 2))), flags=flags, method=method,
    )
    refresh_search(r)
    return r


def refresh_search(r: Record) -> None:
    r.search_text = search_blob(r.species_text, r.species_lat_text, r.event, r.toponym_text, r.habitat, r.observer, r.note, r.group_name, r.methodology, r.user_note)
    phases = canon_phases(r.event, r.fragment if r.kind == "phenology" else "")
    r.methodology_norm = search_blob(r.methodology)
    r.species_norm = search_blob(r.species_text, r.species_lat_text)
    r.event_norm = search_blob(r.event, *phases)
    if phases:
        r.extra = {**(r.extra or {}), "phase": phases[0]}
    r.observer_norm = norm(r.observer)
    r.toponym_norm = search_blob(r.toponym_text)


def finalize_species(db, rec: Record, group_hint: str):
    flags = list(rec.flags or [])
    if rec.species_id is None and "species_new" in flags:
        s = sp.upsert_species(db, rec.species_text, rec.species_lat_text, rec.group_name or group_hint, "auto")
        flags = [f for f in flags if f != "species_new"]
        if s:
            rec.species_id = s.id
            flags.append("species_auto_added")
    if rec.toponym_id is None and "toponym_new" in flags:
        t = tp.upsert_toponym(db, rec.toponym_text)
        flags = [f for f in flags if f != "toponym_new"]
        if t:
            rec.toponym_id = t.id
            flags.append("toponym_not_in_catalog")
    rec.flags = flags


def dedupe(records: list[Record]) -> list[Record]:
    best: dict[tuple, Record] = {}
    order: list[tuple] = []
    for r in records:
        key = (norm(r.species_text or r.species_lat_text)[:25], r.date, r.date_end, norm(r.event)[:18], r.value, norm(r.toponym_text)[:15])
        if key not in best:
            best[key] = r
            order.append(key)
        elif r.confidence > best[key].confidence:
            best[key] = r
    return [best[k] for k in order]
