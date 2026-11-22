from __future__ import annotations

import csv
import re
import threading
from pathlib import Path

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ROOT
from ..models import Toponym
from ..textutil import norm

PREFIX = re.compile(r"^(о\.|оз\.|р\.|г\.|пос\.|п\.|гора|озеро|река|остров|урочище|болото|остров)\s+", re.I)
COORD = re.compile(r"N\s*(\d{2}[.,]\d{3,})\s*[,;]?\s*[EЕ][O0О]?\s*(\d{2}[.,]\d{3,})")


def key(s: str) -> str:
    s = norm(s)
    s = PREFIX.sub("", s)
    return s.replace("-", " ").strip()


def extract_coords(text: str) -> tuple[float, float] | None:
    m = COORD.search(text or "")
    if not m:
        return None
    return float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))


class TopoIndex:
    def __init__(self):
        self.lock = threading.RLock()
        self.by_key: dict[str, int] = {}
        self.meta: dict[int, tuple[str, float | None, float | None]] = {}

    def load(self, db: Session):
        with self.lock:
            self.by_key.clear()
            self.meta.clear()
            for t in db.execute(select(Toponym)).scalars():
                self.add(t)

    def add(self, t: Toponym):
        with self.lock:
            self.meta[t.id] = (t.name, t.lat, t.lon)
            for nm in [t.name] + list(t.aliases or []):
                k = key(nm)
                if k:
                    self.by_key.setdefault(k, t.id)

    def resolve(self, text: str) -> int | None:
        k = key(text)
        if not k:
            return None
        with self.lock:
            if k in self.by_key:
                return self.by_key[k]
            for kk, tid in self.by_key.items():
                if len(kk) >= 5 and re.search(r"(?<!\w)" + re.escape(kk) + r"(?!\w)", k):
                    return tid
            if len(k) >= 6 and self.by_key:
                best = process.extractOne(k, list(self.by_key), scorer=fuzz.ratio, score_cutoff=88)
                if best:
                    return self.by_key[best[0]]
        return None


index = TopoIndex()


def load_seed(db: Session, path: Path | None = None) -> int:
    path = path or ROOT / "reference" / "toponyms_seed.csv"
    if not path.exists():
        return 0
    have = {t.name for t in db.execute(select(Toponym)).scalars()}
    n = 0
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            name = row["name"].strip()
            if not name or name in have:
                continue
            lat = float(row["lat"]) if row.get("lat") else None
            lon = float(row["lon"]) if row.get("lon") else None
            db.add(Toponym(name=name, norm=key(name), lat=lat, lon=lon, kind=row.get("kind", ""),
                           aliases=[a for a in (row.get("aliases") or "").split("|") if a], source=row.get("source", "seed")))
            n += 1
    db.commit()
    return n


def upsert_toponym(db: Session, name: str) -> Toponym | None:
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        return None
    tid = index.resolve(name)
    if tid:
        return db.get(Toponym, tid)
    t = Toponym(name=name, norm=key(name), kind="", aliases=[], source="auto")
    db.add(t)
    db.flush()
    index.add(t)
    return t
