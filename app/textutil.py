from __future__ import annotations

import re
from functools import lru_cache

import pymorphy3

_morph = pymorphy3.MorphAnalyzer()
_WORD = re.compile(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*|[A-Za-z]+")


def norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[^\w\s\-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=200_000)
def lemma(word: str) -> str:
    w = word.lower().replace("ё", "е")
    if not re.match(r"[а-я]", w):
        return w
    out = []
    for p in w.split("-"):
        try:
            out.append(_morph.parse(p)[0].normal_form.replace("ё", "е"))
        except Exception:
            out.append(p)
    return "-".join(out)


@lru_cache(maxsize=50_000)
def is_known(word: str) -> bool:
    try:
        return bool(_morph.parse(word)[0].is_known)
    except Exception:
        return False


def tokens(s: str) -> list[str]:
    return _WORD.findall(s or "")


def lemmas(s: str) -> list[str]:
    return [lemma(t) for t in tokens(s)]


def lemma_key(s: str) -> str:
    return " ".join(lemmas(s))


def search_blob(*parts: str) -> str:
    raw = " ".join(p for p in parts if p)
    return (norm(raw) + " | " + " ".join(lemmas(raw))).strip()


def clean_cell(s: str) -> str:
    s = (s or "").replace("\xad", "")
    s = re.sub(r"-\s*\n\s*", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()
