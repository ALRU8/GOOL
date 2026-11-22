import datetime as dt

from app.pipeline.calendar import parse_calendar_page
from app.pipeline.dates import parse_date
from app.pipeline.phases import canon_phases
from app.pipeline.sections import authors_of_text, find_headings, names_in
from app.pipeline.species import latin_norm
from app.textutil import lemma_key

CAL = """46
6. КАЛЕНДАРЬ ПРИРОДЫ 2020 года
Основные сезонные процессы,
Дата
1
2
3
4
5
6
ФЕНОЛОГИЧЕСКАЯ ЗИМА
Предзимье
Переход минимальных температур ниже 0С
11.10.19
10.10
14.09.93
29.10.07
26
Первый ледостав на р. Паз
28.10.19
04.11
05.10.01
27.11.18
12
ФЕНОЛОГИЧЕСКАЯ ВЕСНА
Первовесенье
Прилет гусей
20.04
30.04
09.04.03
24.05.04
19
Начало цветения березы пушистой (появление се-
режек)
06.06
04.06
11.05.03
21.06.96
27
Явление, не наблюдавшееся
—
10.05
21.04.11
17.06.09
20
"""


def test_fuzzy_dates():
    from app.pipeline.dates import parse_fuzzy
    r = parse_fuzzy("конец октября", 1994)
    assert (r["date"], r["date_end"], r["precision"]) == (dt.date(1994, 10, 21), dt.date(1994, 10, 31), "decade")
    r = parse_fuzzy("середина - конец мая", 1995)
    assert (r["date"], r["date_end"], r["precision"]) == (dt.date(1995, 5, 11), dt.date(1995, 5, 31), "range")
    r = parse_fuzzy("лето 1993 года")
    assert (r["date"], r["date_end"], r["precision"]) == (dt.date(1993, 6, 1), dt.date(1993, 8, 31), "season")
    r = parse_fuzzy("в июне 1992")
    assert (r["precision"], r["date"], r["date_end"]) == ("month", dt.date(1992, 6, 1), dt.date(1992, 6, 30))
    r = parse_fuzzy("21.01-11.03", 1996)
    assert (r["date"], r["date_end"], r["precision"]) == (dt.date(1996, 1, 21), dt.date(1996, 3, 11), "range")
    r = parse_fuzzy("3-4 июня", 1996)
    assert (r["date"], r["date_end"], r["precision"]) == (dt.date(1996, 6, 3), dt.date(1996, 6, 4), "range")
    r = parse_fuzzy("в 1994 году")
    assert (r["precision"], r["date"]) == ("year", dt.date(1994, 1, 1))
    assert parse_fuzzy("первой декаде июня", 1996)["precision"] == "decade"
    assert parse_fuzzy("неизвестно", 1996)["flags"] == ["year_inferred", "unparsed_date"] or "unparsed_date" in parse_fuzzy("неизвестно", 1996)["flags"]
    assert parse_fuzzy("12.05.2005")["precision"] == "day"


def test_dates():
    assert parse_date("12.05.2005")[0] == dt.date(2005, 5, 12)
    assert parse_date("11.10.19")[0] == dt.date(2019, 10, 11)
    d, fl = parse_date("12.05", 1996)
    assert d == dt.date(1996, 5, 12) and "year_inferred" in fl
    assert parse_date("3 июня", 1996)[0] == dt.date(1996, 6, 3)
    assert "bad_date" in parse_date("31.02.2000")[1]
    assert parse_date("—") == (None, [])
    assert parse_date("в конце октября", 1994)[0] == dt.date(1994, 10, 21)


def test_calendar_parser():
    recs, season, sub, header = parse_calendar_page(CAL, 2020)
    assert header is True
    assert [r["event"] for r in recs] == [
        "Переход минимальных температур ниже 0С", "Первый ледостав на р. Паз", "Прилет гусей",
        "Начало цветения березы пушистой (появление сережек)", "Явление, не наблюдавшееся"]
    assert recs[0]["date"] == dt.date(2019, 10, 11)
    assert recs[2]["date"] == dt.date(2020, 4, 20)
    assert recs[0]["extra"]["years_observed"] == 26 and recs[0]["extra"]["earliest"] == "1993-09-14"
    assert recs[2]["extra"]["season"].lower().startswith("фенологическая весна")
    assert recs[4]["date"] is None and recs[4]["extra"]["observed_in_year"] is False


def test_lemma_matching():
    assert lemma_key("березы пушистой") == lemma_key("Берёза пушистая")
    assert latin_norm("Betula pubescens Ehrh.") == "betula pubescens"
    assert latin_norm("Vaccinium vitis-idaea L.") == "vaccinium vitis-idaea"
    assert latin_norm("Betula sp.") == "betula"


def test_canon_phases():
    assert canon_phases("Зацветание купальницы европейской")[0] == "начало цветения"
    assert canon_phases("Начало цветения черники")[0] == "начало цветения"
    assert canon_phases("Прилет журавлей")[0] == "прилёт / первая песня"
    assert canon_phases("Первый ледостав на р. Паз")[0] == "ледостав"


def test_names_and_headings():
    assert names_in("наблюдения вела Крюковой С.А. и Воробьева Н.Г.") == ["Крюкова С. А.", "Воробьева Н. Г."]
    assert names_in("Окончательная корректура") == []
    assert "Поликарпова Н. В." in authors_of_text("Составитель: Поликарпова Н.В.\n текст")
    assert find_headings("8.4 Птицы\nобычная строка") == ["8.4. Птицы"]
    assert find_headings("VIII. ФАУНА И ЖИВОТНЫЙ МИР") == ["VIII. ФАУНА И ЖИВОТНЫЙ МИР"]


AI_ANSWER = """[ТАБЛИЦА] Таблица 5. Снегомерная съемка на постоянных площадях в течение зимы 1992/93 гг.
| Месяц, число | п. Янискоски / высота снега | п. Никель / высота снега |
|---|---|---|
| 10.20 | 5 | 11 |
| 11.20 | 24 | 24 |
Примечание: данные по двум постам.

[РИСУНОК] Рисунок 3. Динамика численности белки в 1992-1995 гг.
Тип: график
Описание: по оси X годы, по оси Y число особей на 10 км маршрута.
Данные:
| Год | Значение |
|---|---|
| 1992 | 12 |
| 1993 | 9 |
"""


def test_parse_ai_blocks():
    from app.pipeline.elements import is_empty_answer, parse_blocks, rows_to_markdown
    tables, figures = parse_blocks(AI_ANSWER)
    assert len(tables) == 1 and len(figures) == 1
    t = tables[0]
    assert t["title"].startswith("Таблица 5") and t["kind"] == "weather"
    assert len(t["rows"]) == 3 and t["rows"][0][1] == "п. Янискоски / высота снега" and t["rows"][2] == ["11.20", "24", "24"]
    assert figures[0]["caption"].startswith("Рисунок 3") and figures[0]["has_data"] is True and "график" in figures[0]["description"]
    assert is_empty_answer("НЕТ ЭЛЕМЕНТОВ") and is_empty_answer("")
    assert parse_blocks("НЕТ ЭЛЕМЕНТОВ") == ([], [])
    assert rows_to_markdown([["a", "b"], ["1", "2"]]).count("|") >= 8


def test_scan_helpers():
    from app.pipeline.scan import continues_table, graph_captions, looks_tabular, text_quality
    ok, _ = text_quality("В течение зимы неоднократно метели с сильными ветрами заносы на дорогах. Наибольшая продолжительность метели была на севере заповедника. " * 3)
    assert ok
    bad, why = text_quality("Ъ1ф лжм ртнкв щшцк ъыьэ ждлорп нгшщз хъфыв апрол джэяч смить бююб " * 4)
    assert not bad and why
    assert not text_quality("ab")[0]
    nums = "\n".join(f"{i}.{i % 12 + 1:02d}\n{i * 3}\n0.{i}" for i in range(1, 12))
    assert looks_tabular("Таблица 5\n" + nums)
    assert continues_table(nums) and not continues_table("Таблица 6\n" + nums)
    assert graph_captions("текст\nРисунок 3. Динамика численности белки\nещё") and not graph_captions("Рисунок 7.16 – Диапенсия лапландская")


def test_multi_year_and_dedupe():
    from app.pipeline.dates import parse_fuzzy
    r = parse_fuzzy("15.04.1902 - 23.04.1901", 1992)
    assert r["date"] is None and "multi_year_dates" in r["flags"]
    r = parse_fuzzy("весна 1991-1995")
    assert (r["date"], r["date_end"]) == (dt.date(1991, 1, 1), dt.date(1995, 12, 31)) and "multi_year_dates" in r["flags"]
    from app.models import Record
    from app.pipeline.build import dedupe
    a = Record(species_text="Желна", event="весенний прилёт", date=None, confidence=0.5, toponym_text="")
    b = Record(species_text="желна", event="весенний прилет", date=None, confidence=0.7, toponym_text="")
    c = Record(species_text="щур", event="весенний прилёт", date=None, confidence=0.6, toponym_text="")
    out = dedupe([a, b, c])
    assert len(out) == 2 and out[0] is b


def test_json_salvage_truncated():
    from app import yc
    cut = '[{"species_ru": "брусника", "event": "цветение", "date": "05.06"}, {"species_ru": "черника", "event": "цвет'
    got = yc.parse_json_block(cut)
    assert got == [{"species_ru": "брусника", "event": "цветение", "date": "05.06"}]
    assert yc.parse_json_block('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    try:
        yc.parse_json_block("совсем не json")
        assert False
    except ValueError:
        pass


def test_units_topics_headings():
    from app.pipeline.units import normalize_unit, topic_of
    assert normalize_unit("экз.") == "особей" and normalize_unit("особи") == "особей"
    assert normalize_unit("особей на 10 км") == normalize_unit("особей/10 км") == "особей/10 км"
    assert normalize_unit("кв. км") == "км²" and normalize_unit("г/м2") == "г/м²" and normalize_unit("") == ""
    assert topic_of("8.4. Птицы") == "Птицы" and topic_of("IV. ПОГОДА") == "Погода и климат"
    assert topic_of("1. Четырехгодовиков в сборах не было.") == "Другое"
    from app.pipeline.sections import find_headings
    assert find_headings("1. Четырехгодовиков в сборах не было.") == []
    assert find_headings("7.3 Сезонная динамика растительных сообществ") == ["7.3. Сезонная динамика растительных сообществ"]
