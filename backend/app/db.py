"""SQLite models and sessions. Schema: docs/database.md."""

import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

from . import config


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
    status: Mapped[str] = mapped_column(String, default="active")  # active | failing (3+ failed checks in a row)
    current_version_id: Mapped[int | None] = mapped_column(ForeignKey("site_versions.id", use_alter=True))
    homepage_url: Mapped[str | None] = mapped_column(String)
    homepage_etag: Mapped[str | None] = mapped_column(String)
    homepage_last_modified: Mapped[str | None] = mapped_column(String)
    scope_path: Mapped[str] = mapped_column(String, default="/")
    summary: Mapped[str | None] = mapped_column(Text)
    # Sitemaps found in the last crawl: [{"url", "etag", "last_modified"}], re-checked by the monitor.
    sitemaps: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_checked_at: Mapped[datetime | None]
    last_crawled_at: Mapped[datetime | None]
    last_changed_at: Mapped[datetime | None]
    next_check_at: Mapped[datetime] = mapped_column(default=utcnow)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    current_version: Mapped["SiteVersion | None"] = relationship(foreign_keys=[current_version_id], post_update=True)
    pages: Mapped[list["PageRow"]] = relationship(back_populates="site", cascade="all, delete-orphan")

    __table_args__ = (Index("idx_sites_next_check", "next_check_at"),)


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
    md_url: Mapped[str | None] = mapped_column(String)  # markdown version of the page, if found
    section: Mapped[str] = mapped_column(String)
    score: Mapped[float] = mapped_column(Float)
    etag: Mapped[str | None] = mapped_column(String)
    last_modified: Mapped[str | None] = mapped_column(String)

    site: Mapped[Site] = relationship(back_populates="pages")

    __table_args__ = (UniqueConstraint("site_id", "url"),)


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
    _engine = make_engine(url or config.DATABASE_URL)
    Base.metadata.create_all(_engine)
    # pages.alt_descriptions was removed. Databases made before that still have it (NOT NULL), so drop it.
    if "alt_descriptions" in {c["name"] for c in inspect(_engine).get_columns("pages")}:
        with _engine.begin() as conn:
            conn.execute(text("ALTER TABLE pages DROP COLUMN alt_descriptions"))
    SessionLocal.configure(bind=_engine)
    return _engine
