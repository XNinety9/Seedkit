"""Downsample old snapshots: full resolution, then one per hour, then one per day."""

import logging
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from seedkit.config import Settings

log = logging.getLogger(__name__)


def _downsample(session: Session, older_than: int, bucket: int, newer_than: int = 0) -> int:
    # Keep the last snapshot of each (torrent, bucket); counters are cumulative so nothing is lost.
    result = session.execute(
        text(
            """
            DELETE FROM snapshots WHERE id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY hash, ts / :bucket ORDER BY ts DESC
                    ) AS rn
                    FROM snapshots WHERE ts < :older_than AND ts >= :newer_than
                ) WHERE rn > 1
            )
            """
        ),
        {"bucket": bucket, "older_than": older_than, "newer_than": newer_than},
    )
    return result.rowcount or 0


def compact(session: Session, settings: Settings, now: int | None = None) -> int:
    now = now or int(time.time())
    day = 86400
    full_until = now - settings.seedkit_retention_full_days * day
    hourly_until = now - settings.seedkit_retention_hourly_days * day
    removed = _downsample(session, full_until, 3600, newer_than=hourly_until)
    removed += _downsample(session, hourly_until, day)
    session.commit()
    if removed:
        log.info("Compacted %d old snapshots", removed)
    return removed
