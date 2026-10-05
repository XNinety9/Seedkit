import json
import logging
import time
from dataclasses import asdict

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from seedkit.db import FileSignature, KeyValue, Snapshot, Torrent
from seedkit.qbit import TorrentData

log = logging.getLogger(__name__)


def store(session: Session, torrents: list[TorrentData], now: int | None = None) -> None:
    """Upsert current torrent states and record one snapshot per torrent."""
    now = now or int(time.time())
    known = {t.hash: t for t in session.scalars(select(Torrent))}

    for data in torrents:
        fields = asdict(data) | {"unregistered": data.unregistered, "last_seen": now, "removed_at": None}
        row = known.get(data.hash)
        if row is None:
            session.add(Torrent(**fields, first_seen=now))
        else:
            for key, value in fields.items():
                setattr(row, key, value)
    # Snapshots reference torrents: insert new torrents first.
    session.flush()
    session.add_all(
        Snapshot(
            ts=now,
            hash=data.hash,
            uploaded=data.uploaded,
            downloaded=data.downloaded,
            seeding_time=data.seeding_time,
        )
        for data in torrents
    )

    current = {t.hash for t in torrents}
    gone = [h for h, row in known.items() if h not in current and row.removed_at is None]
    if gone:
        session.execute(update(Torrent).where(Torrent.hash.in_(gone)).values(removed_at=now))
    session.commit()
    log.info("Collected %d torrents (%d removed since last run)", len(torrents), len(gone))


def store_signatures(session: Session, signatures: dict[str, tuple[str, int]]) -> None:
    for h, (signature, count) in signatures.items():
        session.merge(FileSignature(hash=h, signature=signature, file_count=count))
    session.commit()


def missing_signatures(session: Session) -> list[str]:
    known = set(session.scalars(select(FileSignature.hash)))
    active = session.scalars(select(Torrent.hash).where(Torrent.removed_at.is_(None), Torrent.amount_left == 0))
    return [h for h in active if h not in known]


def store_free_space(session: Session, free: int | None, now: int | None = None) -> None:
    if free is None:
        return
    session.merge(KeyValue(key="disk.free", value=json.dumps(free), ts=now or int(time.time())))
    session.commit()


def free_space(session: Session) -> int | None:
    row = session.get(KeyValue, "disk.free")
    return json.loads(row.value) if row else None
