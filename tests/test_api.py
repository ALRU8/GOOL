import io

import pymupdf


def make_pdf(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), text, fontname="helv", fontsize=11)
    return doc.tobytes()


def test_startup_clears_stuck_stage(client):
    from app import worker
    from app.db import SessionLocal
    from app.models import Document
    with SessionLocal() as db:
        d = Document(filename="stuck.pdf", storage_key="x", sha256="stuck", status="done", stage="проверка записей")
        db.add(d)
        db.commit()
        did = d.id
    worker.recover()
    with SessionLocal() as db:
        assert db.get(Document, did).stage == ""


def test_health_and_config(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    cfg = client.get("/api/config").json()
    assert cfg["yandex_configured"] is False


def test_seed_loaded(client):
    r = client.get("/api/species", params={"q": "берёза"}).json()
    assert any(s["name_lat"] == "Betula pubescens" for s in r)
    r = client.get("/api/suggest/species", params={"q": "Betula"}).json()
    assert r


def test_upload_invalid_files(client):
    r = client.post("/api/documents", files=[("files", ("broken.pdf", io.BytesIO(b"not a pdf"), "application/pdf"))]).json()
    assert r[0]["status"] == "error" and "повреждён" in r[0]["error"]
    r = client.post("/api/documents", files=[("files", ("empty.pdf", io.BytesIO(b""), "application/pdf"))]).json()
    assert r[0]["status"] == "error"
    r = client.post("/api/documents", files=[("files", ("notes.txt", io.BytesIO(b"abc"), "text/plain"))]).json()
    assert r[0]["status"] == "error" and "формат" in r[0]["error"]
    r = client.post("/api/documents", files=[("files", ("old.doc", io.BytesIO(b"abc"), "application/msword"))]).json()
    assert r[0]["status"] == "error" and ".docx" in r[0]["error"]
    r = client.post("/api/documents", files=[("files", ("broken.docx", io.BytesIO(b"PK"), "application/octet-stream"))]).json()
    assert r[0]["status"] == "error" and "повреждён" in r[0]["error"]


def test_process_text_pdf_and_search(client):
    import time
    pdf = make_pdf("Calendar test page without tables.")
    r = client.post("/api/documents", files=[("files", ("Test (2001 god).pdf", io.BytesIO(pdf), "application/pdf"))], data={"year": "2001"}).json()
    doc_id = r[0]["id"]
    for _ in range(60):
        d = [x for x in client.get("/api/documents").json() if x["id"] == doc_id][0]
        if d["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert d["status"] == "done", d
    assert d["year"] == 2001
    page = client.get(f"/api/documents/{doc_id}/pages/1").json()
    assert "Calendar test page" in page["text"]
    img = client.get(f"/api/documents/{doc_id}/pages/1/image", params={"hl": "Calendar test"})
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    assert client.get(f"/api/documents/{doc_id}/pages/9/image").status_code == 404
    r2 = client.post("/api/documents", files=[("files", ("copy.pdf", io.BytesIO(pdf), "application/pdf"))]).json()
    assert r2[0]["id"] == doc_id


def test_records_crud_and_export(client):
    from app.db import SessionLocal
    from app.models import Document, Record
    from app.pipeline.build import refresh_search
    import datetime as dt
    with SessionLocal() as db:
        doc = db.query(Document).first()
        from app.pipeline import species as sp
        sid, _ = sp.index.resolve("берёза пушистая")
        assert sid
        r = Record(document_id=doc.id, page_no=1, year=2005, kind="phenology", species_text="берёза пушистая", species_id=sid, group_name="сосудистые растения", event="начало зеленения листьев",
                   date=dt.date(2005, 5, 12), date_raw="12.05.2005", toponym_text="остров Варлама", lat=69.1, lon=29.2,
                   observer="Иванов И. И.", fragment="Calendar test", confidence=0.9, method="llm")
        refresh_search(r)
        db.add(r)
        db.commit()
        rid = r.id
    res = client.get("/api/records", params={"species": "Betula pubescens"}).json()
    assert res["total"] == 1 and res["items"][0]["date"] == "2005-05-12"
    assert client.get("/api/records", params={"species": "берёза пушистая", "year_from": 2006}).json()["total"] == 0
    assert client.get("/api/records", params={"observer": "Иванов"}).json()["total"] == 1
    assert client.get("/api/records", params={"phenophase": "зеленение", "year_from": 2000, "year_to": 2010}).json()["total"] == 1
    assert client.get("/api/records", params={"toponym": "Варлама"}).json()["total"] == 1
    ph = client.get("/api/phenology", params={"species": "берёза пушистая"}).json()["series"]
    assert ph and ph[0]["points"][0]["year"] == 2005
    pl = client.get("/api/places/species", params={"toponym": "остров Варлама"}).json()
    assert pl["toponym"]["lat"] == 69.1 and pl["species"]
    x = client.get("/api/records/export", params={"species": "берёза"})
    assert x.status_code == 200 and x.content[:2] == b"PK"
    c = client.get("/api/records/export", params={"species": "берёза", "format": "csv"})
    assert "Betula pubescens" in c.content.decode("utf-8-sig")
    p = client.patch(f"/api/records/{rid}", json={"value": 15, "unit": "особей"}).json()
    assert p["value"] == 15 and p["method"] == "manual" and p["verified"] is True
    assert client.get("/api/quality").status_code == 200
    q = client.post("/api/saved-queries", json={"name": "берёза", "params": {"species": "берёза"}}).json()
    assert client.get("/api/saved-queries").json()[0]["name"] == "берёза"
    client.delete(f"/api/saved-queries/{q['id']}")
    assert client.delete(f"/api/records/{rid}").json() == {"ok": True}


def test_docx_upload_end_to_end(client, tmp_path):
    import time
    from tests.test_office import make_docx
    p = tmp_path / "Календарь 2021.docx"
    make_docx(p)
    r = client.post("/api/documents", files=[("files", (p.name, p.read_bytes(), "application/octet-stream"))], data={"year": "2021"}).json()
    assert r[0]["kind"] == "docx", r
    doc_id = r[0]["id"]
    for _ in range(60):
        d = [x for x in client.get("/api/documents").json() if x["id"] == doc_id][0]
        if d["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert d["status"] == "done", d
    tabs = client.get("/api/tables", params={"doc_id": doc_id}).json()
    assert len(tabs) == 1 and tabs[0]["n_rows"] == 3 and tabs[0]["source"] == "file"
    page = client.get(f"/api/documents/{doc_id}/pages/1").json()
    assert page["document"]["kind"] == "docx" and "брусники" in page["text"]
    assert client.get(f"/api/documents/{doc_id}/pages/1/image").status_code == 404
    assert client.get(f"/api/documents/{doc_id}/file").status_code == 200
    assert client.post(f"/api/documents/{doc_id}/pages/1/ai").status_code == 400
    cov = client.get(f"/api/documents/{doc_id}/coverage").json()
    assert cov["totals"].get("tables") == 1


def test_new_filters_and_stats(client):
    from app.db import SessionLocal
    from app.models import Document, Record
    from app.pipeline.build import refresh_search
    from app.pipeline.dates import day_of_year
    from app.pipeline import species as sp
    import datetime as dt
    sid, _ = sp.index.resolve("черника")
    with SessionLocal() as db:
        doc = db.query(Document).first()
        for y, day in [(2001, 10), (2003, 8), (2005, 6), (2007, 4), (2009, 2), (2011, 1), (2013, 1), (2015, 1), (2017, 1)]:
            dd = dt.date(y, 6, day)
            r = Record(document_id=doc.id, page_no=1, year=y, kind="phenology", species_text="черника", species_id=sid, event="начало цветения",
                       date=dd, date_precision="day", doy=day_of_year(dd), unit="особей", unit_raw="экз.", topic="Флора и растительность",
                       methodology="маршрут Калкупя", value=3, confidence=0.9, method="llm")
            refresh_search(r)
            db.add(r)
        db.commit()
    assert client.get("/api/records", params={"species": "черника", "md_from": "01.06", "md_to": "05.06"}).json()["total"] >= 3
    assert client.get("/api/records", params={"species": "черника", "md_from": "20.06", "md_to": "10.07"}).json()["total"] == 0
    assert client.get("/api/records", params={"species": "черника", "md_from": "25.12", "md_to": "05.06"}).json()["total"] == 6
    assert client.get("/api/records", params={"topic": "Флора и растительность"}).json()["total"] >= 6
    assert client.get("/api/records", params={"unit": "особей", "has_value": "1"}).json()["total"] >= 6
    assert client.get("/api/records", params={"methodology": "Калкупя"}).json()["total"] >= 6
    st = client.get("/api/stats").json()
    assert any(t["value"] == "Флора и растительность" for t in st["topics"]) and any(u["value"] == "особей" for u in st["units"])
    s = client.get("/api/phenology", params={"species": "черника"}).json()["series"][0]["stats"]
    assert s["n"] == 9 and s["earliest"]["year"] == 2011 and s["latest"]["year"] == 2001
    assert s["trend_per_decade"] is not None and s["trend_per_decade"] < 0


def test_geocoder_catalog(client, monkeypatch):
    from app import geocode
    assert geocode.looks_like_place("остров Варлама") and geocode.looks_like_place("Янискоски")
    assert not geocode.looks_like_place("юг заповедника") and not geocode.looks_like_place("площадка у сосны № 9")
    calls = []

    def fake(params):
        calls.append(params["q"])
        return [{"lat": "69.05", "lon": "29.21"}] if "Тестовое" in params["q"] else []
    monkeypatch.setattr(geocode, "_http_get", fake)
    client.post("/api/toponyms", json={"name": "озеро Тестовое", "aliases": []})
    r = client.post("/api/toponyms/geocode", json={"limit": 40})
    assert r.status_code == 400 and not calls
    res = client.post("/api/toponyms/geocode", json={"accept_external": True, "limit": 40}).json()
    assert "озеро Тестовое" in res["found"] and calls
    t = [x for x in client.get("/api/toponyms").json() if x["name"] == "озеро Тестовое"][0]
    assert t["lat"] == 69.05 and t["source"] == "osm"
    n_calls = len(calls)
    client.post("/api/toponyms/geocode", json={"accept_external": True, "limit": 40})
    assert all("Тестовое" not in q for q in calls[n_calls:])


def test_user_note_lifecycle(client):
    from app.db import SessionLocal
    from app.models import Document, Record
    from app.pipeline.build import refresh_search
    from app.pipeline.ingest import restore_user_notes, stash_user_notes
    with SessionLocal() as db:
        doc = db.query(Document).first()
        r = Record(document_id=doc.id, page_no=3, year=2000, kind="event", species_text="лось", event="след", fragment="Следы лося на снегу у озера", confidence=0.9, method="llm")
        refresh_search(r)
        db.add(r)
        db.commit()
        rid, did = r.id, doc.id
    before = client.get(f"/api/records/{rid}").json()
    res = client.patch(f"/api/records/{rid}", json={"user_note": "  Проверил по полевому дневнику  "}).json()
    assert res["user_note"] == "Проверил по полевому дневнику" and res["user_note_at"]
    assert res["confidence"] == before["confidence"] and res["method"] == "llm" and res["verified"] is False and res["flags"] == before["flags"]
    assert client.get("/api/records", params={"q": "дневнику"}).json()["total"] == 1
    assert client.get("/api/records", params={"has_note": "1"}).json()["total"] == 1
    assert "Проверил по полевому дневнику" in client.get("/api/records/export", params={"format": "csv", "has_note": "1"}).content.decode("utf-8-sig")
    with SessionLocal() as db:
        assert stash_user_notes(db, did) == 1
        stash_user_notes(db, did)
        db.query(Record).filter(Record.id == rid).delete()
        new = Record(document_id=did, page_no=3, year=2000, kind="event", species_text="лось", event="след", fragment="Следы лося на снегу у озера.", confidence=0.8, method="llm")
        refresh_search(new)
        db.add(new)
        db.commit()
        nid = new.id
        assert restore_user_notes(db, did) == (1, 0)
    assert client.get(f"/api/records/{nid}").json()["user_note"] == "Проверил по полевому дневнику"
    assert client.patch(f"/api/records/{nid}", json={"user_note": ""}).json()["user_note"] == ""
    assert client.get("/api/records", params={"has_note": "1"}).json()["total"] == 0
