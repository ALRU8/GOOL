from __future__ import annotations

import re
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Record, Species, SpeciesSynonym
from .pipeline import species as sp
from .textutil import norm

GBIF_MATCH = "https://api.gbif.org/v1/species/match"
LICHEN_CLASSES = {"Lecanoromycetes", "Arthoniomycetes", "Lichinomycetes", "Eurotiomycetes"}
FISH_CLASSES = {"Actinopterygii", "Cephalaspidomorphi", "Petromyzonti", "Chondrichthyes", "Elasmobranchii", "Holocephali", "Teleostei"}


def group_of(m: dict) -> str:
    kingdom, phylum, cls = m.get("kingdom", ""), m.get("phylum", ""), m.get("class", "")
    if kingdom == "Plantae":
        return "мхи" if phylum in ("Bryophyta", "Marchantiophyta", "Anthocerotophyta") else "сосудистые растения"
    if kingdom == "Fungi":
        return "лишайники" if cls in LICHEN_CLASSES else "грибы"
    if kingdom == "Animalia":
        if cls == "Aves":
            return "птицы"
        if cls == "Mammalia":
            return "млекопитающие"
        if cls in FISH_CLASSES:
            return "рыбы"
        if cls in ("Amphibia", "Reptilia"):
            return "амфибии и рептилии"
        if cls == "Insecta":
            return "насекомые"
        return "беспозвоночные"
    return ""


def _http_get(name: str) -> dict:
    r = httpx.get(GBIF_MATCH, params={"name": name, "kingdom": ""}, headers={"User-Agent": "gool/1.0"}, timeout=20)
    r.raise_for_status()
    return r.json()


def match(latin: str) -> dict | None:
    m = _http_get(latin)
    if m.get("matchType") in ("EXACT", "FUZZY", "HIGHERRANK") and m.get("rank") in ("SPECIES", "GENUS", "SUBSPECIES", "VARIETY") and m.get("confidence", 0) >= 90:
        return m
    return None


def refine_species(db: Session, limit: int = 40, retry_missing: bool = False) -> dict:
    todo = [s for s in db.execute(select(Species).where(Species.name_lat != "")).scalars()
            if re.search(r"[A-Za-z]{3} [a-z]", s.name_lat) and (s.taxon_status == "" or (retry_missing and s.taxon_status == "gbif-none"))]
    todo.sort(key=lambda s: (s.source != "auto", s.id))
    matched = changed_group = renamed = 0
    missing: list[str] = []
    for s in todo[:limit]:
        try:
            m = match(s.name_lat)
        except Exception:
            continue
        time.sleep(0.1)
        if not m:
            s.taxon_status = "gbif-none"
            missing.append(s.name_lat)
            continue
        canon = m.get("canonicalName") or m.get("species") or s.name_lat
        group = group_of(m)
        if norm(canon) != norm(s.name_lat) and m.get("rank") != "GENUS":
            if not any(x.synonym == s.name_lat for x in s.synonyms):
                s.synonyms.append(SpeciesSynonym(synonym=s.name_lat, norm=norm(s.name_lat)))
            s.name_lat = canon
            renamed += 1
        s.genus = m.get("genus") or s.genus
        if group and s.group_name != group:
            s.group_name = group
            db.execute(Record.__table__.update().where(Record.species_id == s.id).values(group_name=group))
            changed_group += 1
        s.taxon_status = "gbif"
        matched += 1
        db.commit()
    db.commit()
    sp.index.load(db)
    return {"checked": min(limit, len(todo)), "matched": matched, "renamed": renamed, "groups_updated": changed_group,
            "not_found": missing, "remaining": max(0, len(todo) - limit)}
