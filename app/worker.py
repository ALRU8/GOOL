from __future__ import annotations

import logging
import threading

from sqlalchemy import select, update

from .db import SessionLocal
from .models import Document
from .pipeline.ingest import process_document

log = logging.getLogger("worker")
_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop():
    while not _stop.is_set():
        doc_id = None
        with SessionLocal() as db:
            d = db.execute(select(Document).where(Document.status == "queued").order_by(Document.id)).scalars().first()
            if d:
                doc_id = d.id
        if doc_id is None:
            _stop.wait(2.0)
            continue
        log.info("processing document %s", doc_id)
        process_document(doc_id)


def recover():
    with SessionLocal() as db:
        db.execute(update(Document).where(Document.status == "processing").values(status="queued"))
        db.execute(update(Document).where(Document.status.in_(["done", "error"]), Document.stage != "").values(stage=""))
        db.commit()


def start():
    global _thread
    recover()
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="ingest-worker", daemon=True)
    _thread.start()


def stop():
    _stop.set()
