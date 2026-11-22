from __future__ import annotations

from sqlalchemy import inspect, text

from .db import SessionLocal, engine
from .models import Base
from .pipeline import species as sp
from .pipeline import toponyms as tp


def ensure_columns() -> None:
    insp = inspect(engine)
    with engine.begin() as c:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {col["name"] for col in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}'
                if col.server_default is not None:
                    ddl += f" DEFAULT {col.server_default.arg.text if hasattr(col.server_default.arg, 'text') else col.server_default.arg!r}"
                c.execute(text(ddl))


def backfill() -> None:
    from sqlalchemy import or_, select

    from .models import Record
    from .pipeline.dates import day_of_year
    from .pipeline.units import ALL_TOPICS, normalize_unit, topic_of
    from .textutil import search_blob

    with SessionLocal() as db:
        while True:
            rows = db.execute(select(Record).where(or_(
                (Record.doy.is_(None) & Record.date.is_not(None)),
                (Record.unit_raw == "") & (Record.unit != ""),
                (Record.topic.not_in(ALL_TOPICS)),
                (Record.species_norm == "") & ((Record.species_text != "") | (Record.species_lat_text != "")),
            )).limit(2000)).scalars().all()
            if not rows:
                break
            for r in rows:
                if r.doy is None and r.date is not None:
                    r.doy = day_of_year(r.date)
                if not r.unit_raw and r.unit:
                    r.unit_raw = r.unit
                    r.unit = normalize_unit(r.unit)
                r.topic = topic_of(r.section) if r.section else "—"
                if not r.species_norm and (r.species_text or r.species_lat_text):
                    r.species_norm = search_blob(r.species_text, r.species_lat_text)
            db.commit()


def init() -> None:
    if engine.dialect.name == "postgresql":
        with engine.begin() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(engine)
    ensure_columns()
    with SessionLocal() as db:
        sp.load_seed(db)
        tp.load_seed(db)
        sp.index.load(db)
        tp.index.load(db)
    backfill()
