from __future__ import annotations

from .models import Document, Record
from .pipeline import species as sp

FLAG_TEXT = {
    "year_inferred": "год не указан в тексте, взят из тома",
    "fragment_not_found": "цитата не найдена на странице дословно",
    "toponym_not_in_catalog": "место не из каталога (нет координат)",
    "species_auto_added": "вид добавлен в справочник автоматически",
    "species_unresolved": "вид не найден в справочнике",
    "species_fuzzy": "вид сопоставлен приблизительно",
    "unparsed_date": "дата не распознана",
    "bad_date": "некорректная дата",
    "date_year_differs": "год даты отличается от года тома",
    "no_unit": "не указана единица измерения",
    "no_fragment": "нет исходного фрагмента",
    "ambiguous_columns": "неоднозначная таблица: неясно, к какому году относится дата",
    "multi_year_dates": "даты разных лет (границы многолетнего ряда или период из нескольких лет)",
    "ai_doubt": "проверяющая модель сомневается в записи",
    "not_observed_in_year": "в отчётном году не наблюдалось",
}


def rec_dict(r: Record, doc: Document | None = None) -> dict:
    meta = sp.index.meta.get(r.species_id) if r.species_id else None
    doc = doc or r.document
    return {
        "id": r.id, "document_id": r.document_id, "doc_title": doc.title if doc else "", "doc_year": doc.year if doc else None,
        "volume": doc.volume if doc else None,
        "page": r.page_no, "year": r.year, "kind": r.kind,
        "species_text": r.species_text, "species_lat_text": r.species_lat_text,
        "species_id": r.species_id, "species_ref": meta[0] if meta else "", "species_lat": meta[1] if meta else "",
        "genus": meta[2] if meta else "", "group": r.group_name or (meta[3] if meta else ""),
        "event": r.event, "date": r.date.isoformat() if r.date else None, "date_raw": r.date_raw,
        "date_end": r.date_end.isoformat() if r.date_end else None, "date_precision": r.date_precision or ("day" if r.date else ""),
        "toponym": r.toponym_text, "toponym_id": r.toponym_id, "lat": r.lat, "lon": r.lon, "habitat": r.habitat,
        "value": r.value, "value_raw": r.value_raw, "unit": r.unit, "unit_raw": r.unit_raw, "topic": r.topic if r.topic != "—" else "", "methodology": r.methodology, "observer": r.observer, "observer_source": r.observer_source,
        "note": r.note, "user_note": r.user_note or "", "user_note_at": r.user_note_at.isoformat() if r.user_note_at else None, "fragment": r.fragment, "section": r.section, "extra": r.extra or {},
        "confidence": r.confidence, "flags": r.flags or [], "flags_text": [(FLAG_TEXT.get(f, f) + (": " + (r.extra or {}).get("ai_reason", "") if f == "ai_doubt" and (r.extra or {}).get("ai_reason") else "")) for f in (r.flags or [])],
        "ai_check": (r.extra or {}).get("ai_check", ""),
        "method": r.method, "verified": r.verified,
    }


def doc_dict(d: Document) -> dict:
    return {
        "id": d.id, "kind": d.kind or "pdf", "filename": d.filename, "title": d.title, "year": d.year, "volume": d.volume, "status": d.status,
        "stage": d.stage, "error": d.error, "pages_total": d.pages_total, "pages_done": d.pages_done, "pages_ocr": d.pages_ocr, "pages_vision": d.pages_vision,
        "records_count": d.records_count, "created_at": d.created_at.isoformat() if d.created_at else None,
        "finished_at": d.finished_at.isoformat() if d.finished_at else None,
    }
