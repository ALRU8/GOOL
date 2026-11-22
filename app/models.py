from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text as sql_text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(500), default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(10), default="pdf", server_default="pdf")
    stash: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    storage_key: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    stage: Mapped[str] = mapped_column(String(60), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    pages_total: Mapped[int] = mapped_column(Integer, default=0)
    pages_done: Mapped[int] = mapped_column(Integer, default=0)
    pages_ocr: Mapped[int] = mapped_column(Integer, default=0)
    pages_vision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    records_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    pages: Mapped[list[Page]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    printed_no: Mapped[str] = mapped_column(String(20), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    rich: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(10), default="text")
    section: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="new")
    note: Mapped[str] = mapped_column(String(300), default="")
    coverage: Mapped[str] = mapped_column(String(20), default="", server_default="")
    ai_vision: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    spread: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    n_records: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    n_tables: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    n_figures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    document: Mapped[Document] = relationship(back_populates="pages")
    __table_args__ = (UniqueConstraint("document_id", "page_no"),)


class Species(Base):
    __tablename__ = "species"
    id: Mapped[int] = mapped_column(primary_key=True)
    name_ru: Mapped[str] = mapped_column(String(200), index=True)
    name_lat: Mapped[str] = mapped_column(String(200), default="", index=True)
    genus: Mapped[str] = mapped_column(String(100), default="", index=True)
    group_name: Mapped[str] = mapped_column(String(100), default="", index=True)
    rank: Mapped[str] = mapped_column(String(20), default="species")
    source: Mapped[str] = mapped_column(String(20), default="seed")
    taxon_status: Mapped[str] = mapped_column(String(12), default="", server_default="")
    synonyms: Mapped[list[SpeciesSynonym]] = relationship(back_populates="species", cascade="all, delete-orphan")


class SpeciesSynonym(Base):
    __tablename__ = "species_synonyms"
    id: Mapped[int] = mapped_column(primary_key=True)
    species_id: Mapped[int] = mapped_column(ForeignKey("species.id", ondelete="CASCADE"), index=True)
    synonym: Mapped[str] = mapped_column(String(200))
    norm: Mapped[str] = mapped_column(String(200), index=True)
    species: Mapped[Species] = relationship(back_populates="synonyms")


class Toponym(Base):
    __tablename__ = "toponyms"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    norm: Mapped[str] = mapped_column(String(300), index=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    kind: Mapped[str] = mapped_column(String(50), default="")
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(20), default="seed")


class Record(Base):
    __tablename__ = "records"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    year: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="event", index=True)
    species_text: Mapped[str] = mapped_column(String(300), default="")
    species_lat_text: Mapped[str] = mapped_column(String(200), default="")
    species_norm: Mapped[str] = mapped_column(Text, default="", server_default="")
    species_id: Mapped[int | None] = mapped_column(ForeignKey("species.id", ondelete="SET NULL"), nullable=True, index=True)
    group_name: Mapped[str] = mapped_column(String(100), default="", index=True)
    event: Mapped[str] = mapped_column(String(500), default="", index=True)
    date: Mapped[dt.date | None] = mapped_column(Date, nullable=True, index=True)
    date_raw: Mapped[str] = mapped_column(String(100), default="")
    date_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    date_precision: Mapped[str] = mapped_column(String(10), default="", server_default="")
    doy: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    topic: Mapped[str] = mapped_column(String(200), default="", server_default="", index=True)
    methodology: Mapped[str] = mapped_column(String(300), default="", server_default="")
    methodology_norm: Mapped[str] = mapped_column(Text, default="", server_default="")
    toponym_text: Mapped[str] = mapped_column(String(300), default="")
    toponym_id: Mapped[int | None] = mapped_column(ForeignKey("toponyms.id", ondelete="SET NULL"), nullable=True, index=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    habitat: Mapped[str] = mapped_column(String(300), default="")
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_raw: Mapped[str] = mapped_column(String(100), default="")
    unit: Mapped[str] = mapped_column(String(80), default="", index=True)
    unit_raw: Mapped[str] = mapped_column(String(80), default="", server_default="")
    observer: Mapped[str] = mapped_column(String(300), default="", index=True)
    observer_source: Mapped[str] = mapped_column(String(10), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    user_note: Mapped[str] = mapped_column(Text, default="", server_default="")
    user_note_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    fragment: Mapped[str] = mapped_column(Text, default="")
    section: Mapped[str] = mapped_column(String(300), default="")
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    flags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    method: Mapped[str] = mapped_column(String(20), default="rule")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    search_text: Mapped[str] = mapped_column(Text, default="")
    event_norm: Mapped[str] = mapped_column(Text, default="")
    observer_norm: Mapped[str] = mapped_column(Text, default="")
    toponym_norm: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    document: Mapped[Document] = relationship()
    species: Mapped[Species | None] = relationship()
    toponym: Mapped[Toponym | None] = relationship()
    __table_args__ = (Index("ix_records_doc_page", "document_id", "page_no"),)


class Section(Base):
    __tablename__ = "sections"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    authors: Mapped[list[Any]] = mapped_column(JSON, default=list)
    authors_norm: Mapped[str] = mapped_column(Text, default="")


class SavedQuery(Base):
    __tablename__ = "saved_queries"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class LlmCache(Base):
    __tablename__ = "llm_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="llm")
    response: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class DocTable(Base):
    __tablename__ = "doc_tables"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500), default="")
    kind: Mapped[str] = mapped_column(String(20), default="other")
    source: Mapped[str] = mapped_column(String(10), default="geometry")
    n_rows: Mapped[int] = mapped_column(Integer, default=0)
    n_cols: Mapped[int] = mapped_column(Integer, default=0)
    rows: Mapped[list[Any]] = mapped_column(JSON, default=list)
    search_text: Mapped[str] = mapped_column(Text, default="")


class Figure(Base):
    __tablename__ = "figures"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    caption: Mapped[str] = mapped_column(String(600), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(10), default="caption")
    search_text: Mapped[str] = mapped_column(Text, default="")
