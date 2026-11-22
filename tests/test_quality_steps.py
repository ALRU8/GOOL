from sqlalchemy import select

from app.pipeline import verify
from app.pipeline.llm_extract import load_examples, pick_examples


def _doc(db):
    from app.models import Document
    doc = db.query(Document).first()
    if doc is None:
        doc = Document(filename="t.pdf", storage_key="x", sha256="q", year=1999, status="done")
        db.add(doc)
        db.commit()
    return doc


def test_verify_applies_verdicts(client, monkeypatch):
    from app.db import SessionLocal
    from app.models import Record
    from app.pipeline.build import refresh_search
    with SessionLocal() as db:
        doc = _doc(db)
        good = Record(document_id=doc.id, page_no=7, year=1999, kind="phenology", species_text="черника", event="цветение", date_raw="09.06",
                      fragment="Цветение черники отмечено 09.06", confidence=0.7, method="llm", flags=[])
        bad = Record(document_id=doc.id, page_no=7, year=1999, kind="phenology", species_text="брусника", event="цветение", date_raw="09.07",
                     fragment="Цветение черники отмечено 09.06", confidence=0.7, method="llm", flags=[])
        for r in (good, bad):
            refresh_search(r)
            db.add(r)
        db.commit()
        gid, bid, did = good.id, bad.id, doc.id

    import json
    import re

    def fake_llm(prompt, system, model=None, max_tokens=0):
        assert "Цитата" in prompt
        out = []
        for i in map(int, re.findall(r"^ID (\d+)$", prompt, re.M)):
            out.append({"id": i, "ok": i != bid, "reason": "в цитате черника, а не брусника" if i == bid else ""})
        return json.dumps(out)
    monkeypatch.setattr(verify.yc, "llm", fake_llm)
    res = verify.verify_document(did)
    assert res["checked"] >= 2 and res["doubts"] == 1 and res["errors"] == 0
    with SessionLocal() as db:
        g, b = db.get(Record, gid), db.get(Record, bid)
        assert g.extra["ai_check"] == "ok" and g.confidence == 0.74
        assert b.extra["ai_check"] == "doubt" and "ai_doubt" in b.flags and b.confidence == 0.5 and "брусника" in b.extra["ai_reason"]
    assert verify.verify_document(did)["checked"] == 0
    rec = client_get(client, bid)
    assert any("сомневается" in t and "брусника" in t for t in rec["flags_text"]) and rec["ai_check"] == "doubt"


def client_get(client, rid):
    return client.get(f"/api/records/{rid}").json()


def test_mentions_go_last_and_can_be_excluded(client):
    from app.db import SessionLocal
    from app.models import Record
    from app.pipeline import species as sp
    from app.pipeline.build import refresh_search
    bear, _ = sp.index.resolve("бурый медведь")
    gogol, _ = sp.index.resolve("гоголь")
    assert bear and gogol
    with SessionLocal() as db:
        doc = _doc(db)
        rows = [
            Record(document_id=doc.id, page_no=21, year=1995, kind="event", species_text="гоголь", species_id=gogol, event="разоренные гнезда",
                   note="разорено медведем", fragment="кладки уничтожил медведь", confidence=0.94, method="llm"),
            Record(document_id=doc.id, page_no=21, year=2030, kind="count", species_text="медведь", species_id=bear, event="встреча",
                   fragment="Встречен медведь", confidence=0.8, method="llm"),
        ]
        for r in rows:
            refresh_search(r)
            db.add(r)
        db.commit()
    wide = client.get("/api/records", params={"species": "медведь", "page_size": 200, "sort": "year", "order": "desc"}).json()
    flags = [it["mention"] for it in wide["items"]]
    assert True in flags and False in flags
    assert flags == sorted(flags)
    assert wide["mentions_total"] == flags.count(True) >= 1
    assert all("гоголь" in it["species_text"] for it in wide["items"] if it["mention"])
    strict = client.get("/api/records", params={"species": "медведь", "only_species": "1", "page_size": 200}).json()
    assert strict["total"] == wide["total"] - wide["mentions_total"] and not any(it["mention"] for it in strict["items"])
    assert strict["mentions_total"] == wide["mentions_total"]
    page1 = client.get("/api/records", params={"species": "медведь", "page_size": 1, "sort": "year", "order": "desc"}).json()
    assert page1["items"][0]["mention"] is False
    csv = client.get("/api/records/export", params={"species": "медведь", "format": "csv"}).content.decode("utf-8-sig").split("\n")
    marks = [row.split(";")[0] for row in csv[1:] if row]
    assert marks == sorted(marks, key=lambda m: m == "упоминание") and "упоминание" in marks


def test_recommendations_by_search_history(client):
    from app.db import SessionLocal
    from app.models import Record, Species
    from app.pipeline import species as sp
    from app.pipeline.build import refresh_search
    with SessionLocal() as db:
        doc = _doc(db)
        a = Species(name_ru="рекомендуемый вид один", name_lat="Reccus unus", genus="Reccus", group_name="птицы", source="user")
        b = Species(name_ru="рекомендуемый вид два", name_lat="Reccus duo", genus="Reccus", group_name="птицы", source="user")
        c = Species(name_ru="рекомендуемый сосед", name_lat="Vicinus alius", genus="Vicinus", group_name="млекопитающие", source="user")
        db.add_all([a, b, c])
        db.commit()
        sp.index.load(db)
        from app.pipeline import toponyms as tp
        tid = tp.upsert_toponym(db, "озеро Тестовое").id
        db.commit()
        spec = [(a, 901, "начало цветения", "озеро Тестовое"), (a, 902, "начало цветения", "озеро Тестовое"), (b, 903, "", ""), (c, 901, "", "")]
        for s, page, ev, place in spec:
            r = Record(document_id=doc.id, page_no=page, year=2001, kind="phenology" if ev else "count", species_text=s.name_ru, species_id=s.id, group_name=s.group_name,
                       event=ev or "наблюдение", toponym_text=place, toponym_id=tid if place else None, lat=69.1 if place else None, lon=29.2 if place else None,
                       fragment="x", confidence=0.8, method="llm")
            refresh_search(r)
            db.add(r)
        db.commit()
    rec = client.post("/api/recommendations", json={"history": [{"species": "рекомендуемый вид один"}]}).json()["items"]
    labels = [i["label"] for i in rec]
    assert "Рекомендуемый вид два" in labels
    assert "Рекомендуемый сосед" in labels
    assert "Рекомендуемый вид один" not in labels
    genus = next(i for i in rec if i["label"] == "Рекомендуемый вид два")
    assert genus["params"] == {"species": "рекомендуемый вид два"} and "рода" in genus["reason"]
    assert any(i["params"].get("phenophase") == "начало цветения" for i in rec)
    assert any(i["params"].get("toponym") == "озеро Тестовое" for i in rec)
    again = client.post("/api/recommendations", json={"history": [{"species": "рекомендуемый вид два"}, {"species": "рекомендуемый вид один"}]}).json()["items"]
    assert all(i["params"] != {"species": "рекомендуемый вид два"} and i["params"] != {"species": "рекомендуемый вид один"} for i in again)
    assert client.post("/api/recommendations", json={"history": []}).json() == {"items": []}
    assert client.post("/api/recommendations", json={"history": [{"species": "нет такого вида xyz"}]}).json() == {"items": []}
    place = client.post("/api/recommendations", json={"history": [{"toponym": "озеро Тестовое"}]}).json()["items"]
    assert any(i["label"] == "Рекомендуемый вид один" for i in place)


def test_learning_examples_from_expert_edits(client):
    from app.db import SessionLocal
    from app.models import Record
    from app.pipeline.build import refresh_search
    with SessionLocal() as db:
        doc = _doc(db)
        r = Record(document_id=doc.id, page_no=9, year=1999, kind="count", species_text="лось", event="след", date_raw="12.03", value=2, unit="особей",
                   fragment="Встречены следы двух лосей 12.03", topic="Млекопитающие", method="manual", verified=True, confidence=0.97)
        refresh_search(r)
        db.add(r)
        db.commit()
        ex = load_examples(db)
    assert "Млекопитающие" in ex and "следы двух лосей" in ex["Млекопитающие"][0] and '"unit": "особей"' in ex["Млекопитающие"][0]
    text = pick_examples(ex, "Млекопитающие")
    assert text.count("Текст:") >= 1
    st = client.get("/api/quality").json()
    assert st["learning_examples"] >= 1


def test_gbif_refine(client, monkeypatch):
    from app import taxonomy
    from app.db import SessionLocal
    from app.models import Species
    calls = []

    def fake(name):
        calls.append(name)
        if name == "Ledum palustre":
            return {"matchType": "EXACT", "rank": "SPECIES", "confidence": 98, "canonicalName": "Rhododendron tomentosum", "genus": "Rhododendron",
                    "kingdom": "Plantae", "phylum": "Tracheophyta", "class": "Magnoliopsida"}
        return {"matchType": "NONE"}
    monkeypatch.setattr(taxonomy, "_http_get", fake)
    assert client.post("/api/species/gbif", json={}).status_code == 400 and not calls
    with SessionLocal() as db:
        s = Species(name_ru="тестовый багульник", name_lat="Ledum palustre", genus="Ledum", group_name="", source="auto")
        s2 = Species(name_ru="неизвестный вид", name_lat="Xxxx yyyy", genus="Xxxx", group_name="", source="auto")
        db.add_all([s, s2])
        db.commit()
    res = client.post("/api/species/gbif", json={"accept_external": True, "limit": 100}).json()
    assert res["matched"] >= 1 and res["renamed"] >= 1 and "Xxxx yyyy" in res["not_found"]
    with SessionLocal() as db:
        s = db.execute(select(Species).where(Species.name_ru == "тестовый багульник")).scalar_one()
        assert s.name_lat == "Rhododendron tomentosum" and s.genus == "Rhododendron" and s.group_name == "сосудистые растения" and s.taxon_status == "gbif"
        assert any(x.synonym == "Ledum palustre" for x in s.synonyms)
    n = len(calls)
    client.post("/api/species/gbif", json={"accept_external": True, "limit": 100})
    assert "Ledum palustre" not in calls[n:]
    from app.taxonomy import group_of
    assert group_of({"kingdom": "Animalia", "class": "Aves"}) == "птицы" and group_of({"kingdom": "Fungi", "class": "Lecanoromycetes"}) == "лишайники"
    assert group_of({"kingdom": "Plantae", "phylum": "Bryophyta"}) == "мхи"

