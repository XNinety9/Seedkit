from pathlib import Path

from sqlalchemy import BigInteger, Float, ForeignKey, Index, String, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Torrent(Base):
    """Latest known state of a torrent, refreshed on every collection."""

    __tablename__ = "torrents"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str]
    size: Mapped[int] = mapped_column(BigInteger)
    uploaded: Mapped[int] = mapped_column(BigInteger)
    downloaded: Mapped[int] = mapped_column(BigInteger)
    ratio: Mapped[float] = mapped_column(Float)
    state: Mapped[str]
    category: Mapped[str]
    tags: Mapped[str]
    added_on: Mapped[int]
    completion_on: Mapped[int]
    seeding_time: Mapped[int]
    last_activity: Mapped[int]
    num_complete: Mapped[int]
    num_incomplete: Mapped[int]
    amount_left: Mapped[int] = mapped_column(BigInteger)
    save_path: Mapped[str]
    content_path: Mapped[str]
    tracker: Mapped[str] = mapped_column(index=True)
    tracker_status: Mapped[int | None]
    tracker_msg: Mapped[str]
    unregistered: Mapped[bool]
    first_seen: Mapped[int]
    last_seen: Mapped[int]
    # Set when the torrent disappears from qBittorrent; history is kept.
    removed_at: Mapped[int | None] = mapped_column(index=True)


class Snapshot(Base):
    """Cumulative counters of a torrent at a collection time."""

    __tablename__ = "snapshots"
    __table_args__ = (Index("ix_snapshots_hash_ts", "hash", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[int] = mapped_column(index=True)
    hash: Mapped[str] = mapped_column(ForeignKey("torrents.hash"))
    uploaded: Mapped[int] = mapped_column(BigInteger)
    downloaded: Mapped[int] = mapped_column(BigInteger)
    seeding_time: Mapped[int]


class FileSignature(Base):
    """Fingerprint of a torrent's file list (relative names + sizes), to find the same content twice."""

    __tablename__ = "file_signatures"

    hash: Mapped[str] = mapped_column(String(64), ForeignKey("torrents.hash"), primary_key=True)
    signature: Mapped[str] = mapped_column(String(64), index=True)
    file_count: Mapped[int]


class AutoPending(Base):
    """Torrents matched by an automatic cleanup rule, waiting for their grace period to end."""

    __tablename__ = "auto_pending"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule: Mapped[str]
    first_seen: Mapped[int]
    last_seen: Mapped[int]
    notified: Mapped[bool] = mapped_column(default=False)


class TrackerAlias(Base):
    """Groups several announce domains under one tracker name (e.g. acme.org + tk.acme.net → Acme)."""

    __tablename__ = "tracker_aliases"

    domain: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(index=True)


class TrackerRule(Base):
    """Hit & Run requirements of a tracker, set by the user. Keyed by tracker name (alias or domain)."""

    __tablename__ = "tracker_rules"

    tracker: Mapped[str] = mapped_column(primary_key=True)
    min_seed_hours: Mapped[float | None]
    min_ratio: Mapped[float | None]
    # "any": either requirement is enough; "all": both are required.
    satisfy: Mapped[str] = mapped_column(default="any")
    notes: Mapped[str] = mapped_column(default="")


class ActionLog(Base):
    """Audit trail of every action seedkit performed (or simulated) on qBittorrent."""

    __tablename__ = "action_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[int] = mapped_column(index=True)
    action: Mapped[str]
    hash: Mapped[str | None]
    name: Mapped[str] = mapped_column(default="")
    detail: Mapped[str] = mapped_column(default="")


class KeyValue(Base):
    """Small persistent state: notification dedup, last scan results, etc."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
    ts: Mapped[int]


def make_engine(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    return engine


def make_sessionmaker(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)
