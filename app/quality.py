from __future__ import annotations

import csv
from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import ROOT
from .db import get_db
from .models import Document, Page, Record
from .serialize import FLAG_TEXT
from .textutil import norm

router = APIRouter(prefix="/api")
GOLD = ROOT / "eval" / "gold.csv"


def load_gold() -> list[dict]:
    if not GOLD.exists():
        return []
    with open(GOLD, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _match(g: dict, r: Record) -> bool:
    ge, gs = norm(g["event"]), norm(g["species"])
    ok_name = (ge and ge[:18] in norm(r.event)) or (gs and gs[:8] in norm(r.species_text + " " + r.event))
    if not ok_name:
        return False
    if g.get("date"):
        return bool(r.date and r.date.isoformat() == g["date"])
    return True


def _name_only(g: dict, r: Record) -> bool:
    ge, gs = norm(g["event"]), norm(g["species"])
    return bool((ge and ge[:18] in norm(r.event)) or (gs and gs[:8] in norm(r.species_text + " " + r.event)))


@router.get("/quality")
def quality(db: Session = Depends(get_db)):
    gold = load_gold()
    matched, missed, wrong, checked = 0, [], [], 0
    pages_checked: set[tuple[int, int]] = set()
    for g in gold:
        did = db.scalar(select(Document.id).where(Document.year == int(g["doc_year"]), Document.status == "done").limit(1))
        if not did:
            continue
        checked += 1
        page = int(g["page"])
        pages_checked.add((did, page))
        cands = db.execute(select(Record).where(Record.document_id == did, Record.page_no == page)).scalars().all()
        if any(_match(g, r) for r in cands):
            matched += 1
            continue
        near = next((r for r in cands if _name_only(g, r)), None)
        item = {"gold": g, "found": None if not near else {"event": near.event, "date": near.date.isoformat() if near.date else None, "species": near.species_text}}
        (wrong if near else missed).append(item)
    on_pages = sum(db.scalar(select(func.count(Record.id)).where(Record.document_id == d, Record.page_no == p)) or 0 for d, p in pages_checked)
    flags, buckets = Counter(), Counter()
    for fl, c in db.execute(select(Record.flags, Record.confidence)).all():
        flags.update(fl or [])
        buckets["высокая (>=0.8)" if c >= 0.8 else "средняя (0.5-0.8)" if c >= 0.5 else "низкая (<0.5)"] += 1
    ai_checked = ai_doubts = 0
    for (extra,) in db.execute(select(Record.extra).where(Record.method == "llm")).all():
        verdict = (extra or {}).get("ai_check")
        ai_checked += 1 if verdict else 0
        ai_doubts += 1 if verdict == "doubt" else 0
    bad_pages = db.execute(select(Page.document_id, Page.page_no, Page.source, Page.note)
                           .where(Page.source.in_(["empty", "error"]) | (Page.status == "llm_error")).limit(50)).all()
    bad_docs = db.execute(select(Document.filename, Document.error).where(Document.status == "error")).all()
    return {
        "gold_total": len(gold), "gold_checked": checked, "matched": matched,
        "recall": (matched / checked) if checked else None,
        "precision": (matched / on_pages) if on_pages else None,
        "missed": missed[:30], "wrong": wrong[:30],
        "records_total": db.scalar(select(func.count(Record.id))) or 0, "confidence": dict(buckets),
        "learning_examples": db.scalar(select(func.count(Record.id)).where(((Record.method == "manual") | (Record.verified.is_(True))), Record.fragment != "")) or 0,
        "ai_checked": ai_checked,
        "ai_doubts": ai_doubts,
        "flags": [{"flag": f, "text": FLAG_TEXT.get(f, f), "count": n} for f, n in flags.most_common()],
        "problem_pages": [{"document_id": a, "page": b, "source": c, "note": n} for a, b, c, n in bad_pages],
        "unsupported_documents": [{"filename": f, "error": e} for f, e in bad_docs],
    }
