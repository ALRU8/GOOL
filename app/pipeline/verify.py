from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from .. import yc
from ..config import settings
from ..db import SessionLocal
from ..models import Record

log = logging.getLogger("verify")
BATCH = 12
THRESHOLD = 0.9

SYSTEM = """Ты — строгий проверяющий научных записей. Тебе дают цитаты из «Летописи природы» заповедника и поля, извлеченные из каждой цитаты.
Для каждой записи определи, ПОДТВЕРЖДАЮТСЯ ли поля цитатой: вид, событие, дата, значение с единицей, место.
Правила: судишь только по цитате; не требуй того, чего нет среди извлечённых полей; если поле расходится с цитатой (другой вид, другая дата, другое число, другое событие) — запись не подтверждена.
Ответ строго JSON-массив без пояснений: [{"id": число, "ok": true или false, "reason": "кратко, что не подтверждается; пустая строка, если ok"}]"""


def _fields(r: Record) -> str:
    parts = [f"вид={r.species_text or r.species_lat_text or '-'}", f"событие={r.event or '-'}", f"дата={r.date_raw or '-'}"]
    if r.value is not None:
        parts.append(f"значение={r.value} {r.unit}".strip())
    if r.toponym_text:
        parts.append(f"место={r.toponym_text}")
    return "; ".join(parts)


def check_batch(recs: list[Record]) -> dict[int, tuple[bool, str]]:
    body = "\n\n".join(f"ID {r.id}\nЦитата: «{(r.fragment or '')[:450]}»\nПоля: {_fields(r)}" for r in recs)
    raw = yc.llm(body + "\n\nВерни JSON-массив.", SYSTEM, settings.llm_model_cheap, max_tokens=2500)
    out: dict[int, tuple[bool, str]] = {}
    for it in yc.parse_json_block(raw):
        try:
            out[int(it["id"])] = (bool(it.get("ok")), str(it.get("reason") or "")[:200])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def apply_verdict(r: Record, ok: bool, reason: str) -> None:
    extra = dict(r.extra or {})
    if ok:
        extra["ai_check"] = "ok"
        r.confidence = min(0.97, round(r.confidence + 0.04, 2))
    else:
        extra["ai_check"] = "doubt"
        extra["ai_reason"] = reason or "поля расходятся с цитатой"
        r.confidence = max(0.05, round(r.confidence - 0.2, 2))
        if "ai_doubt" not in (r.flags or []):
            r.flags = list(r.flags or []) + ["ai_doubt"]
    r.extra = extra


def verify_document(doc_id: int) -> dict:
    with SessionLocal() as db:
        recs = [r for r in db.execute(select(Record).where(Record.document_id == doc_id, Record.method == "llm", Record.fragment != "",
                                                              Record.confidence < THRESHOLD)).scalars()
                if "ai_check" not in (r.extra or {})]
        by_page: dict[int, list[int]] = {}
        for r in recs:
            by_page.setdefault(r.page_no, []).append(r.id)
        batches = [ids[i : i + BATCH] for ids in by_page.values() for i in range(0, len(ids), BATCH)]
    checked = doubts = errors = 0

    def run(ids: list[int]):
        with SessionLocal() as s:
            rs = list(s.execute(select(Record).where(Record.id.in_(ids))).scalars())
            return check_batch(rs)

    with ThreadPoolExecutor(max_workers=settings.workers) as ex:
        results = list(ex.map(lambda ids: (ids, _safe(run, ids)), batches))
    with SessionLocal() as db:
        for ids, verdicts in results:
            if verdicts is None:
                errors += 1
                continue
            for r in db.execute(select(Record).where(Record.id.in_(ids))).scalars():
                if r.id in verdicts:
                    ok, reason = verdicts[r.id]
                    apply_verdict(r, ok, reason)
                    checked += 1
                    doubts += 0 if ok else 1
        db.commit()
    return {"checked": checked, "doubts": doubts, "errors": errors}


def _safe(fn, arg):
    try:
        return fn(arg)
    except Exception as e:
        log.warning("verify batch failed: %s", e)
        return None
