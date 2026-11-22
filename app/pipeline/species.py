from __future__ import annotations

import csv
import re
import threading
from pathlib import Path

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ROOT
from ..models import Species, SpeciesSynonym
from ..textutil import lemma_key, lemmas, norm

LAT_RE = re.compile(r"\b([A-Z][a-z]{2,})(?:\s+(?:x\s+)?([a-z]{3,}(?:-[a-z]+)?))?")
AUTHORITY = re.compile(r"\s*\(?[A-Z][A-Za-z.\-]*(?:\s*(?:&|et|ex)\s*[A-Z][A-Za-z.\-]*)*\)?\s*$")

GROUP_BY_SECTION = [
    ("птиц", "птицы"), ("млекопитающ", "млекопитающие"), ("рыб", "рыбы"), ("амфиби", "амфибии и рептилии"),
    ("рептил", "амфибии и рептилии"), ("беспозвоночн", "беспозвоночные"), ("гриб", "грибы"),
    ("лишайник", "лишайники"), ("мох", "мхи"), ("флор", "сосудистые растения"), ("растительн", "сосудистые растения"),
    ("плодоношен", "сосудистые растения"), ("ягодник", "сосудистые растения"), ("урожайност", "грибы"),
]


def latin_norm(s: str) -> str:
    s = (s or "").replace("×", " x ").strip()
    m = LAT_RE.search(s)
    if not m:
        return ""
    genus, sp = m.group(1).lower(), (m.group(2) or "").lower()
    if sp in {"sp", "spp", "var", "subsp", "ssp", "f", "L", "l"} or len(sp) < 3:
        sp = ""
    return f"{genus} {sp}".strip()


def group_from_section(section: str) -> str:
    s = norm(section)
    for k, g in GROUP_BY_SECTION:
        if k in s:
            return g
    return ""


class SpeciesIndex:
    def __init__(self):
        self.lock = threading.RLock()
        self.by_lat: dict[str, int] = {}
        self.by_key: dict[str, int] = {}
        self.keys_list: list[str] = []
        self.first: dict[str, list[tuple[tuple[str, ...], int]]] = {}
        self.meta: dict[int, tuple[str, str, str, str]] = {}
        self.genus_ids: dict[str, list[int]] = {}

    def load(self, db: Session):
        with self.lock:
            self.__init__()
            rows = db.execute(select(Species)).scalars().all()
            syns = db.execute(select(SpeciesSynonym)).scalars().all()
            by_sp: dict[int, list[str]] = {}
            for s in syns:
                by_sp.setdefault(s.species_id, []).append(s.synonym)
            for sp in rows:
                self._add(sp, by_sp.get(sp.id, []))

    def _add(self, sp: Species, syns: list[str]):
        self.meta[sp.id] = (sp.name_ru, sp.name_lat, sp.genus, sp.group_name)
        if sp.genus:
            self.genus_ids.setdefault(sp.genus.lower(), []).append(sp.id)
        ln = latin_norm(sp.name_lat)
        if ln:
            self.by_lat.setdefault(ln, sp.id)
        for name in [sp.name_ru] + syns:
            if not name:
                continue
            if re.search(r"[A-Za-z]{3}", name) and not re.search(r"[А-Яа-я]", name):
                l2 = latin_norm(name)
                if l2:
                    self.by_lat.setdefault(l2, sp.id)
                continue
            k = lemma_key(name)
            if not k:
                continue
            if k not in self.by_key:
                self.by_key[k] = sp.id
                self.keys_list.append(k)
                toks = tuple(k.split())
                self.first.setdefault(toks[0], []).append((toks, sp.id))
        for lst in self.first.values():
            lst.sort(key=lambda x: -len(x[0]))

    def add_species(self, sp: Species, syns: list[str] = ()):
        with self.lock:
            self._add(sp, list(syns))

    def resolve(self, ru: str = "", lat: str = "") -> tuple[int | None, str]:
        with self.lock:
            ln = latin_norm(lat) if lat else ""
            if ln and ln in self.by_lat:
                return self.by_lat[ln], "lat"
            if ru:
                k = lemma_key(ru)
                if k in self.by_key:
                    return self.by_key[k], "ru"
                if self.keys_list and len(k) >= 5:
                    best = process.extractOne(k, self.keys_list, scorer=fuzz.ratio, score_cutoff=90)
                    if best:
                        return self.by_key[best[0]], "fuzzy"
            if ln and " " not in ln and ln in self.genus_ids:
                ids = self.genus_ids[ln]
                if len(ids) == 1:
                    return ids[0], "genus"
            return None, ""

    def find_in_text(self, text: str) -> list[tuple[int, str]]:
        words = re.findall(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*", text or "")
        lm = [lemma_key(w) for w in words]
        out, i = [], 0
        with self.lock:
            while i < len(lm):
                cands = self.first.get(lm[i])
                hit = None
                if cands:
                    for toks, sid in cands:
                        if tuple(lm[i : i + len(toks)]) == toks:
                            hit = (toks, sid)
                            break
                if hit:
                    out.append((hit[1], " ".join(words[i : i + len(hit[0])])))
                    i += len(hit[0])
                else:
                    i += 1
        return out

    def expand_query(self, q: str) -> set[int]:
        q = (q or "").strip()
        if not q:
            return set()
        ids: set[int] = set()
        with self.lock:
            lat = bool(re.fullmatch(r"[A-Za-z .×\-]+", q))
            if lat:
                ln = latin_norm(q)
                if ln in self.by_lat:
                    ids.add(self.by_lat[ln])
                g = ln.split()[0] if ln else ""
                if g and (" " not in ln):
                    ids.update(self.genus_ids.get(g, []))
                if not ids and ln:
                    ids.update(i for l, i in self.by_lat.items() if l.startswith(ln))
                return ids
            ql = set(lemmas(q))
            if not ql:
                return ids
            for k, sid in self.by_key.items():
                if ql <= set(k.split()):
                    ids.add(sid)
            if not ids and len(q) >= 4:
                for k, score, _ in process.extract(lemma_key(q), self.keys_list, scorer=fuzz.ratio, limit=5, score_cutoff=82):
                    ids.add(self.by_key[k])
            if len(ql) == 1:
                for sid in list(ids):
                    pass
        return ids


index = SpeciesIndex()


def upsert_species(db: Session, ru: str, lat: str, group: str = "", source: str = "auto") -> Species | None:
    ln = latin_norm(lat) if lat else ""
    if not ru and not ln:
        return None
    sid, _ = index.resolve(ru, lat)
    if sid:
        return db.get(Species, sid)
    if not ln and lat and re.search(r"[A-Za-z]{3}", lat):
        clean = re.sub(r"\s*[\(\[].*$", "", lat).strip()
        full_lat, genus = clean[:200], clean.split()[0].rstrip(".")
    else:
        genus = (ln.split()[0] if ln else "").capitalize()
        full_lat = " ".join(ln.split()).capitalize() if ln else ""
    sp = Species(name_ru=((ru or "").strip().lower() or full_lat)[:200], name_lat=full_lat[:200], genus=genus[:100],
                 group_name=group, rank="species" if " " in ln else ("genus" if ln else "species"), source=source)
    db.add(sp)
    db.flush()
    index.add_species(sp)
    return sp


def load_seed(db: Session, path: Path | None = None) -> int:
    path = path or ROOT / "reference" / "species_seed.csv"
    if not path.exists():
        return 0
    existing = {s.name_ru for s in db.execute(select(Species)).scalars()}
    n = 0
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            ru = row["name_ru"].strip()
            if not ru or ru in existing:
                continue
            lat = row.get("name_lat", "").strip()
            sp = Species(name_ru=ru, name_lat=lat, genus=lat.split()[0] if lat else "", group_name=row.get("group", "").strip(),
                         rank=row.get("rank", "species") or "species", source="seed")
            for syn in [s.strip() for s in (row.get("synonyms") or "").split("|") if s.strip()]:
                sp.synonyms.append(SpeciesSynonym(synonym=syn, norm=norm(syn)))
            db.add(sp)
            n += 1
    db.commit()
    return n
