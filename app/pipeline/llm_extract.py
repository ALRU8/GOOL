from __future__ import annotations

import logging

from .. import yc
from ..config import settings

log = logging.getLogger("llm_extract")

SYSTEM = """Ты — помощник-эколог. Из фрагмента «Летописи природы» заповедника «Пасвик» ты извлекаешь НАБЛЮДЕНИЯ в виде JSON.
Верни ТОЛЬКО JSON-массив объектов (без пояснений). Если наблюдений нет — верни [].

Поля объекта (все строковые, кроме value):
- kind: "phenology" (фенофаза или сезонное явление с датой: цветение, прилёт, зеленение, ледостав и т.п.), "count" (численность/обилие/количество особей, находки), "event" (прочее наблюдение: встреча, поведение, поражение болезнью и т.п.)
- species_ru: русское название вида или группы так, как в тексте ("" если не указан)
- species_lat: латинское название, если приведено в тексте ("" если нет)
- event: что наблюдали, кратко. Для фенологии — название фенофазы (например "начало цветения", "массовое цветение", "плодоношение", "зеленение", "прилёт"); если в таблице есть шапка с названием фенофазы — используй её. Не пиши просто "фенофаза".
- date: дата как в тексте ("12.05.2005", "12.05", "3 июня", "21.01-11.03"), "" если нет
- toponym: место (топоним) если указано, иначе ""
- habitat: местообитание/биотоп (например "сосняк лишайниковый"), иначе ""
- value: число (десятичная точка), null если нет
- unit: единица измерения ("особей", "шт.", "%", "г/м2", "самцов"), иначе ""
- methodology: методика учёта, маршрут, стационар, пробная площадка или способ измерения, если указаны (например "маршрутный учёт, 10 км", "площадка у сосны № 9", "ловушко-сутки"), иначе ""
- lat, lon: широта и долгота в десятичных градусах, если координаты прямо указаны в тексте для этого наблюдения (например "N 69.37273 E 29.89055" → 69.37273 и 29.89055), иначе null
- observer: ФИО наблюдателя/автора в тексте, иначе ""
- note: важное примечание, иначе ""
- ambiguous: true, если в строке таблицы несколько дат/чисел (колонки по разным годам) и по тексту НЕ ясно, какая из них относится к какому году; в этом случае date = первая дата строки; иначе false
- fragment: ТОЧНАЯ цитата из текста (до 250 символов), подтверждающая запись. Копируй дословно, ничего не исправляя.

Правила: извлекай только то, что явно сказано; ничего не выдумывай и не додумывай. Не извлекай метеоданные,
списки литературы, общие рассуждения без конкретных видов/событий. Одна запись = один вид + одно событие/значение.
Если в таблице несколько видов — отдельная запись на каждую строку.
В тексте страницы после строки «=== ТАБЛИЦЫ И РИСУНКИ (ИИ) ===» идут таблицы и описания рисунков, прочитанные по изображению страницы: они точнее OCR-текста. Если одни и те же данные есть и в OCR-тексте, и в таблице ИИ, бери таблицу и НЕ дублируй запись. Метеорологические таблицы (температура, осадки, снег) не извлекай. Текст может содержать ошибки распознавания — не исправляй их в fragment."""


def build_prompt(year, section: str, page_no: int, chunk: str, prev_tail: str = "", examples: str = "") -> str:
    ctx = f"Год тома: {year or 'неизвестен'}\nРаздел: {section or 'неизвестен'}\nСтраница: {page_no}\n"
    if examples:
        ctx += f"Примеры записей, проверенных или исправленных экспертом (учитывай их формулировки и исправления):\n{examples}\n"
    if prev_tail:
        ctx += f"Контекст — конец предыдущей страницы (только для понимания шапки таблицы, не извлекай из него):\n{prev_tail}\n"
    return f"{ctx}\nТЕКСТ:\n{chunk}\n\nВерни JSON-массив."


def extract_chunk(year, section: str, page_no: int, chunk: str, prev_tail: str = "", model: str | None = None, examples: str = "") -> list[dict]:
    prompt = build_prompt(year, section, page_no, chunk, prev_tail, examples)
    raw = yc.llm(prompt, SYSTEM, model or settings.llm_model_extract, max_tokens=6000)
    try:
        data = yc.parse_json_block(raw)
    except ValueError:
        log.warning("bad json on page %s", page_no)
        raise
    if isinstance(data, dict):
        data = data.get("records") or data.get("observations") or [data]
    out = []
    for it in data:
        if isinstance(it, dict):
            out.append(it)
    return out


def worth_llm(text: str, has_species: bool) -> bool:
    import re
    if len(text.strip()) < 250:
        return False
    low = text.lower()
    if low.count("литератур") > 3 and len(re.findall(r"\(\d{4}\)|\d{4}\.\s", text)) > 15:
        return False
    digits = sum(c.isdigit() for c in text)
    if digits / max(1, len(text)) > 0.55:
        return False
    has_date = bool(re.search(r"\b\d{1,2}[.]\d{2}([.]\d{2,4})?\b|\b\d{1,2}\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)", low))
    has_latin = bool(re.search(r"\b[A-Z][a-z]{3,}\s+[a-z]{4,}\b", text))
    has_num_unit = bool(re.search(r"\d+\s*(особ|шт|экз|самц|самк|пар|гнезд|%|г/м|кг|ос\.)", low))
    return (has_date or has_num_unit) and (has_species or has_latin)


def load_examples(db, limit: int = 120) -> dict[str, list[str]]:
    import json

    from sqlalchemy import select

    from ..models import Record
    rows = db.execute(select(Record).where(((Record.method == "manual") | (Record.verified.is_(True))), Record.fragment != "")
                      .order_by((Record.method == "manual").desc(), Record.id.desc()).limit(limit)).scalars().all()
    by_topic: dict[str, list[str]] = {}
    for r in rows:
        obj = {"kind": r.kind, "species_ru": r.species_text, "species_lat": r.species_lat_text, "event": r.event, "date": r.date_raw,
               "toponym": r.toponym_text, "value": r.value, "unit": r.unit_raw or r.unit, "habitat": r.habitat, "methodology": r.methodology}
        obj = {k: v for k, v in obj.items() if v not in ("", None)}
        line = f"Текст: «{r.fragment[:220]}»\n=> {json.dumps(obj, ensure_ascii=False)}"
        by_topic.setdefault(r.topic or "", []).append(line)
    return by_topic


def pick_examples(by_topic: dict[str, list[str]], topic: str, n: int = 4) -> str:
    own = by_topic.get(topic, [])
    others = [l for t, ls in by_topic.items() if t != topic for l in ls]
    return "\n".join((own + others)[:n])
