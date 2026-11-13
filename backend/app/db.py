"""SQLite models and sessions. Schema: docs/database.md."""

import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_site_id() -> str:
    return secrets.token_hex(4)


class Base(DeclarativeBase):
    pass


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_site_id)
    url: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active")  # active | paused | failing
    current_version_id: Mapped[int | None] = mapped_column(ForeignKey("site_versions.id", use_alter=True))
    homepage_url: Mapped[str | None] = mapped_column(String)
    homepage_etag: Mapped[str | None] = mapped_column(String)
    homepage_last_modified: Mapped[str | None] = mapped_column(String)
    scope_path: Mapped[str] = mapped_column(String, default="/")
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_checked_at: Mapped[datetime | None]
    last_crawled_at: Mapped[datetime | None]
    last_changed_at: Mapped[datetime | None]
    next_check_at: Mapped[datetime] = mapped_column(default=utcnow)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    current_version: Mapped["SiteVersion | None"] = relationship(foreign_keys=[current_version_id], post_update=True)
    pages: Mapped[list["PageRow"]] = relationship(back_populates="site", cascade="all, delete-orphan")
    sitemaps: Mapped[list["SitemapRow"]] = relationship(back_populates="site", cascade="all, delete-orphan")

    __table_args__ = (Index("idx_sites_next_check", "status", "next_check_at"),)


class SiteVersion(Base):
    __tablename__ = "site_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    llms_txt: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String)
    page_count: Mapped[int] = mapped_column(Integer)
    change_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    __table_args__ = (Index("idx_versions_site", "site_id", "created_at"),)


class PageRow(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    alt_descriptions: Mapped[list] = mapped_column(JSON, default=list)
    md_url: Mapped[str | None] = mapped_column(String)
    section: Mapped[str] = mapped_column(String)
    score: Mapped[float] = mapped_column(Float)
    selected: Mapped[bool] = mapped_column(default=True)
    fingerprint: Mapped[str] = mapped_column(String)
    etag: Mapped[str | None] = mapped_column(String)
    last_modified: Mapped[str | None] = mapped_column(String)
    http_status: Mapped[int | None] = mapped_column(Integer)
    last_fetched_at: Mapped[datetime] = mapped_column(default=utcnow)

    site: Mapped[Site] = relationship(back_populates="pages")

    __table_args__ = (
        UniqueConstraint("site_id", "url"),
        Index("idx_pages_selected", "site_id", "selected"),
    )


class SitemapRow(Base):
    __tablename__ = "sitemaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String)
    etag: Mapped[str | None] = mapped_column(String)
    last_modified: Mapped[str | None] = mapped_column(String)

    site: Mapped[Site] = relationship(back_populates="sitemaps")

    __table_args__ = (UniqueConstraint("site_id", "url"),)


class CheckRun(Base):
    __tablename__ = "check_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String)  # check | crawl
    trigger: Mapped[str] = mapped_column(String)  # initial | scheduled | change_detected | ttl | manual
    result: Mapped[str] = mapped_column(String)  # unchanged | changed | failed
    reason: Mapped[str | None] = mapped_column(Text)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]

    __table_args__ = (Index("idx_runs_site", "site_id", "started_at"),)


def make_engine(url: str):
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    # An in-memory database ("sqlite://", used by tests) must share one connection.
    pool = {"poolclass": StaticPool} if url == "sqlite://" else {}
    engine = create_engine(url, connect_args={"check_same_thread": False}, **pool)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


_engine = None
SessionLocal = sessionmaker(expire_on_commit=False)


def init_db(url: str | None = None):
    """Create the engine and tables. Called once at startup (and by tests with sqlite://)."""
    global _engine
    _engine = make_engine(url or get_settings().database_url)
    Base.metadata.create_all(_engine)
    SessionLocal.configure(bind=_engine)
    return _engine
