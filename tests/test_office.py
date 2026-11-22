import docx
from openpyxl import Workbook

from app.pipeline import office


def make_docx(path):
    d = docx.Document()
    d.add_heading("Флора и растительность", level=1)
    d.add_paragraph("Начало цветения брусники отмечено 16.06.2020 на острове Варлама.")
    d.add_paragraph("Таблица 1. Фенофазы")
    t = d.add_table(rows=3, cols=3)
    for r, row in enumerate([["Вид", "Фенофаза", "Дата"], ["брусника", "начало цветения", "16.06"], ["черника", "начало цветения", "09.06"]]):
        for c, v in enumerate(row):
            t.cell(r, c).text = v
    d.save(str(path))


def test_docx_pages(tmp_path):
    p = tmp_path / "a.docx"
    make_docx(p)
    ok, msg, n = office.validate(p, "docx")
    assert ok and n == 1
    pg = office.read_pages(p, "docx")[0]
    assert pg["text"].startswith("1. Флора и растительность")
    assert len(pg["tables"]) == 1 and pg["tables"][0]["rows"][1] == ["брусника", "начало цветения", "16.06"]
    assert pg["tables"][0]["title"].startswith("Таблица 1")
    assert "| Вид | Фенофаза | Дата |" in pg["rich"]


def test_xlsx_and_csv(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Наблюдения"
    ws.append(["Вид", "Дата", "Число"])
    for i in range(130):
        ws.append(["лебедь", "13.03.2020", i])
    p = tmp_path / "a.xlsx"
    wb.save(str(p))
    pages = office.read_pages(p, "xlsx")
    assert len(pages) == 2 and pages[0]["tables"][0]["rows"][0] == ["Вид", "Дата", "Число"] and len(pages[0]["tables"][0]["rows"]) == 131
    assert pages[1]["tables"] == [] and "Вид" in pages[1]["text"]
    c = tmp_path / "a.csv"
    c.write_bytes("Вид;Дата\nбелка;01.02.2020\n".encode("cp1251"))
    pg = office.read_pages(c, "csv")
    assert pg[0]["tables"][0]["rows"][1] == ["белка", "01.02.2020"]
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    assert office.validate(bad, "docx")[0] is False
    assert office.kind_of("x.DOCX") == "docx" and office.kind_of("x.doc") is None
